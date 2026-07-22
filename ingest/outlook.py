"""Outlook ingest layer via Microsoft Graph — read-only.

Two jobs, sharing one token:

1. `refresh()` — recent inbox mail that plausibly needs a reply, cached for the
   Tasks page. Ported from `fetchOutlookEmails` in the Roland Express server.
2. `iter_sent_to()` — a Sent Items lookup used by the prospect follow-up
   tracker to answer "did Craig ever email these people after the call?".
   Deliberately not cached: it is a point query against a set of addresses, and
   caching the whole sent folder would mean storing a copy of Craig's outbox.

**Never writes.** Draft creation was removed on 2026-07-22 (both here and in
Zapier Zap 273528709) in favour of tracking follow-ups rather than drafting them.

Auth note: the app registration still carries a consented `Mail.ReadWrite`
grant, so Azure returns it in the token response even though only `Mail.Read`
is requested. Removing it requires an Azure app-registration change. Nothing
here uses it.

⚠️ The Azure client secret expires **2026-09-27**. When it does, every call here
fails with `AADSTS7000222` and Outlook silently drops out of the dashboard.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from typing import Iterable, Iterator

import requests

from ingest._shared import company_matcher
from store import db

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPE = "https://graph.microsoft.com/Mail.Read offline_access"

# Matches the Roland server's window: mail older than this is not a live task.
INBOX_DAYS = 7
PAGE_SIZE = 50
MAX_PAGES = 10

INBOX_FIELDS = (
    "id,subject,from,receivedDateTime,isRead,importance,flag,bodyPreview,"
    "webLink,hasAttachments"
)
SENT_FIELDS = "id,subject,sentDateTime,toRecipients,ccRecipients,webLink"


class OutlookAuthError(RuntimeError):
    """Token refresh failed — expired refresh token, or expired client secret."""


class GraphClient:
    def __init__(self, client_id: str, tenant_id: str, client_secret: str, refresh_token: str):
        self._auth = {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
            "scope": SCOPE,
        }
        self._tenant = tenant_id
        self._token: str | None = None
        self._expires_at = 0.0
        self.session = requests.Session()

    def _access_token(self) -> str:
        # 60s of slack so a token can't expire mid-request.
        if self._token and time.time() < self._expires_at - 60:
            return self._token

        url = f"https://login.microsoftonline.com/{self._tenant}/oauth2/v2.0/token"
        resp = self.session.post(url, data=self._auth, timeout=30)
        data = resp.json()
        if "access_token" not in data:
            raise OutlookAuthError(
                f"{data.get('error')}: {(data.get('error_description') or '')[:200]}"
            )
        self._token = data["access_token"]
        self._expires_at = time.time() + int(data.get("expires_in", 3600))
        return self._token

    def _get(self, url: str, params: dict | None = None) -> dict:
        for attempt in range(3):
            resp = self.session.get(
                url,
                headers={"Authorization": f"Bearer {self._access_token()}"},
                params=params,
                timeout=30,
            )
            if resp.status_code == 429:
                wait = resp.headers.get("Retry-After")
                time.sleep(float(wait) if wait else 1.5 ** attempt)
                continue
            resp.raise_for_status()
            return resp.json()
        resp.raise_for_status()
        return {}

    def _paged(self, url: str, params: dict) -> Iterator[dict]:
        """Follow Graph's @odata.nextLink, which already carries its own params."""
        pages = 0
        next_url, next_params = url, params
        while next_url and pages < MAX_PAGES:
            data = self._get(next_url, next_params)
            yield from data.get("value", [])
            pages += 1
            next_url, next_params = data.get("@odata.nextLink"), None

    def iter_inbox(self, since_iso: str) -> Iterator[dict]:
        yield from self._paged(f"{GRAPH}/me/mailFolders/Inbox/messages", {
            "$top": PAGE_SIZE,
            "$select": INBOX_FIELDS,
            "$orderby": "receivedDateTime desc",
            "$filter": f"receivedDateTime ge {since_iso}",
        })

    def iter_sent(self, since_iso: str) -> Iterator[dict]:
        yield from self._paged(f"{GRAPH}/me/mailFolders/SentItems/messages", {
            "$top": PAGE_SIZE,
            "$select": SENT_FIELDS,
            "$orderby": "sentDateTime desc",
            "$filter": f"sentDateTime ge {since_iso}",
        })


def client_from_secrets(secrets) -> GraphClient:
    return GraphClient(
        secrets["MS_CLIENT_ID"],
        secrets["MS_TENANT_ID"],
        secrets["MS_CLIENT_SECRET"],
        secrets["MS_REFRESH_TOKEN"],
    )


def _recipients(message: dict) -> set[str]:
    """Every address a message went to, To and Cc, lowercased."""
    out = set()
    for field in ("toRecipients", "ccRecipients"):
        for entry in message.get(field) or []:
            address = ((entry or {}).get("emailAddress") or {}).get("address")
            if address:
                out.add(address.strip().lower())
    return out


# Outlook writes calendar responses into Sent Items with these prefixes. They
# are generated by clicking a button, not by writing to someone, so counting one
# as a follow-up is wrong — a meeting cancellation was matching as "followed up".
_CALENDAR_SUBJECT = re.compile(
    r"^\s*(canceled|cancelled|accepted|declined|tentative|updated|"
    r"invitation|fw:\s*(canceled|cancelled|accepted|declined))\s*:",
    re.IGNORECASE,
)

# Above this many recipients it is a broadcast, not a personal follow-up.
# Observed failure: a proposal blasted to a distribution list matched three
# unrelated prospect calls because one attendee happened to be on it.
MAX_PERSONAL_RECIPIENTS = 5


def is_personal_reply(message: dict) -> bool:
    """True when a sent message plausibly is a follow-up someone wrote."""
    if _CALENDAR_SUBJECT.match(message.get("subject") or ""):
        return False
    return len(_recipients(message)) <= MAX_PERSONAL_RECIPIENTS


def iter_sent_to(
    client: GraphClient, addresses: Iterable[str], since_iso: str
) -> Iterator[tuple[str, dict]]:
    """Yield (matched_address, message) for personal sent mail to any address.

    Filtering happens client-side. Graph cannot filter on a recipient collection
    without `$search`, which disables `$orderby` and caps results — one scan of a
    bounded window is both simpler and cheaper than N per-address searches.

    Calendar responses and broadcasts are excluded; see `is_personal_reply`.
    """
    wanted = {a.strip().lower() for a in addresses if a and a.strip()}
    if not wanted:
        return
    for message in client.iter_sent(since_iso):
        if not is_personal_reply(message):
            continue
        hit = wanted & _recipients(message)
        if hit:
            yield sorted(hit)[0], message


def refresh(secrets, progress=None) -> dict:
    """Pull recent inbox mail into the cache. Returns counts."""
    client = client_from_secrets(secrets)
    fetched_at = db.now_iso()
    since = datetime.now(timezone.utc) - timedelta(days=INBOX_DAYS)
    since_iso = since.isoformat().replace("+00:00", "Z")

    if progress:
        progress("Outlook: fetching recent mail…")

    matcher = company_matcher()
    rows: list[dict] = []
    try:
        for m in client.iter_inbox(since_iso):
            sender = (m.get("from") or {}).get("emailAddress") or {}
            subject = m.get("subject") or "(no subject)"
            rows.append({
                "id": m.get("id"),
                "subject": subject,
                "from_name": sender.get("name") or sender.get("address") or "Unknown",
                "from_email": (sender.get("address") or "").lower(),
                "received_at": m.get("receivedDateTime"),
                "is_unread": int(not m.get("isRead", False)),
                "is_flagged": int((m.get("flag") or {}).get("flagStatus") == "flagged"),
                "is_high_importance": int(m.get("importance") == "high"),
                "has_attachments": int(bool(m.get("hasAttachments"))),
                "body_preview": (m.get("bodyPreview") or "")[:300],
                "web_link": m.get("webLink") or "",
                "company": matcher.resolve([subject]),
                "fetched_at": fetched_at,
            })
    except OutlookAuthError as e:
        # Degrade like every other ingest: the dashboard shows no Outlook items
        # and says why, rather than failing the whole refresh.
        return {"outlook_messages": 0, "skipped": str(e)}

    db.upsert_outlook_messages(rows)
    pruned = db.prune_outlook_messages_before(since.isoformat())

    db.set_meta("last_outlook_refresh", fetched_at)
    return {"outlook_messages": len(rows), "pruned": pruned}


def priority_of(row) -> str:
    """Roland's mapping: an unread flagged/important mail outranks plain unread."""
    unread = bool(row["is_unread"])
    if unread and (row["is_flagged"] or row["is_high_importance"]):
        return "high"
    return "medium" if unread else "low"

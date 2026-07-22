"""Slack ingest layer — read-only.

Pulls messages addressed to Craig (`to:me`), keeps the ones that look like they
need a response, and writes them into the SQLite cache. Ported from the Roland
Express server (`fetchSlackItems` in `server.js`).

**This module never writes to Slack.** The Roland server also posted a thread
reply when a Slack-sourced task was completed; that was dropped deliberately as
part of moving off Slack as an output channel.

Note the API this uses (`search.messages`) requires a **user** token (`xoxp-`),
not a bot token — bot tokens cannot search. The Roland `.env` stores it under
`SLACK_BOT_TOKEN`, which is a misnomer.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from typing import Iterator

import requests

from classify import actionable
from ingest._shared import company_matcher
from store import db

BASE = "https://slack.com/api"

PAGE_SIZE = 100
MAX_PAGES = 5

# How much history to keep. Slack search returns a recent window; anything older
# than this is dropped from the cache on refresh.
RETENTION_DAYS = 30

# https://ezra.slack.com/archives/C123ABC/p1774563176048259
_PERMALINK = re.compile(r"/archives/([A-Z0-9]+)/p(\d+)")

# Channels whose names say nothing about a client.
_GENERIC_CHANNELS = re.compile(
    r"^(general|random|announcements|engineering|sales|marketing)$", re.IGNORECASE
)
_MPDM = re.compile(r"^mpdm-", re.IGNORECASE)
_BARE_ID = re.compile(r"^[UCW][A-Z0-9]{6,}$", re.IGNORECASE)
_CHANNEL_PREFIX = re.compile(
    r"^(client[-_]|deal[-_]|proj[-_]|project[-_]|acct[-_]|account[-_]|co[-_])",
    re.IGNORECASE,
)


class SlackClient:
    def __init__(self, token: str):
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}"})

    def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{BASE}/{path}"
        for attempt in range(3):
            resp = self.session.get(url, params=params, timeout=30)
            if resp.status_code == 429:
                wait = resp.headers.get("Retry-After")
                time.sleep(float(wait) if wait else 1.5 ** attempt)
                continue
            resp.raise_for_status()
            return resp.json()
        resp.raise_for_status()
        return {}

    def iter_mentions(self) -> Iterator[dict]:
        """Yield messages matching `to:me`, newest first, following pagination."""
        page = 1
        while page <= MAX_PAGES:
            data = self._get("search.messages", {
                "query": "to:me",
                "sort": "timestamp",
                "sort_dir": "desc",
                "count": PAGE_SIZE,
                "page": page,
            })
            if not data.get("ok"):
                # A missing scope or a bot token yields ok=false. Degrade to
                # "no Slack items" rather than failing the whole refresh, the
                # same way the HubSpot ingest handles a missing scope.
                raise SlackUnavailable(data.get("error") or "unknown error")

            messages = (data.get("messages") or {})
            yield from messages.get("matches", [])

            paging = messages.get("paging") or {}
            if page >= (paging.get("pages") or 1):
                return
            page += 1


class SlackUnavailable(RuntimeError):
    """search.messages refused the request (bad token or missing scope)."""


def _parse_permalink(permalink: str, fallback_channel: str, fallback_ts: str):
    """Return (channel_id, thread_ts) from a permalink, falling back to fields.

    Slack's `p1774563176048259` format is the timestamp with the dot removed;
    the API elsewhere expects `1774563176.048259`.
    """
    match = _PERMALINK.search(permalink or "")
    if not match:
        return fallback_channel, fallback_ts
    channel_id, raw_ts = match.group(1), match.group(2)
    return channel_id, f"{raw_ts[:10]}.{raw_ts[10:]}"


def company_from_channel(channel_name: str) -> str:
    """Best-effort company name from a channel like `client-acme-corp`.

    Returns "" for DMs, group DMs, bare IDs, and generic team channels.
    """
    if not channel_name or channel_name == "directmessage":
        return ""
    if _MPDM.match(channel_name) or _BARE_ID.match(channel_name):
        return ""
    if _GENERIC_CHANNELS.match(channel_name):
        return ""

    stripped = _CHANNEL_PREFIX.sub("", channel_name).replace("-", " ").replace("_", " ").strip()
    if len(stripped) < 2:
        return ""
    return stripped.title()


def _ts_to_iso(ts: str) -> str | None:
    """Slack epoch-seconds string ("1774563176.048259") → ISO 8601."""
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None


def refresh(token: str, progress=None) -> dict:
    """Pull actionable Slack mentions into the cache. Returns counts."""
    client = SlackClient(token)
    fetched_at = db.now_iso()
    matcher = company_matcher()

    if progress:
        progress("Slack: searching mentions…")

    cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).isoformat()

    rows: list[dict] = []
    seen = 0
    too_old = 0
    try:
        for m in client.iter_mentions():
            seen += 1
            cleaned = actionable.clean_text(m.get("text") or "")
            if not actionable.is_actionable(cleaned):
                continue

            permalink = m.get("permalink") or ""
            channel = m.get("channel") or {}
            channel_id, thread_ts = _parse_permalink(
                permalink, channel.get("id") or "", m.get("ts") or ""
            )

            # Results are newest-first, but skip rather than break: a single
            # undated message shouldn't truncate the rest of the page.
            posted_at = _ts_to_iso(thread_ts)
            if posted_at and posted_at < cutoff:
                too_old += 1
                continue

            # Slack reports a DM's "channel name" as the other party's user ID.
            # Blank it so the UI can render "Direct message" instead of #U09ABC.
            channel_name = channel.get("name") or ""
            if _BARE_ID.match(channel_name) or channel_name == "directmessage":
                channel_name = ""

            # Channel name first — an explicit client channel is stronger
            # evidence than a company mentioned in passing in the text.
            company = company_from_channel(channel_name) or matcher.resolve([cleaned])

            rows.append({
                "id": f"{channel_id}:{thread_ts}",
                "headline": actionable.headline(cleaned)[:120],
                "raw_text": cleaned,
                "channel_id": channel_id,
                "channel_name": channel_name,
                "thread_ts": thread_ts,
                "author": m.get("username") or "",
                "permalink": permalink,
                "company": company or None,
                "posted_at": posted_at,
                "fetched_at": fetched_at,
            })
    except SlackUnavailable as e:
        return {"slack_items": 0, "skipped": str(e)}

    db.upsert_slack_items(rows)

    # Ages out anything that fell outside the window since the last refresh.
    # Fresh results are already filtered above, so this only trims the cache.
    pruned = db.prune_slack_items_before(cutoff)

    db.set_meta("last_slack_refresh", fetched_at)
    return {
        "slack_items": len(rows),
        "scanned": seen,
        "not_actionable": seen - len(rows) - too_old,
        "too_old": too_old,
        "pruned": pruned,
    }

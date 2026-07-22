"""Fathom ingest — pull calls, classify prospect vs not, store for follow-up tracking.

Replaces the Outlook draft generation removed from both the Roland agent and
Zapier Zap 273528709 on 2026-07-22. Craig rarely used the drafts, so the
dashboard tracks whether a follow-up actually went out instead of writing one.

**The classifier prompt is reconstructed from the documented v37 rules in the
Fathom Monitor notes, not copied byte-for-byte from the Zap.** Those rules are
the product of ~15 prompt revisions and 30 documented test calls, so they are
worth following closely — but the wording here is not identical to what the Zap
sends, and the two can drift. If classification quality matters more than that,
copy the live prompt out of the Zap's Claude step and paste it over `_RULES`.

Two things the Roland server's heuristic classifier got wrong that this fixes:
it keyed on the title (so "Post Project Report" and "Wealthbox setup for Ezra
Group" — both real prospects — were missed), and it treated any known-company
domain as an existing client (so a vendor demo from a company already in HubSpot
was never a prospect).
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Iterator

import anthropic
import requests

from classify import companies as companies_cls
from store import db

BASE = "https://api.fathom.ai/external/v1"

# Attendees who are Ezra Group or work for it — never the party to follow up with.
# Shares `classify.companies.EXCLUDED_COMPANY_DOMAINS` with the dashboard so the
# two can't disagree about who counts as external.
#
# This matters more here than anywhere else: a collaborator who sits in on client
# calls appears as an "external attendee" on every one of them, and an ordinary
# email to that person then reads as a follow-up to whichever prospect call they
# happened to attend. Observed with jean_s@doverfr.com and anand@pulse360.com —
# an unrelated "Envestnet GTM Proposal" mail to Jean marked both an LPL call and
# an NM RFP call as followed up. Add a collaborator's domain here and the
# problem disappears; leave it out and their calls silently look handled.
INTERNAL_DOMAINS = set(companies_cls.EXCLUDED_COMPANY_DOMAINS)

# How far back to look on a first run, so the tracker opens with real history
# rather than an empty table.
BACKFILL_DAYS = 90

MODEL = "claude-opus-4-8"

# Transcripts run long; this keeps a single classification bounded. The decision
# is almost always evident early, and the tail of a call is usually scheduling.
MAX_TRANSCRIPT_CHARS = 40_000

MAX_PAGES = 50

_RULES = """\
You classify recorded calls for Ezra Group, a wealth management technology
consulting firm run by Craig Iskowitz. Decide whether a call is a PROSPECT
conversation — one where a follow-up email from Craig would be appropriate.

TRUE (is_prospect) when the call is any of:
- A potential new client: introductory or discovery conversation, pricing, proposals
- Ezra Group pitching or selling a product, service, or program (e.g. the WTIS program)
- Go-to-market, partnership, or business development discussion
- A referral partner: custodian reps, consultants, industry contacts, attorneys, accountants
- A former client exploring re-engagement, a new phase, or a new proposal
- A vendor, partner, or platform demoing their product TO Ezra Group — Craig wants
  these flagged as BD opportunities regardless of who is buying or selling

FALSE when the call is any of:
- A recurring internal meeting: weekly call, daily standup, team sync, 1:1 with a
  contractor or employee
- An internal financial or operations review: P&L, budget, forecasting
- Active project work with an existing client under contract: status updates,
  deliverable reviews, implementation touchpoints
- A podcast, media interview, conference or event planning, or a personal call
- A job interview or contractor interview

Important signals learned from past misclassifications:
- Meeting titles are unreliable. "Post Project Report" and "Wealthbox setup for
  Ezra Group" were both genuine prospects. Judge from the conversation, not the title.
- "Weekly" in the title is a strong FALSE signal.
- A non-obvious internal title ("follow up meeting", a parenthetical like
  "(Internal Project)") is FALSE when every attendee is internal.

When genuinely in doubt, lean TRUE — it is better to flag a possible prospect
than to miss one.

Also return the company name the call is with. Use the external attendees'
organisation, not Ezra Group. If you cannot determine it, return an empty string.
"""

_SCHEMA = {
    "type": "object",
    "properties": {
        "is_prospect": {"type": "boolean"},
        "company_name": {"type": "string"},
        "reason": {
            "type": "string",
            "description": "One sentence explaining the decision.",
        },
    },
    "required": ["is_prospect", "company_name", "reason"],
    "additionalProperties": False,
}


class FathomClient:
    def __init__(self, api_key: str):
        self.session = requests.Session()
        # Fathom uses x-api-key, not a bearer token.
        self.session.headers.update({"X-Api-Key": api_key})

    def _get(self, path: str, params: dict | None = None) -> dict:
        for attempt in range(3):
            resp = self.session.get(f"{BASE}{path}", params=params, timeout=60)
            if resp.status_code == 429:
                wait = resp.headers.get("Retry-After")
                time.sleep(float(wait) if wait else 1.5 ** attempt)
                continue
            resp.raise_for_status()
            return resp.json()
        resp.raise_for_status()
        return {}

    def iter_meetings(self, since_iso: str) -> Iterator[dict]:
        """Yield meetings created after `since_iso`, following cursor pagination.

        Note the response key is `items` and the cursor is `next_cursor` — not
        the `meetings`/`next` shape the endpoint name suggests.
        """
        cursor = None
        pages = 0
        while pages < MAX_PAGES:
            params = {"created_after": since_iso, "include_transcript": "true"}
            if cursor:
                params["cursor"] = cursor
            data = self._get("/meetings", params)
            yield from data.get("items", [])
            pages += 1
            cursor = data.get("next_cursor")
            if not cursor:
                return
        raise RuntimeError(f"Fathom pagination exceeded {MAX_PAGES} pages")


def _fingerprint(meeting: dict) -> tuple:
    """Identify the underlying meeting, independent of which copy Fathom kept.

    Fathom records the host's and the team's copy of the same call as separate
    recordings with different `recording_id`s. Classifying both wastes a Claude
    call and — worse — can return opposite verdicts on the same conversation,
    because these are judgement calls and the two transcripts differ slightly.
    Craig's Fathom Monitor notes flag the duplication; for archiving transcripts
    it was cosmetic, for a follow-up tracker it means chasing a call twice.

    Keyed on **date + external attendees**, deliberately ignoring the title:
    the copies are titled differently ("NM | SOW Phase 2A…" vs "Ezra Group SOW
    Phase 2A…"), so any title-sensitive key fails to collapse exactly the cases
    that motivated this. The same outside people on the same day is a strong
    enough signal on its own; merging two genuinely distinct meetings with an
    identical guest list on one day is possible but rarer, and far less harmful
    than surfacing the same follow-up twice with conflicting verdicts.

    Internal calls have no external attendees, so an attendee-only key would
    collapse every internal meeting in a day into one. Those fall back to the
    title, which is fine — they are never prospects anyway.
    """
    started = meeting.get("recording_start_time") or meeting.get("created_at") or ""
    day = started[:10]

    emails = frozenset(a["email"] for a in _external_attendees(meeting))
    if emails:
        return (day, emails)

    title = (meeting.get("meeting_title") or meeting.get("title") or "").lower()
    return (day, frozenset(w for w in re.findall(r"[a-z0-9]+", title) if len(w) > 2))


def _pick_canonical(group: list[dict]) -> dict:
    """Of several recordings of one meeting, keep the longest transcript."""
    return max(group, key=lambda m: len(_transcript_text(m)))


def _external_attendees(meeting: dict) -> list[dict]:
    """Attendees who aren't Ezra Group, with their email addresses."""
    raw = (
        meeting.get("calendar_invitees")
        or meeting.get("participants")
        or meeting.get("attendees")
        or []
    )
    out = []
    for a in raw:
        email = (a.get("email") or a.get("email_address") or "").strip().lower()
        if not email or "@" not in email:
            continue
        if email.split("@")[-1] in INTERNAL_DOMAINS:
            continue
        out.append({"email": email, "name": a.get("name") or a.get("display_name") or ""})
    return out


def _transcript_text(meeting: dict) -> str:
    """Flatten Fathom's transcript into speaker-prefixed lines.

    Transcripts arrive as `[{speaker: {display_name}, text, timestamp}, ...]`.
    """
    segments = meeting.get("transcript") or []
    if isinstance(segments, str):
        return segments[:MAX_TRANSCRIPT_CHARS]

    lines = []
    for seg in segments:
        speaker = ((seg or {}).get("speaker") or {}).get("display_name") or "Unknown"
        text = (seg or {}).get("text") or ""
        if text:
            lines.append(f"{speaker}: {text}")
    return "\n".join(lines)[:MAX_TRANSCRIPT_CHARS]


def classify(client: anthropic.Anthropic, title: str, attendees: list[dict],
             transcript: str) -> dict | None:
    """Return {is_prospect, company_name, reason}, or None if unclassifiable."""
    if not transcript or len(transcript) < 100:
        # Too short to judge. Better to leave unclassified than to guess — an
        # unclassified call is visible as a gap; a wrong one silently misleads.
        return None

    attendee_list = ", ".join(
        f"{a['name']} <{a['email']}>" if a["name"] else a["email"] for a in attendees
    ) or "none recorded"

    response = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        thinking={"type": "adaptive"},
        output_config={
            "effort": "low",
            "format": {"type": "json_schema", "schema": _SCHEMA},
        },
        system=_RULES,
        messages=[{
            "role": "user",
            "content": (
                f"Meeting title: {title}\n"
                f"External attendees: {attendee_list}\n\n"
                f"Transcript:\n{transcript}"
            ),
        }],
    )

    if response.stop_reason == "refusal":
        return None
    for block in response.content:
        if block.type == "text":
            try:
                return json.loads(block.text)
            except json.JSONDecodeError:
                return None
    return None


def detect_followups(secrets, progress=None) -> dict:
    """Stamp prospect calls where a follow-up email has since gone out.

    Matching is by **recipient address, not thread** — Craig replying inside an
    existing thread counts, which is the common real-world case. First qualifying
    sent message wins; once stamped a call is never re-checked.

    Sent Items is scanned once for the whole open set rather than per call:
    Graph cannot `$filter` on a recipient collection without `$search`, which
    disables `$orderby`, so one bounded scan beats N per-address searches.
    """
    from ingest import outlook  # local import: only needed when MS Graph is set up

    with db.connect() as conn:
        open_calls = conn.execute(
            "SELECT recording_id, call_at, attendees_json FROM prospect_calls "
            "WHERE is_prospect = 1 AND followed_up_at IS NULL AND call_at IS NOT NULL"
        ).fetchall()

    if not open_calls:
        return {"checked": 0, "matched": 0}

    # Watch from the oldest open call, so a follow-up sent weeks ago is still found.
    oldest = min(r["call_at"] for r in open_calls)
    since_iso = oldest.replace("+00:00", "Z")

    wanted: dict[str, list[tuple[str, datetime]]] = {}
    for row in open_calls:
        call_at = _parse_iso(row["call_at"])
        if call_at is None:
            continue
        for att in json.loads(row["attendees_json"] or "[]"):
            wanted.setdefault(att["email"], []).append((row["recording_id"], call_at))

    if progress:
        progress(f"Outlook: scanning Sent Items for {len(wanted)} addresses…")

    client = outlook.client_from_secrets(secrets)
    matched = 0
    stamped: set[str] = set()
    try:
        for address, message in outlook.iter_sent_to(client, wanted.keys(), since_iso):
            sent_at = _parse_iso(message.get("sentDateTime"))
            if sent_at is None:
                continue
            for recording_id, call_at in wanted.get(address, []):
                # Only mail sent *after* the call counts as a follow-up.
                if recording_id in stamped or sent_at <= call_at:
                    continue
                db.mark_followed_up(
                    recording_id,
                    sent_at.isoformat(),
                    message.get("subject") or "(no subject)",
                    address,
                    message.get("webLink"),
                )
                stamped.add(recording_id)
                matched += 1
    except outlook.OutlookAuthError as e:
        return {"checked": len(open_calls), "matched": 0, "skipped": str(e)}

    db.set_meta("last_followup_scan", db.now_iso())
    return {"checked": len(open_calls), "matched": matched}


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def refresh(secrets, progress=None, *, days: int | None = None) -> dict:
    """Pull recent Fathom calls, classify unseen ones, store them."""
    fathom = FathomClient(secrets["FATHOM_API_KEY"])
    claude = anthropic.Anthropic(api_key=secrets["ANTHROPIC_API_KEY"])
    fetched_at = db.now_iso()

    window = days if days is not None else BACKFILL_DAYS
    since = (datetime.now(timezone.utc) - timedelta(days=window))
    since_iso = since.isoformat().replace("+00:00", "Z")

    already = db.classified_recording_ids()
    if progress:
        progress(f"Fathom: fetching calls since {since_iso[:10]}…")

    # Collapse duplicate recordings of the same meeting before classifying —
    # one Claude call per conversation, and one consistent verdict.
    groups: dict[tuple, list[dict]] = {}
    seen = 0
    for meeting in fathom.iter_meetings(since_iso):
        seen += 1
        groups.setdefault(_fingerprint(meeting), []).append(meeting)

    deduped = seen - len(groups)
    if progress and deduped:
        progress(f"Fathom: {seen} recordings collapsed to {len(groups)} meetings")

    rows: list[dict] = []
    skipped = unclassifiable = internal_only = 0
    for group in groups.values():
        meeting = _pick_canonical(group)
        recording_id = str(meeting.get("recording_id") or meeting.get("id") or "")
        if not recording_id:
            continue
        # Any copy already classified means the meeting is known.
        if any(
            str(m.get("recording_id") or m.get("id") or "") in already for m in group
        ):
            skipped += 1
            continue

        title = meeting.get("meeting_title") or meeting.get("title") or "Untitled"
        attendees = _external_attendees(meeting)

        # Structural gate, applied before the model sees anything: a call with
        # no external attendee cannot need a follow-up email — there is nobody
        # outside Ezra Group to send it to. The classifier alone got this wrong
        # (it flagged an internal Craig/Hannah standup as a prospect because the
        # two of them spent it discussing a live deal), and no amount of prompt
        # wording makes "we talked about selling" the same as "we talked to the
        # buyer". Also saves a Claude call on every internal meeting.
        if not attendees:
            internal_only += 1
            continue

        if progress:
            progress(f"Fathom: classifying “{title[:48]}”…")

        verdict = classify(claude, title, attendees, _transcript_text(meeting))
        if verdict is None:
            unclassifiable += 1
            continue

        rows.append({
            "recording_id": recording_id,
            "call_title": title,
            "call_at": meeting.get("recording_start_time") or meeting.get("created_at"),
            "company": (verdict.get("company_name") or "").strip() or None,
            "attendees_json": json.dumps(attendees),
            "fathom_url": meeting.get("url") or meeting.get("meeting_url"),
            "is_prospect": int(bool(verdict.get("is_prospect"))),
            "classification_reason": verdict.get("reason"),
            "classified_at": db.now_iso(),
            "followed_up_at": None,
            "follow_up_subject": None,
            "follow_up_to": None,
            "follow_up_url": None,
            "dismissed_at": None,
            "fetched_at": fetched_at,
        })

    db.upsert_prospect_calls(rows)
    db.set_meta("last_fathom_refresh", fetched_at)
    return {
        "recordings_seen": seen,
        "duplicates_collapsed": deduped,
        "classified": len(rows),
        "prospects": sum(r["is_prospect"] for r in rows),
        "already_known": skipped,
        "internal_only": internal_only,
        "unclassifiable": unclassifiable,
    }

"""Follow-ups page — prospect calls with no follow-up email sent.

Replaces the Outlook draft generation removed from Roland and Zapier Zap
273528709 on 2026-07-22. Craig rarely used the drafts, so this tracks whether
a follow-up actually went out rather than writing one he'd ignore.

A call clears when any personal email is sent to one of its external attendees
after the call — new thread or reply, since replying inside an existing thread
is the common case. Calendar responses and broadcasts don't count; see
`ingest.outlook.is_personal_reply`.

Ages in **business days**: a Friday call followed up Monday is fine.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from classify import followup
from store import db

st.set_page_config(page_title="Follow-ups — EG Marketing Dashboard", page_icon="📮", layout="wide")

if not st.session_state.get("authed"):
    st.warning("Sign in on the main page first.")
    st.stop()

LEVEL_ICON = {"overdue": "🔴", "due": "🟠", "ok": "🟢", "sent": "✅"}


def _parse(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@st.cache_data(ttl=600)
def load_calls() -> pd.DataFrame:
    db.init_db()
    with db.connect() as conn:
        df = pd.read_sql(
            "SELECT * FROM prospect_calls WHERE is_prospect = 1 AND dismissed_at IS NULL",
            conn,
        )
    if df.empty:
        return df

    rows = []
    for _, r in df.iterrows():
        call_at = _parse(r["call_at"])
        sent_at = _parse(r["followed_up_at"])
        state = followup.status(call_at, sent_at)
        attendees = json.loads(r["attendees_json"] or "[]")
        rows.append({
            "recording_id": r["recording_id"],
            "Call": r["call_title"],
            "Company": r["company"],
            "Date": call_at.date() if call_at else None,
            "Who": ", ".join(a["name"] or a["email"] for a in attendees) or "—",
            "Status": f"{LEVEL_ICON[state.level]} {state.label}",
            "Recording": r["fathom_url"],
            "_level": state.level,
            "_days": state.business_days,
            "_reason": r["classification_reason"],
            "_sent_subject": r["follow_up_subject"],
            "_sent_to": r["follow_up_to"],
        })
    return pd.DataFrame(rows)


st.title("📮 Prospect follow-ups")
st.caption(
    "Every prospect call, and whether a follow-up email went out afterwards. "
    "Clears automatically when you email any attendee after the call — nothing to tick off."
)

data = load_calls()
if data.empty:
    st.warning(
        "No prospect calls cached yet. Run a Fathom refresh from the Overview page."
    )
    st.stop()

awaiting = data[data["_level"] != "sent"].sort_values("_days", ascending=False)
sent = data[data["_level"] == "sent"].sort_values("Date", ascending=False)

n_overdue = int((data["_level"] == "overdue").sum())
n_due = int((data["_level"] == "due").sum())

c1, c2, c3, c4 = st.columns(4)
c1.metric("🔴 Overdue", n_overdue, help="More than 4 business days since the call.")
c2.metric("🟠 Due", n_due, help="2 to 4 business days since the call.")
c3.metric("Awaiting reply", len(awaiting))
c4.metric("Followed up", len(sent))

scan_age = db.age_hours("last_followup_scan")
if scan_age is None:
    st.warning("Sent Items has never been scanned — every call below will look unanswered.", icon="⚠️")
elif scan_age > db.STALE_AFTER_HOURS:
    st.warning(f"Sent Items last scanned {db.describe_age(scan_age)}.", icon="⚠️")

st.divider()

COLUMNS = ["Status", "Date", "Company", "Call", "Who", "Recording"]
COLUMN_CONFIG = {
    "Status": st.column_config.TextColumn("Status", width="medium"),
    "Call": st.column_config.TextColumn("Call", width="large"),
    "Recording": st.column_config.LinkColumn("Fathom", display_text="Listen ↗", width="small"),
}

st.subheader(f"Awaiting follow-up · {len(awaiting)}")
if awaiting.empty:
    st.success("Every prospect call has a follow-up. 🎉")
else:
    st.caption("Oldest first — the ones most likely to have gone cold.")
    st.dataframe(
        awaiting[COLUMNS], width="stretch", hide_index=True, column_config=COLUMN_CONFIG
    )

    with st.expander("Why were these flagged as prospect calls?"):
        st.caption(
            "One line per call from the classifier. If something here is not a "
            "prospect, that is a prompt-tuning signal — see `ingest/fathom.py`."
        )
        for _, r in awaiting.iterrows():
            st.markdown(f"**{r['Company'] or '?'} — {r['Call']}**  \n{r['_reason']}")

with st.expander(f"✅ Followed up · {len(sent)}", expanded=False):
    st.caption("The email that cleared each call, so a wrong match is visible rather than silent.")
    if sent.empty:
        st.caption("Nothing yet.")
    else:
        proof = sent.assign(
            **{"Email sent": sent["_sent_subject"], "To": sent["_sent_to"]}
        )
        st.dataframe(
            proof[["Date", "Company", "Call", "Email sent", "To"]],
            width="stretch",
            hide_index=True,
        )

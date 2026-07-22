"""Daily Briefing — the replacement for the Slack DM briefing.

Reproduces the sections of "Ezra Group — Daily Briefing" (which Paperclip's CEO
agent posted to Craig's Slack self-DM until 2026-07-15) from the local cache,
so it is always current rather than a once-a-day snapshot to scroll back for.

**What carried over and what didn't.** Overdue, due-today, pipeline and top
deals are all derived from cached data and are live here. Two sections are not:

* *Outbound touches* ("4/10 this week") — needs Sent Items classified into
  prospect / partner / client / conference, which the agent did with a model.
  The raw send count is here instead; it is honest but not the same number.
* *Campaign status* — a written assessment of Raymond James / IBD / LATAM
  against their deadlines. That was the agent reading email threads and
  judging progress; there is no cached equivalent.

Both are noted on the page rather than silently dropped, so the gap is visible.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import streamlit as st

from classify import followup, pipeline, priority
from store import db

st.set_page_config(page_title="Daily Briefing — EG", page_icon="🗞️", layout="wide")

if not st.session_state.get("authed"):
    st.warning("Sign in on the main page first.")
    st.stop()

HUBSPOT_PORTAL_ID = st.secrets.get("HUBSPOT_PORTAL_ID", "50726076")
HUBSPOT_BASE = "https://app.hubspot.com"
TOP_DEALS = 5


@st.cache_data(ttl=600)
def _table(name: str) -> pd.DataFrame:
    db.init_db()
    with db.connect() as conn:
        return pd.read_sql(f"SELECT * FROM {name}", conn)


def _as_date(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce", utc=True).dt.tz_localize(None).dt.date


def _is_date(value) -> bool:
    """True for a real date. `pd.NaT` is truthy, so a bare `if value` does not
    guard against a missing date — it slips through and raises on comparison."""
    return value is not None and pd.notna(value)


def _parse(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


today = date.today()
hour = datetime.now().hour
greeting = "Good morning" if hour < 12 else "Good afternoon" if hour < 17 else "Good evening"

st.title(f"🗞️ {greeting}, Craig")
st.caption(f"Ezra Group — {today:%A, %B %-d, %Y}")

# --- source freshness ------------------------------------------------------
stale = {
    label: db.age_hours(key)
    for label, key in (
        ("HubSpot", "last_full_refresh"), ("Asana", "last_asana_refresh"),
        ("Slack", "last_slack_refresh"), ("Outlook", "last_outlook_refresh"),
        ("Fathom", "last_fathom_refresh"),
    )
}
lagging = {k: v for k, v in stale.items() if v is None or v > db.STALE_AFTER_HOURS}
if lagging:
    st.warning(
        "Briefing is only as current as its sources — stale: "
        + ", ".join(f"**{k}** ({db.describe_age(v)})" for k, v in lagging.items()),
        icon="⚠️",
    )

# --- headline numbers ------------------------------------------------------
asana, hs_tasks, deals = _table("asana_tasks"), _table("tasks"), _table("deals")
active = pipeline.active_tasks(hs_tasks)
open_deals = pipeline.open_deals(deals)

def _overdue_and_today(df: pd.DataFrame, col: str) -> tuple[int, int]:
    if df.empty:
        return 0, 0
    d = _as_date(df[col])
    return int((d < today).sum()), int((d == today).sum())

a_over, a_today = _overdue_and_today(asana, "due_on")
h_over, h_today = _overdue_and_today(active, "due_at") if not active.empty else (0, 0)

prospect_calls = _table("prospect_calls")
awaiting = 0
if not prospect_calls.empty:
    live = prospect_calls[
        (prospect_calls["is_prospect"] == 1)
        & prospect_calls["followed_up_at"].isna()
        & prospect_calls["dismissed_at"].isna()
    ]
    awaiting = sum(
        followup.status(_parse(r["call_at"]), None).needs_attention
        for _, r in live.iterrows()
    )

pipeline_value = float(open_deals["amount"].fillna(0).sum()) if not open_deals.empty else 0.0

c1, c2, c3, c4 = st.columns(4)
c1.metric("Overdue", f"{a_over + h_over:,}", help="Asana + HubSpot tasks past due.")
c2.metric("Due today", f"{a_today + h_today:,}")
c3.metric("Follow-ups owed", f"{awaiting:,}", help="Prospect calls with no follow-up email after 2+ business days.")
c4.metric("Open pipeline", f"${pipeline_value:,.0f}", delta=f"{len(open_deals)} deals")

st.divider()

# --- follow-ups owed -------------------------------------------------------
st.subheader("📮 Follow-ups owed")
if not prospect_calls.empty and awaiting:
    rows = []
    for _, r in live.iterrows():
        state = followup.status(_parse(r["call_at"]), None)
        if not state.needs_attention:
            continue
        who = ", ".join(
            a["name"] or a["email"] for a in json.loads(r["attendees_json"] or "[]")
        )
        rows.append({
            "": "🔴" if state.level == "overdue" else "🟠",
            "Company": r["company"], "Call": r["call_title"],
            "Who": who, "Waiting": state.label, "Fathom": r["fathom_url"],
        })
    st.dataframe(
        pd.DataFrame(rows).sort_values("Waiting", ascending=False),
        width="stretch", hide_index=True,
        column_config={"Fathom": st.column_config.LinkColumn("", display_text="Listen ↗", width="small")},
    )
else:
    st.success("No prospect call is waiting on a follow-up.")

# --- overdue work ----------------------------------------------------------
st.subheader("⚠️ Overdue")
overdue_rows = []
if not asana.empty:
    for (_, r), d in zip(asana.iterrows(), _as_date(asana["due_on"])):
        if _is_date(d) and d < today:
            p = priority.compute(d, "medium")
            overdue_rows.append({"When": p.label, "Source": "Asana", "Task": r["name"],
                                 "Company": r["company"], "Link": r["url"], "_s": p.score})
if not active.empty:
    for (_, r), d in zip(active.iterrows(), _as_date(active["due_at"])):
        if _is_date(d) and d < today:
            p = priority.compute(d, r["priority"])
            overdue_rows.append({
                "When": p.label, "Source": "HubSpot", "Task": r["subject"], "Company": None,
                "Link": f"{HUBSPOT_BASE}/tasks/{HUBSPOT_PORTAL_ID}/view/all/task/{r['id']}",
                "_s": p.score})

if overdue_rows:
    od = pd.DataFrame(overdue_rows).sort_values("_s")
    st.caption(
        f"{len(od)} overdue. Showing the 12 least stale — the ones most likely still live. "
        "Full list on the **Tasks** page."
    )
    st.dataframe(
        od.head(12)[["When", "Source", "Task", "Company", "Link"]],
        width="stretch", hide_index=True,
        column_config={"Link": st.column_config.LinkColumn("", display_text="Open ↗", width="small")},
    )
else:
    st.success("Nothing overdue.")

# --- pipeline --------------------------------------------------------------
st.subheader("💰 Pipeline")
if open_deals.empty:
    st.info("No open deals cached.")
else:
    close = _as_date(open_deals["closedate"])
    week = today + timedelta(days=7)
    closing = open_deals[[_is_date(d) and today <= d <= week for d in close]]
    if not closing.empty:
        st.caption(f"**{len(closing)} closing in the next 7 days** — "
                   f"${closing['amount'].fillna(0).sum():,.0f} at stake.")

    top = open_deals.sort_values("amount", ascending=False).head(TOP_DEALS)
    st.dataframe(
        pd.DataFrame({
            "Deal": top["name"],
            "Amount": top["amount"],
            "Stage": top.get("stage_label"),
            "Close": _as_date(top["closedate"]),
            "Link": [f"{HUBSPOT_BASE}/contacts/{HUBSPOT_PORTAL_ID}/record/0-3/{i}" for i in top["id"]],
        }),
        width="stretch", hide_index=True,
        column_config={
            "Amount": st.column_config.NumberColumn(format="$%d"),
            "Link": st.column_config.LinkColumn("", display_text="Open ↗", width="small"),
        },
    )

# --- what the Slack version had that this doesn't --------------------------
with st.expander("Not carried over from the Slack briefing"):
    st.markdown(
        "**Outbound touch count** (*“4/10 this week”*) — the agent read Sent Items "
        "and classified each message as prospect / partner / client / conference. "
        "A raw send count is available but would not be the same number, so it is "
        "not shown rather than shown wrongly.\n\n"
        "**Campaign status** — the written assessment of Raymond James, IBD outreach "
        "and the LATAM Roundtable against their deadlines. That was the agent reading "
        "threads and judging progress; there is no cached equivalent.\n\n"
        "Both are reconstructible — they need a model pass over Sent Items, which is "
        "the same shape as the Fathom classifier in `ingest/fathom.py`."
    )

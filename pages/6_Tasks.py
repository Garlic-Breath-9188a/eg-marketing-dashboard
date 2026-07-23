"""Tasks page — every open item from every source, on one ranked list.

Replaces the Roland dashboard at localhost:3001. Sources:

  • Asana        — assigned, incomplete
  • HubSpot      — active tasks
  • HubSpot      — open deals (ranked by close date)
  • Slack        — messages addressed to Craig that look like asks
  • Outlook      — flagged or high-importance mail only

Outlook is filtered hard on purpose. The Roland server treated all recent inbox
mail as tasks, which meant its top "task" senders were asana.com, Cision,
LinkedIn and Substack — 187 items of which ~12 were real. Flag state is the only
reliable signal here, because it is the one Craig sets by hand. "Unread from a
known HubSpot contact" was measured and rejected: 3,783 contact domains include
every PR firm and newsletter sender ever imported, so it added 30 rows of pure
noise.

Everything is scored by `classify.priority` so items from different systems sort
against each other honestly. Lower score = needs attention sooner.

**Why this page is sectioned rather than one ranked list.** The Roland priority
algorithm assumes tasks carry due dates. In practice 68% of these do not, so
they all tie on score and a single sorted list degenerates into one 390-row pile
between a handful of dated items. Three sections instead:

  1. **Needs attention** — dated, and not yet abandoned. The real work list.
  2. **Unscheduled** — no date anywhere. Real work, but nothing is claiming it
     is due, so it should not crowd out things that are.
  3. **Stale** — 30+ days overdue. Almost always finished-but-not-closed or
     quietly dropped; they belong in a triage pile, not at the top of the list.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from classify import pipeline, priority
from classify.company_link import CompanyResolver
from ingest import outlook
from store import db

st.set_page_config(page_title="Tasks — EG Marketing Dashboard", page_icon="✅", layout="wide")

if not st.session_state.get("authed"):
    st.warning("Sign in on the main page first.")
    st.stop()

HUBSPOT_PORTAL_ID = st.secrets.get("HUBSPOT_PORTAL_ID", "50726076")
HUBSPOT_BASE = "https://app.hubspot.com"

@st.cache_data(ttl=3600)
def _table(name: str) -> pd.DataFrame:
    db.init_db()
    with db.connect() as conn:
        return pd.read_sql(f"SELECT * FROM {name}", conn)


def _as_date(series: pd.Series) -> pd.Series:
    """Parse to naive dates. Sources mix date-only, UTC ISO, and epoch strings."""
    return pd.to_datetime(series, errors="coerce", utc=True).dt.tz_localize(None).dt.date


def _rows_from_asana(df: pd.DataFrame) -> list[dict]:
    if df.empty:
        return []
    due = _as_date(df["due_on"])
    return [
        {
            "Source": "Asana",
            "Task": r["name"],
            "Company": r["company"],
            "Context": r["project"] or "",
            # Asana has no native priority field; the Node server assumed medium.
            "_priority": priority.compute(d, "medium"),
            "Link": r["url"],
        }
        for (_, r), d in zip(df.iterrows(), due)
    ]


def _rows_from_hubspot_tasks(df: pd.DataFrame, resolver: CompanyResolver) -> list[dict]:
    active = pipeline.active_tasks(df)
    if active.empty:
        return []
    due = _as_date(active["due_at"])
    return [
        {
            "Source": "HubSpot",
            "Task": r["subject"],
            "Company": resolver.for_task(r),
            "Context": (r["task_type"] or "").replace("_", " ").title(),
            "_priority": priority.compute(d, r["priority"]),
            "Link": f"{HUBSPOT_BASE}/tasks/{HUBSPOT_PORTAL_ID}/view/all/task/{r['id']}",
        }
        for (_, r), d in zip(active.iterrows(), due)
    ]


def _rows_from_deals(df: pd.DataFrame, resolver: CompanyResolver) -> list[dict]:
    open_ = pipeline.open_deals(df)
    if open_.empty:
        return []
    close = _as_date(open_["closedate"])
    rows = []
    for (_, r), d in zip(open_.iterrows(), close):
        amount = r.get("amount")
        rows.append({
            "Source": "Deal",
            "Task": r["name"],
            "Company": resolver.for_deal(r),
            "Context": f"${amount:,.0f}" if pd.notna(amount) else (r.get("stage_label") or ""),
            "_priority": priority.compute(d, None, is_deal=True),
            "Link": f"{HUBSPOT_BASE}/contacts/{HUBSPOT_PORTAL_ID}/record/0-3/{r['id']}",
        })
    return rows


def _rows_from_slack(df: pd.DataFrame) -> list[dict]:
    if df.empty:
        return []
    posted = pd.to_datetime(df["posted_at"], errors="coerce", utc=True)
    return [
        {
            "Source": "Slack",
            "Task": r["headline"],
            "Company": r["company"],
            # Slack messages carry no due date, so they all score as "no date".
            # Recency is the only ordering signal they have.
            "Context": f"#{r['channel_name']}" if r["channel_name"] else "Direct message",
            "_priority": priority.compute(None, "medium"),
            "_recency": p,
            "Link": r["permalink"],
        }
        for (_, r), p in zip(df.iterrows(), posted)
    ]


def _rows_from_outlook(df: pd.DataFrame) -> list[dict]:
    """Flagged / high-importance mail only — see the module docstring."""
    if df.empty:
        return []
    marked = df[(df["is_flagged"] == 1) | (df["is_high_importance"] == 1)]
    if marked.empty:
        return []
    received = pd.to_datetime(marked["received_at"], errors="coerce", utc=True)
    rows = []
    for (_, r), ts in zip(marked.iterrows(), received):
        # A flagged mail's received date stands in for a due date — it is the
        # day Craig decided it needed action. High-importance-only mail has no
        # such signal and stays undated.
        due = ts.date() if (r["is_flagged"] and pd.notna(ts)) else None
        rows.append({
            "Source": "Outlook",
            "Task": r["subject"],
            "Company": r["company"],
            "Context": f"From {r['from_name']}" + (" · 📎" if r["has_attachments"] else ""),
            "_priority": priority.compute(due, outlook.priority_of(r)),
            "_recency": ts,
            "Link": r["web_link"],
        })
    return rows


def load_unified() -> pd.DataFrame:
    rows: list[dict] = []
    rows += _rows_from_asana(_table("asana_tasks"))
    deals_df = _table("deals")
    resolver = CompanyResolver(_table("companies"), deals_df, _table("contacts"))
    rows += _rows_from_hubspot_tasks(_table("tasks"), resolver)
    rows += _rows_from_deals(deals_df, resolver)
    rows += _rows_from_slack(_table("slack_items"))
    rows += _rows_from_outlook(_table("outlook_messages"))
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["When"] = df["_priority"].apply(lambda p: p.label)
    df["Tier"] = df["_priority"].apply(lambda p: p.tier)
    df["Overdue"] = df["_priority"].apply(lambda p: p.is_overdue)
    df["_score"] = df["_priority"].apply(lambda p: p.score)
    if "_recency" not in df.columns:
        df["_recency"] = pd.NaT
    # Newest first within a score tie; items with no recency signal sort last.
    df["_recency"] = pd.to_datetime(df["_recency"], errors="coerce", utc=True)
    return df.drop(columns=["_priority"]).sort_values(
        ["_score", "_recency"], ascending=[True, False], na_position="last", kind="stable"
    )


# 30+ days past due. Kept out of the main list on the assumption that nothing
# survives a month overdue while still being live work.
STALE_TIER = "very-stale"


st.title("✅ Tasks")
st.caption(
    "Every open item across Asana, HubSpot tasks and deals, Slack, and flagged "
    "Outlook mail — scored on one scale so sources compare honestly."
)

data = load_unified()
if data.empty:
    st.warning("No tasks cached yet — refresh from the Overview page first.")
    st.stop()

# Every count below is only as current as the cache behind it. A stale cache
# keeps deleted HubSpot tasks on the page with links that 404.
_ages = {
    "HubSpot": db.age_hours("last_full_refresh"),
    "Asana": db.age_hours("last_asana_refresh"),
    "Slack": db.age_hours("last_slack_refresh"),
    "Outlook": db.age_hours("last_outlook_refresh"),
}
_stale = {k: v for k, v in _ages.items() if v is None or v > db.STALE_AFTER_HOURS}
if _stale:
    st.warning(
        "Stale sources: "
        + ", ".join(f"**{k}** ({db.describe_age(v)})" for k, v in _stale.items())
        + ". Refresh from the Overview page.",
        icon="⚠️",
    )

n_stale = int((data["Tier"] == STALE_TIER).sum())
n_undated = int((data["Tier"] == "none").sum())
n_live_overdue = int((data["Overdue"] & (data["Tier"] != STALE_TIER)).sum())
n_due_soon = int(data["Tier"].isin({"today", "soon", "upcoming"}).sum())

c1, c2, c3, c4 = st.columns(4)
c1.metric(
    "Due soon", f"{n_due_soon:,}",
    help="Due today, tomorrow, or within the next week.",
)
c2.metric(
    "Overdue", f"{n_live_overdue:,}",
    help="Past due but under 30 days. Excludes the stale pile, which would "
         "otherwise drown this number.",
)
c3.metric(
    "Unscheduled", f"{n_undated:,}",
    help="No due or close date on the record in any source system.",
)
c4.metric(
    "Stale", f"{n_stale:,}",
    delta=f"{n_stale / len(data):.0%} of all items" if len(data) else None,
    delta_color="inverse",
    help="30+ days overdue. Almost certainly needs closing or re-dating "
         "at the source rather than doing.",
)

st.divider()

# --- filters ---------------------------------------------------------------
all_sources = sorted(data["Source"].unique())
sources = st.multiselect("Source", all_sources, default=all_sources)
view = data[data["Source"].isin(sources)]

companies = sorted(view["Company"].dropna().unique())
if companies:
    picked = st.multiselect("Company", companies, default=[])
    if picked:
        view = view[view["Company"].isin(picked)]

COLUMNS = ["When", "Source", "Task", "Company", "Context", "Link"]
COLUMN_CONFIG = {
    "When": st.column_config.TextColumn("When", width="small"),
    "Source": st.column_config.TextColumn("Source", width="small"),
    "Task": st.column_config.TextColumn("Task", width="large"),
    "Link": st.column_config.LinkColumn("Open", display_text="Open ↗", width="small"),
}


def _table_for(frame: pd.DataFrame) -> None:
    if frame.empty:
        st.caption("Nothing here.")
        return
    st.dataframe(
        frame[COLUMNS],
        width="stretch",
        hide_index=True,
        column_config=COLUMN_CONFIG,
    )


stale = view[view["Tier"] == STALE_TIER]
undated = view[view["Tier"] == "none"]
attention = view[~view["Tier"].isin({STALE_TIER, "none"})]

st.subheader(f"Needs attention · {len(attention):,}")
st.caption("Has a due or close date and is not yet abandoned.")
_table_for(attention)

st.subheader(f"Unscheduled · {len(undated):,}")
st.caption(
    "Real work with no date on it anywhere. Slack messages are newest first; "
    "the rest have no ordering signal, which is itself worth fixing at the source."
)
_table_for(undated)

with st.expander(f"⚠️ Stale — 30+ days overdue · {len(stale):,}", expanded=False):
    st.caption(
        "Deliberately collapsed. Nothing stays a month overdue and still live, so "
        "these are almost always done-but-not-closed or quietly dropped. Worth a "
        "triage pass to close or re-date them at the source — every one of these "
        "is inflating the overdue count above."
    )
    _table_for(stale)

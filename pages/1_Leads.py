"""Leads page — qualified (engaged ICP) leads, with an opt-in to see all ICP."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st

from classify import qualified as _qualified
from classify.leads import LEAD_CATEGORIES, classify_dataframe
from store import db

if not st.session_state.get("authed"):
    st.warning("Sign in on the main page first.")
    st.stop()


@st.cache_data(ttl=3600)
def load_contacts() -> pd.DataFrame:
    db.init_db()
    with db.connect() as conn:
        df = pd.read_sql("SELECT * FROM contacts", conn)
    if "createdate" in df.columns:
        df["createdate"] = pd.to_datetime(df["createdate"], errors="coerce", utc=True)
    return df


@st.cache_data(ttl=3600)
def load_companies() -> pd.DataFrame:
    db.init_db()
    with db.connect() as conn:
        return pd.read_sql("SELECT * FROM companies", conn)


st.title("📋 Qualified Leads")
st.caption(
    "ICP firms (RIA · Broker-Dealer · WealthTech) whose contacts have actually "
    "**engaged** — a form fill, email open/click, logged contact, or an associated "
    "deal. A plain `firm_type` match is an industry filter, not demand, so "
    "unengaged ICP contacts are hidden by default. Flip **Show** to include them."
)

contacts = load_contacts()
companies = load_companies()

if contacts.empty:
    st.warning("No data — refresh from the Sales page first.")
    st.stop()

classified = classify_dataframe(contacts, companies)
classified = _qualified.qualify(classified)  # adds engaged / account / motion
company_lookup = companies.set_index("id")[["name", "domain"]].to_dict("index") if not companies.empty else {}


def _company_name(cid):
    if not cid:
        return None
    info = company_lookup.get(cid)
    return info["name"] if info else None


def _company_domain(cid):
    if not cid:
        return None
    info = company_lookup.get(cid)
    return info["domain"] if info else None


classified["company_name"] = classified["company_id"].apply(_company_name)
classified["company_domain"] = classified["company_id"].apply(_company_domain)

# Scope controls — what counts as a lead worth showing.
scope_col, motion_col = st.columns(2)
with scope_col:
    show_mode = st.radio(
        "Show",
        ["Qualified — engaged only", "All ICP leads (incl. unengaged)"],
        index=0, horizontal=True,
    )
with motion_col:
    motion_label = st.radio(
        "Motion",
        ["All motions", "Advisory (RIA · Broker-Dealer)", "WealthTech vendors (WTIS)"],
        index=0, horizontal=True,
    )
engaged_only = show_mode.startswith("Qualified")
motion_key = (
    "advisory" if motion_label.startswith("Advisory")
    else "vendor" if motion_label.startswith("WealthTech")
    else None
)

# Detail filters
col1, col2, col3 = st.columns(3)
with col1:
    today = datetime.now(timezone.utc).date()
    date_range = st.date_input("Period", value=(today - timedelta(days=90), today), max_value=today)
with col2:
    selected_categories = st.multiselect("Lead category", LEAD_CATEGORIES, default=LEAD_CATEGORIES)
with col3:
    sources = sorted(classified["hs_analytics_source"].dropna().unique().tolist())
    selected_sources = st.multiselect("HubSpot source", sources, default=sources)

if isinstance(date_range, tuple) and len(date_range) == 2:
    start_ts = pd.Timestamp(date_range[0], tz="UTC")
    end_ts = pd.Timestamp(date_range[1], tz="UTC") + pd.Timedelta(days=1)
else:
    start_ts = pd.Timestamp(today - timedelta(days=90), tz="UTC")
    end_ts = pd.Timestamp(today, tz="UTC") + pd.Timedelta(days=1)

mask = (
    (classified["lead_status"] == "lead")
    & (classified["createdate"] >= start_ts)
    & (classified["createdate"] < end_ts)
    & (classified["lead_category"].isin(selected_categories))
    & (classified["hs_analytics_source"].isin(selected_sources) | classified["hs_analytics_source"].isna())
)
if engaged_only:
    mask &= classified["engaged"]
if motion_key is not None:
    mask &= classified["motion"] == motion_key
filtered = classified[mask]

m1, m2, m3 = st.columns(3)
m1.metric("Qualified leads" if engaged_only else "ICP leads", f"{len(filtered):,}")
m2.metric(
    "Distinct firms", f"{filtered['account'].nunique():,}",
    help="A firm counts once however many of its contacts match.",
)
_engaged_n = int(filtered["engaged"].sum())
_engaged_rate = (_engaged_n / len(filtered) * 100) if len(filtered) else 0
m3.metric(
    "Engaged", f"{_engaged_n:,}",
    delta=f"{_engaged_rate:.0f}% of shown", delta_color="off",
    help="Contacts with at least one recorded interaction.",
)

_MOTION_LABEL = {"advisory": "Advisory", "vendor": "WealthTech vendor"}
disp = filtered.copy()
disp["_motion"] = disp["motion"].map(_MOTION_LABEL).fillna("—")
disp["_engaged"] = disp["engaged"].map({True: "✅", False: "—"})

display = disp[[
    "createdate", "email", "company_name", "company_domain",
    "lead_category", "_motion", "_engaged",
    "hs_analytics_source", "first_conversion_event_name", "recent_conversion_event_name",
    "lifecyclestage", "id",
]].rename(columns={
    "createdate": "Created",
    "email": "Email",
    "company_name": "Company",
    "company_domain": "Domain",
    "lead_category": "Category",
    "_motion": "Motion",
    "_engaged": "Engaged",
    "hs_analytics_source": "Original source",
    "first_conversion_event_name": "First conversion",
    "recent_conversion_event_name": "Recent conversion",
    "lifecyclestage": "Lifecycle stage",
    "id": "HubSpot contact ID",
}).sort_values("Created", ascending=False)

st.dataframe(display, width="stretch", hide_index=True)

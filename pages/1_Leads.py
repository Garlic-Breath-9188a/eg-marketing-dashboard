"""Leads page — contact-level list of classified leads."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st

from classify.leads import LEAD_CATEGORIES, classify_dataframe
from store import db

st.set_page_config(page_title="Leads — EG Marketing Dashboard", page_icon="📋", layout="wide")

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


st.title("📋 Leads")

contacts = load_contacts()
companies = load_companies()

if contacts.empty:
    st.warning("No data — refresh from the Overview page first.")
    st.stop()

classified = classify_dataframe(contacts, companies)
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

# Filters
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

filtered = classified[
    (classified["lead_status"] == "lead")
    & (classified["createdate"] >= start_ts)
    & (classified["createdate"] < end_ts)
    & (classified["lead_category"].isin(selected_categories))
    & (classified["hs_analytics_source"].isin(selected_sources) | classified["hs_analytics_source"].isna())
]

st.metric("Matching leads", f"{len(filtered):,}")

display = filtered[[
    "createdate", "email", "company_name", "company_domain",
    "lead_category", "classification_source",
    "hs_analytics_source", "first_conversion_event_name", "recent_conversion_event_name",
    "lifecyclestage", "id",
]].rename(columns={
    "createdate": "Created",
    "email": "Email",
    "company_name": "Company",
    "company_domain": "Domain",
    "lead_category": "Category",
    "classification_source": "Source of classification",
    "hs_analytics_source": "Original source",
    "first_conversion_event_name": "First conversion",
    "recent_conversion_event_name": "Recent conversion",
    "lifecyclestage": "Lifecycle stage",
    "id": "HubSpot contact ID",
}).sort_values("Created", ascending=False)

st.dataframe(display, use_container_width=True, hide_index=True)

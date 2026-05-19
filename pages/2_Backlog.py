"""Backlog page — unclassified contacts that have engagement events.

The dashboard treats firm_type as the source of truth for "is this a lead."
When a contact submits a form / downloads / clicks but has no firm_type,
they're invisible to the lead count. This page surfaces them so you can fix
the classification in HubSpot.

A contact appears here when ALL of the following are true:
  • lead_status == "unclassified" (no firm_type on Contact or Company)
  • has at least one engagement signal (form fill / conversion event)
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from classify.leads import classify_dataframe
from store import db

st.set_page_config(page_title="Backlog — EG Marketing Dashboard", page_icon="🛠️", layout="wide")

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


HUBSPOT_PORTAL_BASE = st.secrets.get("HUBSPOT_PORTAL_BASE", "https://app.hubspot.com")

st.title("🛠️ Classification Backlog")
st.caption(
    "Contacts with engagement (form fills, conversions) but no firm_type set on "
    "Contact or Company. Open each in HubSpot and set firm_type so they count "
    "toward leads next refresh."
)

contacts = load_contacts()
companies = load_companies()
if contacts.empty:
    st.warning("No data — refresh from the Overview page first.")
    st.stop()

classified = classify_dataframe(contacts, companies)

# Lookup for company info
company_lookup = (
    companies.set_index("id")[["name", "domain"]].to_dict("index") if not companies.empty else {}
)
classified["company_name"] = classified["company_id"].apply(
    lambda cid: company_lookup.get(cid, {}).get("name") if cid else None
)
classified["company_domain"] = classified["company_id"].apply(
    lambda cid: company_lookup.get(cid, {}).get("domain") if cid else None
)

# Filter: unclassified + has engagement
backlog = classified[
    (classified["lead_status"] == "unclassified")
    & (
        classified["first_conversion_event_name"].notna()
        | classified["recent_conversion_event_name"].notna()
        | (classified["num_conversion_events"].fillna(0) > 0)
    )
].copy()

# Engagement summary
backlog["engagement_count"] = backlog["num_conversion_events"].fillna(0).astype(int)

st.metric(
    "Unclassified contacts with engagement",
    f"{len(backlog):,}",
    help="Each of these contacts has at least one form fill or conversion event but no firm_type. Set firm_type on them (or their associated Company) in HubSpot.",
)

total_events = int(backlog["engagement_count"].sum())
st.metric("Total engagement events from this backlog", f"{total_events:,}")

st.divider()


def _hubspot_url(contact_id: str) -> str:
    return f"{HUBSPOT_PORTAL_BASE}/contacts/_/contact/{contact_id}"


backlog["HubSpot link"] = backlog["id"].apply(_hubspot_url)

display = backlog[[
    "createdate", "email", "company_name", "company_domain",
    "first_conversion_event_name", "recent_conversion_event_name",
    "engagement_count", "hs_analytics_source", "HubSpot link",
]].rename(columns={
    "createdate": "Created",
    "email": "Email",
    "company_name": "Company",
    "company_domain": "Domain",
    "first_conversion_event_name": "First conversion",
    "recent_conversion_event_name": "Recent conversion",
    "engagement_count": "Conversions",
    "hs_analytics_source": "Original source",
}).sort_values("Conversions", ascending=False)

st.dataframe(
    display,
    use_container_width=True,
    hide_index=True,
    column_config={
        "HubSpot link": st.column_config.LinkColumn("Open in HubSpot", display_text="Open ↗"),
    },
)

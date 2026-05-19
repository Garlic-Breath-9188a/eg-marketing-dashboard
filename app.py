"""EG Marketing Dashboard — main entry / Overview page."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import plotly.express as px
import streamlit as st

from classify.leads import LEAD_CATEGORIES, classify_dataframe
from ingest import hubspot
from store import db

st.set_page_config(
    page_title="EG Marketing Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ---------------------------------------------------------------------------
# Password gate
# ---------------------------------------------------------------------------
def _check_password() -> bool:
    if st.session_state.get("authed"):
        return True
    with st.form("login"):
        st.subheader("EG Marketing Dashboard")
        pw = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in")
    if submitted:
        if pw == st.secrets.get("DASHBOARD_PASSWORD", ""):
            st.session_state["authed"] = True
            st.rerun()
        else:
            st.error("Incorrect password.")
    return False


if not _check_password():
    st.stop()


# ---------------------------------------------------------------------------
# Data load
# ---------------------------------------------------------------------------
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


def refresh_from_hubspot():
    token = st.secrets.get("HUBSPOT_TOKEN", "")
    if not token:
        st.error("HUBSPOT_TOKEN not configured in secrets.toml.")
        return
    with st.spinner("Refreshing from HubSpot…"):
        result = hubspot.refresh(token)
    st.success(f"Refreshed: {result['contacts']} contacts, {result['companies']} companies.")
    load_contacts.clear()
    load_companies.clear()


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("### Data")
    last_refresh = db.get_meta("last_full_refresh")
    if last_refresh:
        st.caption(f"Last refresh: {last_refresh}")
    else:
        st.caption("No data yet — run an initial refresh.")
    if st.button("🔄 Refresh from HubSpot"):
        refresh_from_hubspot()

    st.markdown("---")
    st.markdown("### Date range")
    today = datetime.now(timezone.utc).date()
    default_start = today - timedelta(days=90)
    date_range = st.date_input(
        "Period",
        value=(default_start, today),
        max_value=today,
    )
    if isinstance(date_range, tuple) and len(date_range) == 2:
        start_date, end_date = date_range
    else:
        start_date, end_date = default_start, today


# ---------------------------------------------------------------------------
# Main: Overview
# ---------------------------------------------------------------------------
st.title("📊 Marketing Overview")
st.caption(f"Leads = contacts whose firm_type is in: {', '.join(LEAD_CATEGORIES)}")

contacts = load_contacts()
companies = load_companies()

if contacts.empty:
    st.warning("No data in the cache yet. Click **Refresh from HubSpot** in the sidebar.")
    st.stop()

classified = classify_dataframe(contacts, companies)

start_ts = pd.Timestamp(start_date, tz="UTC")
end_ts = pd.Timestamp(end_date, tz="UTC") + pd.Timedelta(days=1)
in_period = classified[
    (classified["createdate"] >= start_ts) & (classified["createdate"] < end_ts)
].copy()

# ---- KPI cards ----
n_contacts = len(in_period)
n_leads = (in_period["lead_status"] == "lead").sum()
n_unclassified = (in_period["lead_status"] == "unclassified").sum()
n_non_lead = (in_period["lead_status"] == "non_lead").sum()

col1, col2, col3, col4 = st.columns(4)
col1.metric("Leads (in period)", f"{n_leads:,}")
col2.metric("Total new contacts", f"{n_contacts:,}")
col3.metric("Unclassified (backlog)", f"{n_unclassified:,}",
            help="New contacts with no firm_type set on either Contact or Company. Fix in HubSpot to make next week's numbers accurate.")
col4.metric("Non-ICP", f"{n_non_lead:,}")

st.divider()

# ---- Leads by week ----
st.subheader("Leads by week")
if n_leads > 0:
    leads_only = in_period[in_period["lead_status"] == "lead"].copy()
    leads_only["week"] = leads_only["createdate"].dt.to_period("W").dt.start_time
    weekly = (
        leads_only.groupby(["week", "lead_category"])
        .size()
        .reset_index(name="leads")
    )
    fig = px.bar(weekly, x="week", y="leads", color="lead_category",
                 category_orders={"lead_category": LEAD_CATEGORIES})
    fig.update_layout(xaxis_title="Week", yaxis_title="Leads", legend_title="Firm type")
    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("No leads in the selected period.")

# ---- Leads by source ----
st.subheader("Leads by HubSpot source")
if n_leads > 0:
    leads_only = in_period[in_period["lead_status"] == "lead"].copy()
    by_source = (
        leads_only["hs_analytics_source"]
        .fillna("(unknown)")
        .value_counts()
        .reset_index()
    )
    by_source.columns = ["source", "leads"]
    fig = px.bar(by_source, x="source", y="leads")
    fig.update_layout(xaxis_title="HubSpot original source", yaxis_title="Leads")
    st.plotly_chart(fig, use_container_width=True)

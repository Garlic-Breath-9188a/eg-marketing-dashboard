"""Sales Dashboard — entry point / navigation router.

Streamlit Cloud runs this file. It owns the one-time setup that must happen
before any page renders — page config, the password gate, and the first-run
cache bootstrap — then hands off to the selected page via st.navigation.

The navigation list is the single source of truth for the sidebar menu: its
order is the menu order (Sales first as the default, Classification Backlog
last because it is cleanup, not daily work) and each st.Page title is the
label shown in the sidebar (so the entry page reads "Sales", not "app").
"""
from __future__ import annotations

import streamlit as st

from ingest import refresh_all
from store import db

st.set_page_config(
    page_title="Sales Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _check_password() -> bool:
    if st.session_state.get("authed"):
        return True
    with st.form("login"):
        st.subheader("Sales Dashboard")
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

db.init_db()

# Streamlit Cloud has no persistent disk: every redeploy starts with an empty
# SQLite cache, and no cron exists there to fill it. Bootstrap on first load so
# the deployed app is never blank. Locally this is a no-op — the launchd job
# (scripts/com.ezragroup.eg-dashboard-refresh.plist) keeps the cache warm.
if not db._count("contacts") and not st.session_state.get("_bootstrapped"):
    st.session_state["_bootstrapped"] = True
    with st.spinner("First run — loading data from all sources. This takes a minute…"):
        _report = refresh_all.refresh_all(dict(st.secrets), only_stale=False)
    if _report.failed:
        st.warning(_report.summary())

# Sidebar menu. Order here == order in the sidebar. Classification Backlog is
# intentionally last (cleanup, not daily work).
_pages = [
    st.Page("home.py", title="Sales", icon="📊", default=True),
    st.Page("pages/0_Daily_Briefing.py", title="Daily Briefing", icon="🗞️"),
    st.Page("pages/1_Leads.py", title="Leads", icon="📋"),
    st.Page("pages/3_Forms.py", title="Forms", icon="📨"),
    st.Page("pages/4_LinkedIn.py", title="LinkedIn", icon="💼"),
    st.Page("pages/5_Content.py", title="Content", icon="📰"),
    st.Page("pages/6_Tasks.py", title="Tasks", icon="✅"),
    st.Page("pages/7_Follow_ups.py", title="Follow-ups", icon="📮"),
    st.Page("pages/2_Backlog.py", title="Classification Backlog", icon="🛠️"),
]

st.navigation(_pages).run()

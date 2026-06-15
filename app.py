"""EG Marketing Dashboard — main entry / Command Center page."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import pandas as pd
import streamlit as st

from classify.leads import LEAD_CATEGORIES, classify_dataframe
from ingest import authoredup, hubspot, wordpress
from store import db

st.set_page_config(
    page_title="EG Marketing Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Compact density: shrink metric font, tighten vertical spacing.
st.markdown(
    """
    <style>
      .block-container { padding-top: 3rem; padding-bottom: 1rem; }
      [data-testid="stMetricValue"] { font-size: 1.4rem; }
      [data-testid="stMetricLabel"] { font-size: 0.8rem; }
      [data-testid="stMetricDelta"] { font-size: 0.75rem; }
      h1 { font-size: 1.6rem !important; padding-top: 0.5rem !important; margin-bottom: 0.5rem !important; line-height: 1.3 !important; }
      h2 { font-size: 1.15rem !important; padding-top: 0.5rem !important; }
      h3 { font-size: 1rem !important; }
      h4 { font-size: 0.95rem !important; padding-top: 0.4rem !important; margin-bottom: 0.3rem !important; }
      hr { margin: 0.5rem 0 !important; }
      div[data-testid="stHorizontalBlock"] { gap: 0.5rem; }
    </style>
    """,
    unsafe_allow_html=True,
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

db.init_db()


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
    # Ensure newer columns exist even if cached load predates the migration
    for col in ("hubspot_url", "num_associated_deals", "notes_last_contacted",
                "hs_email_last_open_date", "hs_email_last_click_date",
                "hubspot_owner_id", "hs_lead_status"):
        if col not in df.columns:
            df[col] = None
    return df


@st.cache_data(ttl=3600)
def load_companies() -> pd.DataFrame:
    db.init_db()
    with db.connect() as conn:
        return pd.read_sql("SELECT * FROM companies", conn)


@st.cache_data(ttl=3600)
def load_forms() -> pd.DataFrame:
    db.init_db()
    with db.connect() as conn:
        return pd.read_sql("SELECT * FROM forms", conn)


@st.cache_data(ttl=3600)
def load_form_submissions() -> pd.DataFrame:
    db.init_db()
    with db.connect() as conn:
        df = pd.read_sql("SELECT * FROM form_submissions", conn)
    if "submitted_at" in df.columns:
        df["submitted_at"] = pd.to_datetime(df["submitted_at"], errors="coerce", utc=True)
    return df


@st.cache_data(ttl=3600)
def load_linkedin_posts() -> pd.DataFrame:
    db.init_db()
    with db.connect() as conn:
        df = pd.read_sql("SELECT * FROM linkedin_posts", conn)
    if "published_at" in df.columns:
        df["published_at"] = pd.to_datetime(df["published_at"], errors="coerce", utc=True)
    return df


@st.cache_data(ttl=3600)
def load_deals() -> pd.DataFrame:
    db.init_db()
    with db.connect() as conn:
        try:
            df = pd.read_sql("SELECT * FROM deals", conn)
        except Exception:
            return pd.DataFrame()
    for col in ("closedate", "createdate"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce", utc=True)
    # Ensure stage-metadata columns exist even if cached load predates the migration.
    for col in ("stage_is_closed", "stage_is_won", "stage_label"):
        if col not in df.columns:
            df[col] = None
    return df


@st.cache_data(ttl=3600)
def load_tasks() -> pd.DataFrame:
    db.init_db()
    with db.connect() as conn:
        try:
            df = pd.read_sql("SELECT * FROM tasks", conn)
        except Exception:
            return pd.DataFrame()
    for col in ("due_at", "completed_at"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce", utc=True)
    return df


def refresh_from_hubspot():
    token = st.secrets.get("HUBSPOT_TOKEN", "")
    if not token:
        st.error("HUBSPOT_TOKEN not configured in secrets.toml.")
        return
    status = st.empty()
    bar = st.progress(0.0)

    def _progress(stage, current, total):
        status.text(f"{stage}… ({current}/{total})" if total else f"{stage}…")
        bar.progress(min(1.0, current / total) if total else 0.0)

    result = hubspot.refresh(token, progress=_progress)
    status.empty()
    bar.empty()
    deals_n = result.get("deals", 0)
    tasks_n = result.get("tasks", 0)
    extra = ""
    if deals_n or tasks_n:
        extra = f", {deals_n} deals, {tasks_n} tasks"
    else:
        extra = " (deals/tasks: 0 — add crm.objects.deals.read scope to Service Key to enable)"
    st.success(
        f"Refreshed: {result['contacts']} contacts, {result['companies']} companies, "
        f"{result['forms']} forms, {result['submissions']} submissions{extra}."
    )
    load_contacts.clear()
    load_companies.clear()
    load_forms.clear()
    load_form_submissions.clear()
    load_linkedin_posts.clear()
    load_deals.clear()
    load_tasks.clear()


def refresh_from_authoredup():
    api_key = st.secrets.get("AUTHOREDUP_API_KEY", "")
    if not api_key:
        st.error("AUTHOREDUP_API_KEY not configured in secrets.toml.")
        return
    status = st.empty()

    def _progress(stage, current, total):
        status.text(f"{stage}…")

    try:
        result = authoredup.refresh(api_key, progress=_progress)
    except Exception as e:
        status.empty()
        st.error(f"AuthoredUp refresh failed: {e}")
        return
    status.empty()
    st.success(f"AuthoredUp refreshed: {result['actors']} actors, {result['posts']} posts.")
    load_linkedin_posts.clear()


def refresh_from_wordpress():
    base_url = st.secrets.get("WORDPRESS_BASE_URL", "")
    if not base_url:
        st.error(
            "WORDPRESS_BASE_URL not configured in secrets. "
            "Add WORDPRESS_BASE_URL (and optionally WORDPRESS_USER/WORDPRESS_APP_PASSWORD + WPCOM_API_TOKEN/WPCOM_SITE) to `.streamlit/secrets.toml`."
        )
        return
    status = st.empty()

    def _progress(stage, current, total):
        status.text(f"{stage}…")

    try:
        result = wordpress.refresh(dict(st.secrets), progress=_progress)
    except Exception as e:
        status.empty()
        st.error(f"WordPress refresh failed: {e}")
        return
    status.empty()
    if result.get("error"):
        st.error(result["error"])
    else:
        st.success(f"WordPress refreshed: {result.get('posts', 0)} posts.")


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
    last_linkedin = db.get_meta("last_linkedin_refresh")
    if last_linkedin:
        st.caption(f"LinkedIn refresh: {last_linkedin}")
    if st.button("💼 Refresh from AuthoredUp"):
        refresh_from_authoredup()
    last_wp = db.get_meta("last_wordpress_refresh")
    if last_wp:
        st.caption(f"WordPress refresh: {last_wp}")
    if st.button("📰 Refresh from WordPress"):
        refresh_from_wordpress()

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

    st.markdown("---")
    st.markdown("### Data trust")
    trusted_from = st.date_input(
        "Trust data from",
        value=datetime(2026, 2, 1).date(),
        help="HubSpot was adopted in Feb 2026. Period-over-period comparisons (deltas) are suppressed when the prior comparison window falls before this date.",
    )

    st.markdown("---")
    st.caption(
        "Leads = contacts whose firm_type ∈ "
        + ", ".join(LEAD_CATEGORIES)
        + ". Form data from HubSpot Form Submissions API."
    )


# ===========================================================================
# Marketing Command Center — exception-based, deal-focused Overview
# ===========================================================================
ASANA_STRATEGY_URL = "https://app.asana.com/1/1182309086818187/project/1214458328037729"
ASANA_STRATEGY_NAME = "90-Day Marketing Demand Generation Plan"


def _asana_search_url(query) -> str | None:
    """Asana search deep-link for a task subject — HubSpot tasks have no Asana ID,
    so we open a search for the task name to land on the matching Asana task to edit."""
    q = (str(query) if query is not None else "").strip()
    if not q:
        return ASANA_STRATEGY_URL
    return f"https://app.asana.com/0/search?q={quote(q)}"

# Constructed HubSpot record URLs. Contacts/companies/deals don't return a record
# URL from the v3 API, so we build canonical deep links:
#   {base}/contacts/{portalId}/record/{objectTypeId}/{recordId}
# (objectTypeId: 0-1 contact, 0-2 company, 0-3 deal). The portal ID is required for
# the link to resolve — read it from the HUBSPOT_PORTAL_ID secret, else from cache
# meta (auto-populated at ingest), else fall back to the legacy /_/ shortcut.
HUBSPOT_PORTAL_BASE = st.secrets.get("HUBSPOT_PORTAL_BASE", "https://app.hubspot.com")
HUBSPOT_PORTAL_ID = str(
    st.secrets.get("HUBSPOT_PORTAL_ID", "") or db.get_meta("hubspot_portal_id") or ""
).strip()


def _record_url(object_type_id: str, rid, legacy_path: str) -> str | None:
    if not rid:
        return None
    if HUBSPOT_PORTAL_ID:
        return f"{HUBSPOT_PORTAL_BASE}/contacts/{HUBSPOT_PORTAL_ID}/record/{object_type_id}/{rid}"
    return f"{HUBSPOT_PORTAL_BASE}/contacts/_/{legacy_path}/{rid}"


def _contact_url(cid) -> str | None:
    return _record_url("0-1", cid, "contact")


def _company_url(cid) -> str | None:
    return _record_url("0-2", cid, "company")


def _deal_url(deal_id, stored_url=None) -> str | None:
    if stored_url:
        return stored_url
    return _record_url("0-3", deal_id, "deal")


# ---- Header: title + Asana strategy link ----
head_l, head_r = st.columns([0.68, 0.32])
with head_l:
    st.title("📊 Marketing Command Center")
with head_r:
    st.markdown(
        f"<div style='text-align:right; padding-top:1.5rem;'>"
        f"<a href='{ASANA_STRATEGY_URL}' target='_blank' "
        f"style='display:inline-block; padding:0.45rem 0.9rem; border-radius:6px; "
        f"background:#796eff; color:#fff; font-size:0.85rem; font-weight:600; text-decoration:none;'>"
        f"🎯 90-Day Marketing Strategy ↗</a></div>",
        unsafe_allow_html=True,
    )

_period_days = (end_date - start_date).days + 1
st.caption(
    f"Period: **{start_date.strftime('%b %d, %Y')} – {end_date.strftime('%b %d, %Y')}** "
    f"({_period_days} days). Change in the sidebar · strategy tracked in Asana: *{ASANA_STRATEGY_NAME}*."
)

# ---- Load + classify ----
contacts = load_contacts()
companies = load_companies()
if contacts.empty:
    st.warning("No data in the cache yet. Click **Refresh from HubSpot** in the sidebar.")
    st.stop()

classified = classify_dataframe(contacts, companies)
submissions_df = load_form_submissions()
deals_df = load_deals()
tasks_df = load_tasks()

now_ts = pd.Timestamp.now(tz="UTC")
start_ts = pd.Timestamp(start_date, tz="UTC")
end_ts = pd.Timestamp(end_date, tz="UTC") + pd.Timedelta(days=1)
period_length = end_ts - start_ts
prior_start_ts = start_ts - period_length
prior_end_ts = start_ts

in_period = classified[
    (classified["createdate"] >= start_ts) & (classified["createdate"] < end_ts)
].copy()
in_prior_period = classified[
    (classified["createdate"] >= prior_start_ts) & (classified["createdate"] < prior_end_ts)
].copy()

trusted_from_ts = pd.Timestamp(trusted_from, tz="UTC")
prior_comparison_trustworthy = prior_start_ts >= trusted_from_ts


def _fmt_delta(current: float, prior: float) -> str | None:
    """Signed percent-change vs the prior period, or None to hide the delta."""
    if not prior_comparison_trustworthy:
        return None  # Prior window is pre-HubSpot — comparison would be meaningless.
    if prior == 0:
        return None if current == 0 else "new (vs 0)"
    pct = (current - prior) / prior * 100
    return f"{pct:+.0f}% vs prior"


# ---------------------------------------------------------------------------
# Exclusion lists (internal / vendor / partner — not prospects)
# ---------------------------------------------------------------------------
ICP_FIRM_TYPES_FOR_OUTREACH = {
    "ria", "brokerdealer", "banktrust", "custodian", "asset_manager", "fintech",
}
EXCLUDED_DOMAINS = {
    "ezragroup.com",      # Own company
    "grimmandco.org",     # Fractional CMO firm
    "streetcredpr.com",   # PR firm
    "mochadesigns.co",    # External development partner
}
EXCLUDED_EMAILS = {
    "grimm.marcus@gmail.com",  # Fractional CMO (personal email)
}


def _domain_of(email: str | None) -> str | None:
    if not email or "@" not in str(email):
        return None
    return str(email).split("@")[-1].lower().strip()


def _is_excluded(email: str | None) -> bool:
    if not email:
        return False
    e = str(email).lower().strip()
    if e in EXCLUDED_EMAILS:
        return True
    dom = _domain_of(e)
    return dom in EXCLUDED_DOMAINS if dom else False


# ---------------------------------------------------------------------------
# Engagement builders (hot accounts, stalled, multi-touch warm)
# ---------------------------------------------------------------------------
HEAT_WINDOW_DAYS = 30
heat_cutoff = now_ts - pd.Timedelta(days=HEAT_WINDOW_DAYS)


def _build_hot_accounts() -> pd.DataFrame:
    """ICP firms with 2+ contacts who submitted a form in the last HEAT_WINDOW_DAYS."""
    if submissions_df.empty or classified.empty:
        return pd.DataFrame()
    recent_subs = submissions_df[submissions_df["submitted_at"] >= heat_cutoff].copy()
    if recent_subs.empty:
        return pd.DataFrame()
    recent_subs = recent_subs[~recent_subs["contact_email"].apply(_is_excluded)]
    if recent_subs.empty:
        return pd.DataFrame()

    email_to_company = (
        classified.dropna(subset=["email"])
        .assign(email_lc=classified["email"].str.lower())
        .set_index("email_lc")[["company_id", "lead_status", "lead_category", "firm_type"]]
        .to_dict("index")
    )
    rows = []
    for _, sub in recent_subs.iterrows():
        email = sub.get("contact_email")
        if not email:
            continue
        info = email_to_company.get(email)
        if not info or not info.get("company_id"):
            continue
        rows.append({
            "company_id": info["company_id"],
            "contact_email": email,
            "lead_status": info["lead_status"],
            "submitted_at": sub["submitted_at"],
        })
    if not rows:
        return pd.DataFrame()
    eng = pd.DataFrame(rows)

    company_lookup = companies.set_index("id")[["name", "domain", "firm_type"]].to_dict("index") if not companies.empty else {}
    agg = (
        eng.groupby("company_id")
        .agg(
            contacts_engaged=("contact_email", "nunique"),
            submissions=("contact_email", "count"),
            last_activity=("submitted_at", "max"),
            leads_count=("lead_status", lambda s: int((s == "lead").sum())),
        )
        .reset_index()
    )

    def _ft(c):
        v = company_lookup.get(c, {}).get("firm_type")
        if v is None or (isinstance(v, float) and v != v):
            return ""
        return str(v).lower().strip()

    def _nm(c):
        v = company_lookup.get(c, {}).get("name")
        if v is None or (isinstance(v, float) and v != v):
            return None
        return str(v)

    def _dm(c):
        v = company_lookup.get(c, {}).get("domain")
        if v is None or (isinstance(v, float) and v != v):
            return None
        return str(v).lower().strip()

    agg["company_name"] = agg["company_id"].apply(_nm)
    agg["company_domain"] = agg["company_id"].apply(_dm)
    agg["firm_type"] = agg["company_id"].apply(_ft)

    agg = agg[~agg["company_domain"].fillna("").str.lower().isin(EXCLUDED_DOMAINS)]
    agg = agg[agg["firm_type"].isin(ICP_FIRM_TYPES_FOR_OUTREACH)]
    agg = agg[agg["contacts_engaged"] >= 2].copy()
    if agg.empty:
        return agg
    agg["heat_score"] = agg["contacts_engaged"] * agg["submissions"]
    agg["days_since"] = (now_ts - agg["last_activity"]).dt.days
    return agg.sort_values(["heat_score", "last_activity"], ascending=[False, False])


def _latest_activity_ts(row) -> pd.Timestamp | None:
    candidates = []
    for col in ("notes_last_contacted", "hs_email_last_click_date", "hs_email_last_open_date"):
        v = row.get(col)
        if v is None or (isinstance(v, float) and v != v) or v == "":
            continue
        try:
            ts = pd.Timestamp(v)
            if ts.tzinfo is None:
                ts = ts.tz_localize("UTC")
            candidates.append(ts)
        except (ValueError, TypeError):
            pass
    return max(candidates) if candidates else None


def _build_stalled_leads() -> pd.DataFrame:
    """ICP lead with prior pipeline activity (deal / sales touch / 2+ fills) but quiet 45+ days."""
    if classified.empty:
        return pd.DataFrame()
    df = classified[classified["lead_status"] == "lead"].copy()
    df = df[~df["email"].apply(_is_excluded)]
    if df.empty:
        return df
    df["_deals"] = df.get("num_associated_deals", 0).fillna(0)
    df["_convs"] = df.get("num_conversion_events", 0).fillna(0)
    df["_notes"] = df.get("notes_last_contacted")
    df["_has_pipeline"] = (
        (df["_deals"] >= 1)
        | (df["_notes"].notna() & (df["_notes"] != ""))
        | (df["_convs"] >= 2)
    )
    df = df[df["_has_pipeline"]].copy()
    if df.empty:
        return df
    df["_last_activity"] = df.apply(_latest_activity_ts, axis=1)
    df = df[df["_last_activity"].notna()].copy()
    df["_days_quiet"] = (now_ts - df["_last_activity"]).dt.days
    df = df[df["_days_quiet"] >= 45].copy()
    return df.sort_values("_days_quiet", ascending=False)


def _build_multi_touch_warm() -> pd.DataFrame:
    """ICP lead with 3+ conversions and zero associated deals — never started."""
    if classified.empty:
        return pd.DataFrame()
    df = classified[classified["lead_status"] == "lead"].copy()
    df = df[~df["email"].apply(_is_excluded)]
    if df.empty:
        return df
    df["_convs"] = df.get("num_conversion_events", 0).fillna(0)
    df["_deals"] = df.get("num_associated_deals", 0).fillna(0)
    df = df[(df["_convs"] >= 3) & (df["_deals"] == 0)].copy()
    if df.empty:
        return df
    df["_last_activity"] = df.apply(_latest_activity_ts, axis=1)
    return df.sort_values("_convs", ascending=False)


# ---------------------------------------------------------------------------
# Deals + tasks: open pipeline, hot-deal scoring, task urgency
# ---------------------------------------------------------------------------
CLOSED_STAGES = {"closedwon", "closedlost"}  # legacy fallback for pre-metadata caches


def _open_deals() -> pd.DataFrame:
    """Open = not closed. OR together every closed-signal we have: custom pipelines
    sometimes ship a "Closed Won/Lost" stage whose metadata.isClosed flag is NOT set,
    so an explicit "closed" stage label (or the legacy literal) is trusted on its own.
    """
    if deals_df.empty:
        return pd.DataFrame()
    df = deals_df
    closed = df["stage_is_closed"].fillna(0).astype(int) == 1
    if "stage_label" in df.columns:
        closed = closed | df["stage_label"].fillna("").str.contains("closed", case=False, na=False)
    if "dealstage" in df.columns:
        closed = closed | df["dealstage"].fillna("").str.lower().isin(CLOSED_STAGES)
    return df[~closed].copy()


def _active_tasks(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "status" not in df.columns or "due_at" not in df.columns:
        return pd.DataFrame()
    return df[~df["status"].fillna("").str.upper().isin({"COMPLETED", "DEFERRED"})].copy()


def _deal_ids_with_urgent_tasks(active: pd.DataFrame, horizon_days: int = 14) -> set[str]:
    """Deal IDs that have an active task overdue or due within `horizon_days`."""
    ids: set[str] = set()
    if active.empty or "associated_deal_ids" not in active.columns:
        return ids
    horizon = now_ts + pd.Timedelta(days=horizon_days)
    soon = active[active["due_at"] < horizon]
    for s in soon["associated_deal_ids"].dropna():
        for d in str(s).split(","):
            d = d.strip()
            if d:
                ids.add(d)
    return ids


open_deals = _open_deals()
active_tasks = _active_tasks(tasks_df)

# Overdue + due-this-week task slices
if not active_tasks.empty:
    week_cutoff = now_ts + pd.Timedelta(days=7)
    overdue_tasks = active_tasks[active_tasks["due_at"] < now_ts].copy()
    tasks_due_week = active_tasks[
        (active_tasks["due_at"] >= now_ts) & (active_tasks["due_at"] < week_cutoff)
    ].copy()
else:
    overdue_tasks = pd.DataFrame()
    tasks_due_week = pd.DataFrame()

urgent_deal_ids = _deal_ids_with_urgent_tasks(active_tasks)

# Company-name lookup for the deal + lead tables
company_name_lookup = companies.set_index("id")["name"].to_dict() if not companies.empty else {}

# Hot deals: blended score (value 0.5 · close-date proximity 0.3 · activity 0.2)
if not open_deals.empty:
    hot_deals = open_deals.copy()
    _max_amount = float(hot_deals["amount"].fillna(0).max() or 0.0)

    def _hot_deal_score(row) -> float:
        amount = row.get("amount") or 0
        value_score = (amount / _max_amount) if _max_amount else 0.0
        close_score = 0.0
        cd = row.get("closedate")
        if pd.notna(cd):
            days = (cd - now_ts).days
            if days <= 0:
                close_score = 1.0  # close date passed — needs attention now
            elif days <= 90:
                close_score = 1 - days / 90
        activity_score = 1.0 if str(row.get("id")) in urgent_deal_ids else 0.0
        return 0.5 * value_score + 0.3 * close_score + 0.2 * activity_score

    hot_deals["score"] = hot_deals.apply(_hot_deal_score, axis=1)
    hot_deals["has_urgent_task"] = hot_deals["id"].astype(str).isin(urgent_deal_ids)
    hot_deals = hot_deals.sort_values("score", ascending=False)
else:
    hot_deals = pd.DataFrame()

# Deals closing inside the selected period (open only)
if not open_deals.empty and "closedate" in open_deals.columns:
    closing_period = open_deals[
        (open_deals["closedate"] >= start_ts) & (open_deals["closedate"] < end_ts)
    ].copy()
else:
    closing_period = pd.DataFrame()

# ---- Engagement-based action sources ----
hot_accounts_df = _build_hot_accounts()
stalled_leads_df = _build_stalled_leads()
multi_touch_df = _build_multi_touch_warm()

# ---------------------------------------------------------------------------
# Critical KPI figures
# ---------------------------------------------------------------------------
QUALIFIED_CATS = ["RIA", "Broker-Dealer", "Fintech"]  # RIA · BD · WealthTech vendors

n_qualified = int(in_period["lead_category"].isin(QUALIFIED_CATS).sum())
p_qualified = int(in_prior_period["lead_category"].isin(QUALIFIED_CATS).sum())
n_ria = int((in_period["lead_category"] == "RIA").sum())
n_bd = int((in_period["lead_category"] == "Broker-Dealer").sum())
n_wt = int((in_period["lead_category"] == "Fintech").sum())

open_pipeline_value = float(open_deals["amount"].fillna(0).sum()) if not open_deals.empty else 0.0
n_open_deals = len(open_deals)
n_closing = len(closing_period)
closing_value = float(closing_period["amount"].fillna(0).sum()) if not closing_period.empty else 0.0
n_overdue = len(overdue_tasks)
n_due_week = len(tasks_due_week)


# ===========================================================================
# 1) ⚡ DO THIS NOW — exception-based action queue
# ===========================================================================
actions: list[dict] = []

# Priority 1 — overdue tasks (already late)
if not overdue_tasks.empty:
    for _, t in overdue_tasks.sort_values("due_at").head(8).iterrows():
        due = t.get("due_at")
        days_late = (now_ts - due).days if pd.notna(due) else None
        detail = (f"{days_late}d late" if days_late is not None else "overdue")
        if pd.notna(due):
            detail += f" · was due {due.strftime('%b %d')}"
        actions.append({
            "priority": 1, "icon": "🚨",
            "title": f"Overdue task: {t.get('subject') or '(no subject)'}",
            "detail": detail,
            "link": _asana_search_url(t.get("subject")),
            "link_label": "Edit in Asana ↗",
        })

# Priority 2 — hot accounts to call (3+ contacts engaged)
if not hot_accounts_df.empty:
    very_hot = hot_accounts_df[hot_accounts_df["contacts_engaged"] >= 3]
    for _, r in very_hot.head(5).iterrows():
        name = r.get("company_name") or r.get("company_domain") or r["company_id"]
        actions.append({
            "priority": 2, "icon": "🔥",
            "title": f"Call {name}",
            "detail": f"{int(r['contacts_engaged'])} contacts engaged · {int(r['submissions'])} fills in {HEAT_WINDOW_DAYS}d · last touch {int(r['days_since'])}d ago",
            "link": _company_url(r["company_id"]),
        })

# Priority 3 — open deals closing this period
if not closing_period.empty:
    for _, d in closing_period.sort_values("amount", ascending=False).head(5).iterrows():
        amt = d.get("amount") or 0
        cd = d.get("closedate")
        actions.append({
            "priority": 3, "icon": "💰",
            "title": f"Advance to close: {d.get('name') or '(unnamed deal)'}",
            "detail": f"${amt:,.0f} · {d.get('stage_label') or d.get('dealstage') or 'stage n/a'} · closes {cd.strftime('%b %d') if pd.notna(cd) else 'TBD'}",
            "link": _deal_url(d["id"], d.get("hubspot_url")),
        })

# Priority 4 — tasks due in the next 7 days
if not tasks_due_week.empty:
    for _, t in tasks_due_week.sort_values("due_at").head(6).iterrows():
        due = t.get("due_at")
        actions.append({
            "priority": 4, "icon": "📌",
            "title": f"Task due: {t.get('subject') or '(no subject)'}",
            "detail": f"due {due.strftime('%b %d') if pd.notna(due) else 'soon'}",
            "link": _asana_search_url(t.get("subject")),
            "link_label": "Edit in Asana ↗",
        })

# Priority 5 — stalled leads to re-engage
for _, c in stalled_leads_df.head(5).iterrows():
    actions.append({
        "priority": 5, "icon": "⏰",
        "title": f"Re-engage {c.get('email') or '(no email)'}",
        "detail": f"{c.get('lead_category') or c.get('firm_type') or 'ICP'} · quiet {int(c['_days_quiet'])}d · had prior pipeline activity",
        "link": _contact_url(c["id"]),
    })

# Priority 6 — multi-touch warm leads with no deal
for _, c in multi_touch_df.head(5).iterrows():
    actions.append({
        "priority": 6, "icon": "💎",
        "title": f"Start the conversation: {c.get('email') or '(no email)'}",
        "detail": f"{int(c['_convs'])} form fills · zero deals · {c.get('lead_category') or c.get('firm_type') or 'ICP'}",
        "link": _contact_url(c["id"]),
    })

st.markdown("#### ⚡ Do This Now")
_ACTION_CAP = 12
if not actions:
    st.markdown(
        "<div style='padding:0.5rem 0.8rem; border-radius:6px; background:#e8f5e9; "
        "border-left:4px solid #4caf50; font-size:0.9rem;'>"
        "<b>✅ You're clear.</b> No overdue tasks, hot accounts, or stalled leads need you right now — keep the pipeline fed."
        "</div>",
        unsafe_allow_html=True,
    )
else:
    actions.sort(key=lambda a: a["priority"])
    for a in actions[:_ACTION_CAP]:
        link_html = (
            f" <a href='{a['link']}' target='_blank' style='font-size:0.78rem; text-decoration:none; color:#796eff;'>{a.get('link_label', 'Open ↗')}</a>"
            if a.get("link") else ""
        )
        st.markdown(
            f"<div style='padding:0.32rem 0.7rem; margin-bottom:0.22rem; border-radius:4px; "
            f"background:#f7f8fb; border-left:3px solid #796eff; font-size:0.88rem;'>"
            f"<b>{a['icon']} {a['title']}</b>{link_html}<br>"
            f"<span style='color:#666; font-size:0.8rem;'>{a['detail']}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )
    if len(actions) > _ACTION_CAP:
        st.caption(f"+ {len(actions) - _ACTION_CAP} more lower-priority actions (re-engage / start-conversation) — see Hot Deals & Qualified Leads below.")


# ===========================================================================
# 2) Critical KPIs
# ===========================================================================
st.markdown("#### Critical KPIs")
k1, k2, k3, k4 = st.columns(4)
with k1:
    st.metric(
        "Qualified leads", f"{n_qualified:,}",
        delta=_fmt_delta(n_qualified, p_qualified),
        help="Priority-segment ICP leads created this period: RIA + Broker-Dealer + WealthTech vendors (Fintech).",
    )
    st.caption(f"{n_ria} RIA · {n_bd} BD · {n_wt} WealthTech")
with k2:
    st.metric(
        "Open pipeline", f"${open_pipeline_value:,.0f}",
        delta=(f"{n_open_deals} open deals" if n_open_deals else "no deal data"),
        delta_color="off",
        help="Total dollar value of all open (not closed) deals. Requires crm.objects.deals.read scope.",
    )
with k3:
    st.metric(
        "Closing this period", f"{n_closing:,}",
        delta=(f"${closing_value:,.0f} at stake" if n_closing else "none scheduled"),
        delta_color="off",
        help="Open deals with a close date inside the selected period.",
    )
with k4:
    st.metric(
        "Tasks overdue", f"{n_overdue:,}",
        delta=f"{n_due_week} due in 7d",
        delta_color="off",
        help="Open tasks past their due date. Delta = tasks due within the next 7 days. Requires crm.objects.tasks.read scope.",
    )


# ===========================================================================
# 3) 🔥 Hot Deals
# ===========================================================================
st.markdown("#### 🔥 Hot Deals — open deals ranked by value, close date & activity")
if hot_deals.empty:
    st.info(
        "No open deals in the cache. If you expect deals here, confirm the "
        "`crm.objects.deals.read` scope on the HubSpot Service Key, then refresh."
    )
else:
    hd = hot_deals.head(12).copy()
    hd["Deal"] = hd["name"]
    hd["Company"] = hd["primary_company_id"].apply(lambda c: company_name_lookup.get(c) if c else None)
    hd["Amount"] = hd["amount"].fillna(0)
    hd["Stage"] = hd["stage_label"].fillna(hd["dealstage"])
    hd["Close date"] = hd["closedate"].dt.date if "closedate" in hd.columns else None
    hd["Days open"] = (now_ts - hd["createdate"]).dt.days if "createdate" in hd.columns else None
    hd["Task due"] = hd["has_urgent_task"].map({True: "● due/overdue", False: ""})
    hd["HubSpot"] = hd.apply(lambda r: _deal_url(r["id"], r.get("hubspot_url")), axis=1)
    st.dataframe(
        hd[["Deal", "Company", "Amount", "Stage", "Close date", "Days open", "Task due", "HubSpot"]],
        use_container_width=True, hide_index=True, height=420,
        column_config={
            "Deal": st.column_config.TextColumn(width="medium"),
            "Company": st.column_config.TextColumn(width="medium"),
            "Amount": st.column_config.NumberColumn(format="$%d", width="small"),
            "Days open": st.column_config.NumberColumn(width="small"),
            "Task due": st.column_config.TextColumn(width="small", help="Has an active task overdue or due within 14 days"),
            "HubSpot": st.column_config.LinkColumn("Open", display_text="Open ↗", width="small"),
        },
    )


# ===========================================================================
# 4) 🎯 Qualified Leads
# ===========================================================================
st.markdown("#### 🎯 Qualified Leads — new RIA · Broker-Dealer · WealthTech in period")
ql = in_period[in_period["lead_category"].isin(QUALIFIED_CATS)].copy()
ql = ql[~ql["email"].apply(_is_excluded)]
if ql.empty:
    st.info("No qualified leads (RIA / Broker-Dealer / WealthTech) created in the selected period.")
else:
    ql = ql.sort_values("createdate", ascending=False).head(25)
    ql["Created"] = ql["createdate"].dt.date
    ql["Email"] = ql["email"]
    ql["Company"] = ql["company_id"].apply(lambda c: company_name_lookup.get(c) if c else None)
    ql["Category"] = ql["lead_category"]
    ql["Source"] = ql["hs_analytics_source"]
    ql["HubSpot"] = ql["id"].apply(_contact_url)
    st.dataframe(
        ql[["Created", "Email", "Company", "Category", "Source", "HubSpot"]],
        use_container_width=True, hide_index=True, height=420,
        column_config={
            "Email": st.column_config.TextColumn(width="medium"),
            "Company": st.column_config.TextColumn(width="medium"),
            "Category": st.column_config.TextColumn(width="small"),
            "HubSpot": st.column_config.LinkColumn("Open", display_text="Open ↗", width="small"),
        },
    )
    if int(in_period["lead_category"].isin(QUALIFIED_CATS).sum()) > 25:
        st.caption(f"Showing 25 most recent of {n_qualified} qualified leads. Full list on the **Leads** page.")

st.divider()
st.caption(
    "Detail moved to dedicated pages (left sidebar) → **Leads** · **Backlog** (classification cleanup) · "
    "**Forms** · **LinkedIn** · **Content**. This page stays focused on what needs action now."
)

"""EG Marketing Dashboard — main entry / Overview page."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import plotly.express as px
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
    st.markdown("### Forms")
    form_year_filter = st.selectbox(
        "Show forms",
        options=["2026 only", "2025 only", "All years"],
        index=0,
    )

    st.markdown("---")
    st.markdown("### Data trust")
    trusted_from = st.date_input(
        "Trust data from",
        value=datetime(2026, 2, 1).date(),
        help="HubSpot was adopted in Feb 2026. Period-over-period comparisons (deltas, signals) are suppressed when the prior comparison window falls before this date.",
    )

    st.markdown("---")
    st.caption(
        "Leads = contacts whose firm_type ∈ "
        + ", ".join(LEAD_CATEGORIES)
        + ". Form data from HubSpot Form Submissions API."
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
st.title("📊 Marketing Overview")
_period_days = (end_date - start_date).days + 1
st.caption(
    f"Period: **{start_date.strftime('%b %d, %Y')} – {end_date.strftime('%b %d, %Y')}** ({_period_days} days). "
    "Change in the sidebar."
)

contacts = load_contacts()
companies = load_companies()

if contacts.empty:
    st.warning("No data in the cache yet. Click **Refresh from HubSpot** in the sidebar.")
    st.stop()

classified = classify_dataframe(contacts, companies)

now_ts = pd.Timestamp.now(tz="UTC")
start_ts = pd.Timestamp(start_date, tz="UTC")
end_ts = pd.Timestamp(end_date, tz="UTC") + pd.Timedelta(days=1)
in_period = classified[
    (classified["createdate"] >= start_ts) & (classified["createdate"] < end_ts)
].copy()


# ---- Load form data and build per-form aggregates ----
forms_df = load_forms()
submissions_df = load_form_submissions()

# Apply year filter from sidebar
if form_year_filter == "2026 only":
    year_pattern = "2026"
elif form_year_filter == "2025 only":
    year_pattern = "2025"
else:
    year_pattern = None

if year_pattern and not forms_df.empty:
    forms_df = forms_df[forms_df["name"].str.contains(year_pattern, case=False, na=False)].copy()

# Exclude forms from the legacy/old site (ezragroup.com pre-redesign).
if not forms_df.empty:
    forms_df = forms_df[~forms_df["name"].str.contains("Old Site", case=False, na=False)].copy()

if not submissions_df.empty and not forms_df.empty:
    submissions_df = submissions_df[submissions_df["form_id"].isin(forms_df["id"])].copy()

# Compute prior period (same length, immediately before current)
period_length = end_ts - start_ts
prior_start_ts = start_ts - period_length
prior_end_ts = start_ts

in_prior_period = classified[
    (classified["createdate"] >= prior_start_ts) & (classified["createdate"] < prior_end_ts)
].copy()

# Filter submissions to the selected period
subs_in_period = submissions_df[
    (submissions_df["submitted_at"] >= start_ts)
    & (submissions_df["submitted_at"] < end_ts)
].copy() if not submissions_df.empty else pd.DataFrame()

subs_in_prior = submissions_df[
    (submissions_df["submitted_at"] >= prior_start_ts)
    & (submissions_df["submitted_at"] < prior_end_ts)
].copy() if not submissions_df.empty else pd.DataFrame()

# Build email → lead_status lookup (lowercase email keys)
classified_with_email = classified.copy()
classified_with_email["email_lc"] = classified_with_email["email"].str.lower()
email_to_status = classified_with_email.set_index("email_lc")["lead_status"].to_dict()
email_to_category = classified_with_email.set_index("email_lc")["lead_category"].to_dict()


def _per_form_aggregate(subs: pd.DataFrame, forms: pd.DataFrame) -> pd.DataFrame:
    """For each form: submissions, leads (among unique submitters), lead %."""
    if subs.empty or forms.empty:
        return pd.DataFrame(columns=["Form", "Subs", "Leads", "%"])
    rows = []
    name_lookup = forms.set_index("id")["name"].to_dict()
    for fid, group in subs.groupby("form_id"):
        people = group["contact_email"].dropna().unique()
        n_people = len(people)
        n_subs = len(group)
        n_leads = sum(1 for e in people if email_to_status.get(e) == "lead")
        # Strip year prefix to save horizontal space.
        raw_name = name_lookup.get(fid, fid)
        display_name = raw_name
        for prefix in ("2026 ", "2025 ", "2024 "):
            if display_name.startswith(prefix):
                display_name = display_name[len(prefix):]
                break
        rows.append({
            "Form": display_name,
            "Subs": n_subs,
            "Leads": n_leads,
            "%": (n_leads / n_people * 100) if n_people else 0.0,
        })
    return pd.DataFrame(rows).sort_values("Subs", ascending=False)


def _spotlight_for(subs: pd.DataFrame, forms: pd.DataFrame, pattern: str) -> dict:
    """Aggregate submissions across forms whose name matches the pattern (case-insensitive substring)."""
    if forms.empty or subs.empty:
        return {"people": 0, "submissions": 0, "leads": 0, "form_count": 0}
    matched_ids = forms[forms["name"].str.contains(pattern, case=False, na=False)]["id"].tolist()
    if not matched_ids:
        return {"people": 0, "submissions": 0, "leads": 0, "form_count": 0}
    relevant = subs[subs["form_id"].isin(matched_ids)]
    people = relevant["contact_email"].dropna().unique()
    leads = sum(1 for e in people if email_to_status.get(e) == "lead")
    return {
        "people": len(people),
        "submissions": len(relevant),
        "leads": leads,
        "form_count": len(matched_ids),
    }


# ---- Headline KPIs (compact row of 5) ----
n_contacts = len(in_period)
n_leads = int((in_period["lead_status"] == "lead").sum())
n_unclassified = int((in_period["lead_status"] == "unclassified").sum())
n_non_lead = int((in_period["lead_status"] == "non_lead").sum())
n_submissions = int(len(subs_in_period))
n_unique_fillers = int(subs_in_period["contact_email"].dropna().nunique()) if not subs_in_period.empty else 0

# Per-category lead counts for the priority segments
n_ria = int((in_period["lead_category"] == "RIA").sum())
n_bd = int((in_period["lead_category"] == "Broker-Dealer").sum())

# Prior-period counterparts
p_contacts = len(in_prior_period)
p_leads = int((in_prior_period["lead_status"] == "lead").sum())
p_unclassified = int((in_prior_period["lead_status"] == "unclassified").sum())
p_non_lead = int((in_prior_period["lead_status"] == "non_lead").sum())
p_submissions = int(len(subs_in_prior))
p_ria = int((in_prior_period["lead_category"] == "RIA").sum())
p_bd = int((in_prior_period["lead_category"] == "Broker-Dealer").sum())

# Load deals + tasks for the deal-pipeline KPIs
deals_df = load_deals()
tasks_df = load_tasks()

# Compute open deals and tasks due this week
CLOSED_STAGES = {"closedwon", "closedlost"}
if not deals_df.empty and "dealstage" in deals_df.columns:
    open_deals = deals_df[~deals_df["dealstage"].fillna("").str.lower().isin(CLOSED_STAGES)].copy()
else:
    open_deals = pd.DataFrame()

week_cutoff = now_ts + pd.Timedelta(days=7)
if not tasks_df.empty and "due_at" in tasks_df.columns:
    tasks_due_week = tasks_df[
        (tasks_df["due_at"] >= now_ts)
        & (tasks_df["due_at"] < week_cutoff)
        & (~tasks_df["status"].fillna("").str.upper().isin({"COMPLETED", "DEFERRED"}))
    ].copy()
else:
    tasks_due_week = pd.DataFrame()

# Open deals that have at least one task due this week
if not open_deals.empty and not tasks_due_week.empty:
    # Build a set of deal IDs referenced by due tasks
    due_deal_ids = set()
    for ids_str in tasks_due_week["associated_deal_ids"].dropna():
        if ids_str:
            for did in str(ids_str).split(","):
                did = did.strip()
                if did:
                    due_deal_ids.add(did)
    open_deals_with_due_tasks = open_deals[open_deals["id"].astype(str).isin(due_deal_ids)]
    n_deals_due = len(open_deals_with_due_tasks)
else:
    n_deals_due = 0

n_tasks_due = len(tasks_due_week)


# If the prior comparison window falls before the trusted-data date, deltas are
# meaningless (we'd be comparing post-HubSpot to pre-HubSpot).
trusted_from_ts = pd.Timestamp(trusted_from, tz="UTC")
prior_comparison_trustworthy = prior_start_ts >= trusted_from_ts


def _fmt_delta(current: float, prior: float) -> str | None:
    """Return a signed percent-change string, e.g. '+12%' or '-7%'. Streamlit colors based on sign."""
    if not prior_comparison_trustworthy:
        return None  # Hide delta entirely when prior period is pre-HubSpot.
    if prior == 0:
        if current == 0:
            return "0% vs prior"
        return "new (vs 0)"
    pct = (current - prior) / prior * 100
    return f"{pct:+.0f}% vs prior period"


# ---- CRO-style aggregates: Hot Accounts ----
HEAT_WINDOW_DAYS = 30  # rolling window for "engaged recently"
heat_cutoff = now_ts - pd.Timedelta(days=HEAT_WINDOW_DAYS)

# Outreach-eligible firm types for the consulting business. Tighter than the broader
# LEAD_CATEGORIES list — Hot Accounts is for "who do we call THIS week," not lead counts.
# Mapping uses Company.firm_type internal values.
ICP_FIRM_TYPES_FOR_OUTREACH = {
    "ria", "brokerdealer", "banktrust", "custodian", "asset_manager", "fintech",
}

# Domains explicitly known not to be prospects — applied even if firm_type isn't set.
# Edit as you discover new internal/vendor/partner domains.
EXCLUDED_DOMAINS = {
    "ezragroup.com",      # Own company
    "grimmandco.org",     # Fractional CMO firm
    "streetcredpr.com",   # PR firm
    "mochadesigns.co",    # External development partner
}

# Specific email addresses to exclude even when the domain is shared (e.g., gmail.com).
# Use for personal addresses of vendors/partners/internal people who aren't prospects.
EXCLUDED_EMAILS = {
    "grimm.marcus@gmail.com",  # Fractional CMO (personal email)
}


def _domain_of(email: str | None) -> str | None:
    if not email or "@" not in str(email):
        return None
    return str(email).split("@")[-1].lower().strip()


def _is_excluded(email: str | None) -> bool:
    """True if this contact is a known internal/vendor/partner — not a prospect."""
    if not email:
        return False
    e = str(email).lower().strip()
    if e in EXCLUDED_EMAILS:
        return True
    dom = _domain_of(e)
    return dom in EXCLUDED_DOMAINS if dom else False


def _build_hot_accounts() -> pd.DataFrame:
    """Aggregate engagement signals per company over the last HEAT_WINDOW_DAYS.

    Filters applied (CRO directive):
      - Only companies with firm_type in ICP_FIRM_TYPES_FOR_OUTREACH
      - Excluded domains (own company, CMO, PR partners, etc.) dropped regardless of firm_type
    """
    if submissions_df.empty or classified.empty:
        return pd.DataFrame()

    recent_subs = submissions_df[submissions_df["submitted_at"] >= heat_cutoff].copy()
    if recent_subs.empty:
        return pd.DataFrame()

    # Drop submissions from excluded contacts/domains right at the source
    recent_subs = recent_subs[~recent_subs["contact_email"].apply(_is_excluded)]
    if recent_subs.empty:
        return pd.DataFrame()

    # Join submissions to contacts → companies via email
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

    # Aggregate per company
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

    # Annotate with company info (tolerate NaN/None from SQLite/pandas)
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

    # CRO directive filters
    agg = agg[~agg["company_domain"].fillna("").str.lower().isin(EXCLUDED_DOMAINS)]
    agg = agg[agg["firm_type"].isin(ICP_FIRM_TYPES_FOR_OUTREACH)]

    # Filter: 2+ contacts engaging
    agg = agg[agg["contacts_engaged"] >= 2].copy()
    if agg.empty:
        return agg

    agg["heat_score"] = agg["contacts_engaged"] * agg["submissions"]
    agg["days_since"] = (now_ts - agg["last_activity"]).dt.days
    return agg.sort_values(["heat_score", "last_activity"], ascending=[False, False])


hot_accounts_df = _build_hot_accounts()


def _latest_activity_ts(row) -> pd.Timestamp | None:
    """Find the most recent activity timestamp across the activity-tracking fields we pull."""
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
    """Stalled = ICP contact who WAS in active pipeline but went quiet 45+ days.

    'In active pipeline' = at least one of:
      - num_associated_deals >= 1 (deal opened)
      - notes_last_contacted is set (sales touched them at some point)
      - num_conversion_events >= 2 (multi-touch via forms)
    """
    if classified.empty:
        return pd.DataFrame()
    df = classified[classified["lead_status"] == "lead"].copy()
    # Drop internal/vendor/partner contacts
    df = df[~df["email"].apply(_is_excluded)]
    if df.empty:
        return df

    # Must have prior pipeline activity
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
    """Multi-touch warm = ICP, 3+ conversions, zero associated deals."""
    if classified.empty:
        return pd.DataFrame()
    df = classified[classified["lead_status"] == "lead"].copy()
    # Drop internal/vendor/partner contacts
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


stalled_leads_df = _build_stalled_leads()
multi_touch_df = _build_multi_touch_warm()


# ---- Signals: auto-generated alerts ----
PERSONAL_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com",
    "aol.com", "me.com", "comcast.net", "live.com", "mail.com", "msn.com",
    "protonmail.com", "ymail.com", "verizon.net", "att.net", "sbcglobal.net",
}


def _domain_from_email(email):
    if email is None or pd.isna(email) or "@" not in str(email):
        return None
    return str(email).split("@")[-1].lower().strip()


def _strip_year(name: str) -> str:
    for prefix in ("2026 ", "2025 ", "2024 "):
        if name.startswith(prefix):
            return name[len(prefix):]
    return name


def _date_recent(ts_str, days: int) -> bool:
    """True if the ISO timestamp is within `days` of now."""
    if not ts_str or pd.isna(ts_str):
        return False
    try:
        ts = pd.Timestamp(ts_str)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        return (now_ts - ts).days <= days
    except (ValueError, TypeError):
        return False


def _generate_cro_signals() -> list[dict]:
    """Sales/marketing alerts: deal-impact, source health, pipeline velocity.

    These take precedence over classification noise.
    """
    sigs = []

    # 1. 🔥 Hot accounts (3+ contacts engaging) — top deal-impact signal
    if not hot_accounts_df.empty:
        very_hot = hot_accounts_df[hot_accounts_df["contacts_engaged"] >= 3]
        for _, row in very_hot.head(5).iterrows():
            cname = row.get("company_name") or row.get("company_domain") or row["company_id"]
            sigs.append({
                "key": f"hot_account:{row['company_id']}",
                "severity": "critical", "icon": "🔥",
                "title": f"Hot account: {cname}",
                "detail": f"{int(row['contacts_engaged'])} distinct contacts, {int(row['submissions'])} engagements in last {HEAT_WINDOW_DAYS}d. "
                          f"Last touch {int(row['days_since'])}d ago. firm_type: {row.get('firm_type') or 'unset'}. "
                          f"Book a discovery call before this cools.",
            })

    # 2. ⏰ Stalled leads — ICP contacts who WERE in active pipeline but went quiet
    # Tightened: must have evidence of active engagement (deal opened OR sales-touched OR multiple form fills)
    # so we don't flag pre-HubSpot bulk imports as "stalled."
    stalled_df = _build_stalled_leads()
    if len(stalled_df) >= 3:
        sigs.append({
            "key": "stalled_leads_bulk",
            "severity": "critical", "icon": "⏰",
            "title": f"{len(stalled_df)} stalled leads",
            "detail": f"{len(stalled_df)} ICP contacts with prior engagement (deal opened, sales contact, or multi-touch) "
                      f"but no activity in 45+ days. See the list below the signals.",
        })

    # 3. 💎 Multi-touch warm contacts — high engagement, no deal yet
    if len(multi_touch_df) >= 1:
        sigs.append({
            "key": "multi_touch_warm_bulk",
            "severity": "warning", "icon": "💎",
            "title": f"{len(multi_touch_df)} multi-touch warm leads",
            "detail": f"{len(multi_touch_df)} ICP contacts with 3+ form fills and zero deals. List below — open each in HubSpot to start the conversation.",
        })

    # 4. 📉 Pipeline drought — rolling 4-week lead trend
    if not classified.empty and prior_comparison_trustworthy:
        four_weeks_ago = now_ts - pd.Timedelta(days=28)
        eight_weeks_ago = now_ts - pd.Timedelta(days=56)
        leads_in = classified[classified["lead_status"] == "lead"].copy()
        recent_leads = leads_in[(leads_in["createdate"] >= four_weeks_ago) & (leads_in["createdate"] <= now_ts)]
        prior_leads_4w = leads_in[(leads_in["createdate"] >= eight_weeks_ago) & (leads_in["createdate"] < four_weeks_ago)]
        if len(prior_leads_4w) >= 10 and len(recent_leads) < len(prior_leads_4w) * 0.7:
            drop = (len(recent_leads) - len(prior_leads_4w)) / len(prior_leads_4w) * 100
            sigs.append({
                "key": "pipeline_drought",
                "severity": "critical", "icon": "📉",
                "title": f"Lead run-rate down {drop:+.0f}% (4-week trend)",
                "detail": f"{len(recent_leads)} leads in last 28d vs. {len(prior_leads_4w)} in the 28d before. "
                          f"At this rate you'll miss target — investigate top sources.",
            })

    return sigs


def _generate_signals() -> list[dict]:
    """Generate signals. Each has a stable `key` so dismissals persist day-to-day.

    Signals that compare prior period are skipped when the prior period falls
    before the trusted-data date.
    """
    sigs = []
    form_names = forms_df.set_index("id")["name"].to_dict() if not forms_df.empty else {}

    # Signals that REQUIRE a trustworthy prior period
    if prior_comparison_trustworthy:
        # 1. Backlog growing materially
        if p_unclassified >= 20 and n_unclassified > p_unclassified * 1.1:
            growth = (n_unclassified - p_unclassified) / p_unclassified * 100
            sigs.append({
                "key": "backlog_growing",
                "severity": "critical", "icon": "🚨",
                "title": f"Backlog grew {growth:+.0f}%",
                "detail": f"{n_unclassified:,} unclassified now vs. {p_unclassified:,} prior ({n_unclassified - p_unclassified:+,} net). "
                          f"Classification debt is compounding — see Backlog page.",
            })

        # 2. Lead count dropped sharply
        if p_leads >= 10 and n_leads < p_leads * 0.75:
            drop = (n_leads - p_leads) / p_leads * 100
            sigs.append({
                "key": "leads_dropped",
                "severity": "critical", "icon": "🚨",
                "title": f"Leads dropped {drop:+.0f}%",
                "detail": f"{n_leads} this period vs. {p_leads} prior. Investigate source mix and form performance below.",
            })

        # 3. Forms that went dark
        if not subs_in_prior.empty:
            prior_counts = subs_in_prior.groupby("form_id").size().to_dict()
            cur_counts = subs_in_period.groupby("form_id").size().to_dict() if not subs_in_period.empty else {}
            for fid, prior_n in prior_counts.items():
                if prior_n >= 3 and cur_counts.get(fid, 0) == 0:
                    name = _strip_year(form_names.get(fid, fid))
                    sigs.append({
                        "key": f"form_dark:{fid}",
                        "severity": "warning", "icon": "⚠️",
                        "title": f"Form went dark: {name}",
                        "detail": f"{prior_n} submissions prior period, 0 now. Page broken, campaign ended, or audience tapped out?",
                    })

        # 4. Forms newly active (debut)
        if not subs_in_period.empty:
            prior_counts = subs_in_prior.groupby("form_id").size().to_dict() if not subs_in_prior.empty else {}
            cur_counts = subs_in_period.groupby("form_id").size().to_dict()
            for fid, cur_n in cur_counts.items():
                if cur_n >= 3 and prior_counts.get(fid, 0) == 0:
                    name = _strip_year(form_names.get(fid, fid))
                    sigs.append({
                        "key": f"form_debut:{fid}",
                        "severity": "info", "icon": "🆕",
                        "title": f"Form debut: {name}",
                        "detail": f"First {cur_n} submissions this period — form had zero fills in the prior period. Brand-new traffic source. Check lead quality on Forms page.",
                    })

        # 5. Lead-rate (audience quality) jumps or drops
        if not subs_in_period.empty and not subs_in_prior.empty:
            cur_per_form = subs_in_period.groupby("form_id")
            prior_per_form = subs_in_prior.groupby("form_id")
            for fid in set(cur_per_form.groups) & set(prior_per_form.groups):
                cur_emails = cur_per_form.get_group(fid)["contact_email"].dropna().unique()
                prior_emails = prior_per_form.get_group(fid)["contact_email"].dropna().unique()
                if len(cur_emails) < 5 or len(prior_emails) < 5:
                    continue
                cur_rate = sum(1 for e in cur_emails if email_to_status.get(e) == "lead") / len(cur_emails) * 100
                prior_rate = sum(1 for e in prior_emails if email_to_status.get(e) == "lead") / len(prior_emails) * 100
                diff_pp = cur_rate - prior_rate
                name = _strip_year(form_names.get(fid, fid))
                if diff_pp >= 20:
                    sigs.append({
                        "key": f"rate_up:{fid}",
                        "severity": "info", "icon": "📈",
                        "title": f"Audience quality up: {name}",
                        "detail": f"Same form, better fits — {cur_rate:.0f}% ICP leads now vs. {prior_rate:.0f}% prior (+{diff_pp:.0f}pp). Worth amplifying.",
                    })
                elif diff_pp <= -20:
                    sigs.append({
                        "key": f"rate_down:{fid}",
                        "severity": "warning", "icon": "📉",
                        "title": f"Audience quality down: {name}",
                        "detail": f"Same form, worse fits — {cur_rate:.0f}% ICP leads now vs. {prior_rate:.0f}% prior ({diff_pp:.0f}pp). Wrong channel/copy?",
                    })

    # Signals that DON'T need a prior period — always run
    # 6. Unclassified domain spikes
    unclass = in_period[in_period["lead_status"] == "unclassified"].copy()
    if not unclass.empty:
        unclass["_domain"] = unclass["email"].apply(_domain_from_email)
        domain_counts = unclass[unclass["_domain"].notna()]["_domain"].value_counts()
        spike_count = 0
        for domain, count in domain_counts.items():
            if count >= 5 and domain not in PERSONAL_DOMAINS:
                sigs.append({
                    "key": f"domain_spike:{domain}",
                    "severity": "info", "icon": "💡",
                    "title": f"Classify once, count {count}: {domain}",
                    "detail": f"{count} unclassified contacts share this domain — set Company.firm_type once and all {count} become leads.",
                })
                spike_count += 1
                if spike_count >= 5:  # cap noise
                    break

    return sigs


_severity_styles = {
    "critical": "background:#fde2e2; border-left:4px solid #d9534f;",
    "warning":  "background:#fff3cd; border-left:4px solid #ffc107;",
    "info":     "background:#e3f2fd; border-left:4px solid #2196f3;",
}
_severity_order = {"critical": 0, "warning": 1, "info": 2}

today_iso = datetime.now(timezone.utc).date().isoformat()
dismissed_keys = db.active_dismissals(today_iso)


def _render_signals(sigs: list[dict], empty_message: str) -> None:
    sigs = [s for s in sigs if s["key"] not in dismissed_keys]
    sigs.sort(key=lambda s: _severity_order.get(s["severity"], 99))
    if not sigs:
        st.markdown(
            f"<div style='padding:0.4rem 0.7rem; margin-bottom:0.5rem; border-radius:4px; "
            f"background:#e8f5e9; border-left:4px solid #4caf50; font-size:0.85rem;'>"
            f"<b>✅</b> {empty_message}"
            f"</div>",
            unsafe_allow_html=True,
        )
        return
    for sig in sigs:
        style = _severity_styles.get(sig["severity"], "")
        row = st.columns([0.04, 0.96])
        with row[0]:
            if st.checkbox(
                "dismiss",
                key=f"sig_dismiss_{sig['key']}",
                label_visibility="collapsed",
                help="Dismiss until tomorrow. If the issue persists, the signal re-appears.",
            ):
                db.dismiss_signal(sig["key"], today_iso)
                st.rerun()
        with row[1]:
            st.markdown(
                f"<div style='padding:0.3rem 0.7rem; margin-bottom:0.2rem; border-radius:4px; "
                f"{style} font-size:0.85rem;'>"
                f"<b>{sig['icon']} {sig['title']}</b> — {sig['detail']}"
                f"</div>",
                unsafe_allow_html=True,
            )


# ---- Hot Accounts table — top of page, deal-impact view ----
st.markdown("**🔥 Hot Accounts** — ICP firms with 2+ contacts engaging in the last 30 days")
st.caption(
    "Filtered to firm_type ∈ RIA, Broker-Dealer, Bank/Trust, Custodian, Asset Manager, Fintech. "
    f"Internal/vendor domains excluded ({', '.join(sorted(EXCLUDED_DOMAINS))}). "
    f"Plus {len(EXCLUDED_EMAILS)} specific email(s) excluded."
)
if hot_accounts_df.empty:
    st.markdown(
        "<div style='padding:0.4rem 0.7rem; margin-bottom:0.5rem; border-radius:4px; "
        "background:#f5f5f5; border-left:4px solid #999; font-size:0.85rem;'>"
        "No ICP firms have 2+ engaged contacts in the last 30 days. "
        "Either classify more companies in HubSpot (so they qualify), or drive more form fills."
        "</div>",
        unsafe_allow_html=True,
    )
else:
    ha_display = hot_accounts_df.head(10)[[
        "company_name", "company_domain", "firm_type",
        "contacts_engaged", "submissions", "leads_count", "days_since",
    ]].rename(columns={
        "company_name": "Company",
        "company_domain": "Domain",
        "firm_type": "Firm type",
        "contacts_engaged": "Contacts",
        "submissions": "Engagements",
        "leads_count": "ICP",
        "days_since": "Days since",
    })
    st.dataframe(
        ha_display, use_container_width=True, hide_index=True, height=320,
        column_config={
            "Company": st.column_config.TextColumn(width="medium"),
            "Contacts": st.column_config.NumberColumn(width="small", help="Distinct contacts at this firm who submitted a form in last 30d"),
            "Engagements": st.column_config.NumberColumn(width="small", help="Total form submissions across all contacts at this firm in last 30d"),
            "ICP": st.column_config.NumberColumn(width="small", help="How many of those contacts have ICP firm_type set"),
            "Days since": st.column_config.NumberColumn(width="small", help="Days since the most recent engagement"),
        },
    )

# ---- CRO Signals (deal-impact and pipeline health) ----
header_cols = st.columns([0.85, 0.15])
header_cols[0].markdown("**🔔 Pipeline Signals**")
if not prior_comparison_trustworthy:
    header_cols[1].caption(f"Prior comparisons off (pre-{trusted_from})")

cro_sigs = _generate_cro_signals()
_render_signals(cro_sigs, "No deal-impact signals today. Pipeline looks steady.")


def _render_action_table(df: pd.DataFrame, columns_to_show: dict, height: int = 260) -> None:
    """Render an actionable table with HubSpot deep-link column."""
    company_lookup = (
        companies.set_index("id")[["name", "domain"]].to_dict("index") if not companies.empty else {}
    )
    df = df.copy()
    df["Company"] = df["company_id"].apply(
        lambda c: company_lookup.get(c, {}).get("name") if c else None
    )
    df["HubSpot"] = df["hubspot_url"] if "hubspot_url" in df.columns else None
    for col, label in columns_to_show.items():
        if col not in df.columns:
            df[col] = None
    display = df[list(columns_to_show.keys()) + ["Company", "HubSpot"]].rename(columns=columns_to_show)
    st.dataframe(
        display,
        use_container_width=True, hide_index=True, height=height,
        column_config={
            "Company": st.column_config.TextColumn(width="medium"),
            "HubSpot": st.column_config.LinkColumn("Open in HubSpot", display_text="Open ↗", width="small"),
        },
    )


# ---- Multi-touch warm leads list ----
if len(multi_touch_df) > 0:
    with st.expander(f"💎 Multi-touch warm leads ({len(multi_touch_df)}) — open in HubSpot to start the conversation", expanded=True):
        _render_action_table(
            multi_touch_df,
            columns_to_show={
                "email": "Email",
                "firm_type": "Firm type",
                "_convs": "Conversions",
                "_last_activity": "Last activity",
                "first_conversion_event_name": "First form",
                "recent_conversion_event_name": "Recent form",
            },
            height=min(80 + len(multi_touch_df) * 35, 320),
        )

# ---- Stalled leads list ----
if len(stalled_leads_df) > 0:
    with st.expander(f"⏰ Stalled leads ({len(stalled_leads_df)}) — last activity 45+ days ago", expanded=False):
        _render_action_table(
            stalled_leads_df,
            columns_to_show={
                "email": "Email",
                "firm_type": "Firm type",
                "_days_quiet": "Days quiet",
                "_last_activity": "Last activity",
                "_deals": "Deals",
                "hs_lead_status": "Status",
            },
            height=min(80 + len(stalled_leads_df) * 35, 380),
        )

# ---- Data hygiene (classification noise, demoted) ----
with st.expander("🧹 Data hygiene signals (classification, low priority)"):
    hygiene_sigs = _generate_signals()
    _render_signals(hygiene_sigs, "No classification issues right now.")

with st.expander("What each signal means"):
    st.markdown(
        """
**Pipeline Signals (top — deal-impact, CRO-grade)**
- **🔥 Hot account** — 3+ distinct contacts at one ICP firm engaged in last 30 days. Book a discovery call before the moment passes.
- **⏰ Stalled leads** — ICP contacts who had prior pipeline activity (deal opened, sales touch, or 2+ form fills) and have been quiet 45+ days. The list appears below.
- **💎 Multi-touch warm** — ICP contacts with 3+ form fills and zero associated deals. Conversations someone should have started already. The list with HubSpot links appears below.
- **📉 Pipeline drought** — rolling 4-week lead count down >30% vs. prior 4 weeks. Time to investigate.

**Data hygiene (low priority, in expander)**
- 💡 Classify-once domain spike — ≥5 unclassified contacts share a domain. One HubSpot edit, batch of N classified.
- 🚨 Backlog grew, 🚨 Leads dropped, ⚠️ Form went dark, 🆕 Form debut, 📈/📉 Audience quality — period-over-period anomalies suppressed when prior period is pre-HubSpot.
        """
    )


c1, c2, c3, c4, c5 = st.columns(5)
c1.metric(
    "RIA leads",
    f"{n_ria:,}",
    delta=_fmt_delta(n_ria, p_ria),
    help="ICP RIA contacts created in the selected period. Priority segment.",
)
c2.metric(
    "Broker-Dealer leads",
    f"{n_bd:,}",
    delta=_fmt_delta(n_bd, p_bd),
    help="ICP Broker-Dealer contacts created in the selected period. Priority segment.",
)
c3.metric(
    "Total leads",
    f"{n_leads:,}",
    delta=_fmt_delta(n_leads, p_leads),
    help="All ICP contacts in the selected period (all 7 firm-type categories).",
)
c4.metric(
    "Open deals · tasks due 7d",
    f"{n_deals_due:,}",
    delta=f"of {len(open_deals)} open" if not open_deals.empty else "no deal data",
    delta_color="off",
    help="Open deals that have at least one task due in the next 7 days. Requires crm.objects.deals.read scope on the HubSpot Service Key.",
)
c5.metric(
    "Tasks due 7d",
    f"{n_tasks_due:,}",
    help="Total tasks due in the next 7 days (across all deals). Requires crm.objects.tasks.read scope.",
)

# Secondary operational row (less critical, kept for visibility)
with st.expander("📋 Operational counts (backlog, total contacts, non-ICP, form fills)"):
    sc1, sc2, sc3, sc4 = st.columns(4)
    sc1.metric("Unclassified backlog", f"{n_unclassified:,}",
               delta=_fmt_delta(n_unclassified, p_unclassified), delta_color="inverse",
               help="Contacts with no firm_type set on either record. See Backlog page.")
    sc2.metric("Total contacts", f"{n_contacts:,}", delta=_fmt_delta(n_contacts, p_contacts))
    sc3.metric("Non-ICP", f"{n_non_lead:,}", delta=_fmt_delta(n_non_lead, p_non_lead), delta_color="off")
    sc4.metric("Form submissions", f"{n_submissions:,}", delta=_fmt_delta(n_submissions, p_submissions),
               help=f"{n_unique_fillers} unique people across {len(forms_df)} forms.")

# ---- Featured form spotlights (real form-submission data) ----
ai = _spotlight_for(subs_in_period, forms_df, "ai notetaker")
env = _spotlight_for(subs_in_period, forms_df, "envestnet")
nit = _spotlight_for(subs_in_period, forms_df, "nitrogen")

ai_p = _spotlight_for(subs_in_prior, forms_df, "ai notetaker")
env_p = _spotlight_for(subs_in_prior, forms_df, "envestnet")
nit_p = _spotlight_for(subs_in_prior, forms_df, "nitrogen")

f1, f2, f3 = st.columns(3)
f1.metric(
    "AI Notetakers — people",
    f"{ai['people']:,}",
    delta=_fmt_delta(ai['people'], ai_p['people']),
    help=f"{ai['submissions']} submissions · {ai['leads']} leads · matched {ai['form_count']} form(s) with 'AI Notetaker' in the name. People = unique fillers in period.",
)
f2.metric(
    "Envestnet case study — people",
    f"{env['people']:,}",
    delta=_fmt_delta(env['people'], env_p['people']),
    help=f"{env['submissions']} submissions · {env['leads']} leads · matched {env['form_count']} form(s) with 'Envestnet' in the name.",
)
f3.metric(
    "Nitrogen case study — people",
    f"{nit['people']:,}",
    delta=_fmt_delta(nit['people'], nit_p['people']),
    help=f"{nit['submissions']} submissions · {nit['leads']} leads · matched {nit['form_count']} form(s) with 'Nitrogen' in the name.",
)

# ---- LinkedIn KPIs (period-bounded) ----
linkedin_posts = load_linkedin_posts()
if not linkedin_posts.empty:
    li_in_period = linkedin_posts[
        (linkedin_posts["published_at"] >= start_ts)
        & (linkedin_posts["published_at"] < end_ts)
    ].copy()
    li_in_prior = linkedin_posts[
        (linkedin_posts["published_at"] >= prior_start_ts)
        & (linkedin_posts["published_at"] < prior_end_ts)
    ].copy()

    def _sum(df, col):
        return int(df[col].fillna(0).sum()) if col in df.columns and not df.empty else 0

    li_posts = len(li_in_period)
    li_comments = _sum(li_in_period, "comment_count")
    li_reactions = _sum(li_in_period, "reaction_count")
    li_impressions = _sum(li_in_period, "impression_count")
    li_followers = _sum(li_in_period, "followers_gained_count")

    li_p_posts = len(li_in_prior)
    li_p_comments = _sum(li_in_prior, "comment_count")
    li_p_reactions = _sum(li_in_prior, "reaction_count")
    li_p_impressions = _sum(li_in_prior, "impression_count")
    li_p_followers = _sum(li_in_prior, "followers_gained_count")

    st.markdown(f"**💼 LinkedIn — {start_date.strftime('%b %d')} – {end_date.strftime('%b %d, %Y')}**")
    li1, li2, li3, li4, li5 = st.columns(5)
    li1.metric("Posts", f"{li_posts:,}", delta=_fmt_delta(li_posts, li_p_posts))
    li2.metric("Impressions", f"{li_impressions:,}", delta=_fmt_delta(li_impressions, li_p_impressions))
    li3.metric("Comments", f"{li_comments:,}", delta=_fmt_delta(li_comments, li_p_comments),
               help="Comments are the highest-signal engagement metric for B2B outreach. See LinkedIn page for per-post detail.")
    li4.metric("Reactions", f"{li_reactions:,}", delta=_fmt_delta(li_reactions, li_p_reactions))
    li5.metric("Followers gained", f"{li_followers:,}", delta=_fmt_delta(li_followers, li_p_followers))


# ---- Chart (left half) + Forms table (right half) ----
left, right = st.columns([1, 1])

with left:
    st.markdown("**Leads by week**")
    if n_leads > 0:
        leads_only = in_period[in_period["lead_status"] == "lead"].copy()
        leads_only["week"] = leads_only["createdate"].dt.to_period("W").dt.start_time
        weekly = (
            leads_only.groupby(["week", "lead_category"])
            .size()
            .reset_index(name="leads")
        )
        fig = px.bar(
            weekly, x="week", y="leads", color="lead_category",
            category_orders={"lead_category": LEAD_CATEGORIES},
        )
        fig.update_layout(
            height=380,
            margin=dict(l=10, r=10, t=10, b=10),
            xaxis_title=None, yaxis_title="Leads",
            legend_title=None,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No leads in the selected period.")

with right:
    st.markdown("**Forms — by submissions**")
    forms_table = _per_form_aggregate(subs_in_period, forms_df)
    if forms_table.empty:
        st.info("No form submissions in the selected period.")
    else:
        st.dataframe(
            forms_table,
            use_container_width=True,
            hide_index=True,
            height=380,
            column_config={
                "Form": st.column_config.TextColumn(width="medium"),
                "Subs": st.column_config.NumberColumn(width="small"),
                "Leads": st.column_config.NumberColumn(width="small"),
                "%": st.column_config.NumberColumn(format="%.0f%%", width="small"),
            },
        )

# ---- Leads by source (full width, smaller) ----
st.markdown("**Leads by HubSpot original source**")
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
    fig.update_layout(
        height=260,
        margin=dict(l=10, r=10, t=10, b=10),
        xaxis_title=None, yaxis_title="Leads",
    )
    st.plotly_chart(fig, use_container_width=True)

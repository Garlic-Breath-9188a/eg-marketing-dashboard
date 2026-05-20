"""Forms drill-down page — pick a form, see who filled it out, export the leads."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from classify.leads import classify_dataframe
from store import db

st.set_page_config(page_title="Forms — EG Marketing Dashboard", page_icon="📨", layout="wide")

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


@st.cache_data(ttl=3600)
def load_forms() -> pd.DataFrame:
    db.init_db()
    with db.connect() as conn:
        return pd.read_sql("SELECT * FROM forms", conn)


@st.cache_data(ttl=3600)
def load_submissions() -> pd.DataFrame:
    db.init_db()
    with db.connect() as conn:
        df = pd.read_sql("SELECT * FROM form_submissions", conn)
    if "submitted_at" in df.columns:
        df["submitted_at"] = pd.to_datetime(df["submitted_at"], errors="coerce", utc=True)
    return df


st.title("📨 Forms drill-down")
st.caption(
    "Pick a form to see everyone who filled it out, with lead classification. "
    "Filter to leads, then download the CSV to import into HubSpot as a static list for a drip campaign."
)

contacts = load_contacts()
companies = load_companies()
forms = load_forms()
submissions = load_submissions()

if forms.empty or submissions.empty:
    st.warning("No form data in cache. Refresh from HubSpot on the Overview page.")
    st.stop()

# Filter out Old Site forms (same global rule as Overview).
forms = forms[~forms["name"].str.contains("Old Site", case=False, na=False)].copy()

# Build form selector — default to the form with the most submissions.
subs_by_form = submissions.groupby("form_id").size().reset_index(name="n")
forms_with_counts = forms.merge(subs_by_form, left_on="id", right_on="form_id", how="left").fillna({"n": 0})
forms_with_counts["label"] = forms_with_counts.apply(
    lambda r: f"{r['name']} ({int(r['n'])} subs)", axis=1
)
forms_with_counts = forms_with_counts.sort_values("n", ascending=False)

selected_label = st.selectbox(
    "Form",
    options=forms_with_counts["label"].tolist(),
    index=0,
)
selected_form_id = forms_with_counts[forms_with_counts["label"] == selected_label].iloc[0]["id"]
selected_form_name = forms_with_counts[forms_with_counts["label"] == selected_label].iloc[0]["name"]

# Build the drill-down: submissions for this form joined with classified contacts.
classified = classify_dataframe(contacts, companies)
classified["email_lc"] = classified["email"].str.lower()
company_lookup = (
    companies.set_index("id")[["name", "domain"]].to_dict("index") if not companies.empty else {}
)

form_subs = submissions[submissions["form_id"] == selected_form_id].copy()
form_subs["contact_email"] = form_subs["contact_email"].str.lower()

# Join to contacts on email
merged = form_subs.merge(
    classified[["email_lc", "id", "firm_type", "lead_status", "lead_category",
                "classification_source", "company_id", "lifecyclestage"]],
    left_on="contact_email", right_on="email_lc", how="left",
)
merged["company_name"] = merged["company_id"].apply(
    lambda cid: company_lookup.get(cid, {}).get("name") if pd.notna(cid) and cid else None
)
merged["company_domain"] = merged["company_id"].apply(
    lambda cid: company_lookup.get(cid, {}).get("domain") if pd.notna(cid) and cid else None
)

# Collapse duplicate submissions per person — keep most recent
merged = merged.sort_values("submitted_at", ascending=False).drop_duplicates(subset=["contact_email"])

# KPIs
n_subs_total = len(form_subs)
n_people = merged["contact_email"].nunique()
n_leads = int((merged["lead_status"] == "lead").sum())
n_unclassified = int((merged["lead_status"] == "unclassified").sum())

c1, c2, c3, c4 = st.columns(4)
c1.metric("Submissions", f"{n_subs_total:,}")
c2.metric("Unique people", f"{n_people:,}")
c3.metric("Leads", f"{n_leads:,}")
c4.metric("Unclassified", f"{n_unclassified:,}",
          help="People whose firm_type isn't set — they may be leads in disguise.")

# Filter
status_filter = st.radio(
    "Show",
    options=["Leads only", "Leads + Unclassified", "Everyone"],
    horizontal=True,
    index=0,
)
if status_filter == "Leads only":
    display = merged[merged["lead_status"] == "lead"].copy()
elif status_filter == "Leads + Unclassified":
    display = merged[merged["lead_status"].isin(["lead", "unclassified"])].copy()
else:
    display = merged.copy()

# Choose columns to show
display_cols = display[[
    "submitted_at", "contact_email", "company_name", "company_domain",
    "firm_type", "lead_category", "lead_status", "classification_source",
    "lifecyclestage",
]].rename(columns={
    "submitted_at": "Submitted",
    "contact_email": "Email",
    "company_name": "Company",
    "company_domain": "Domain",
    "firm_type": "Contact firm_type",
    "lead_category": "Lead category",
    "lead_status": "Status",
    "classification_source": "Source",
    "lifecyclestage": "Lifecycle",
}).sort_values("Submitted", ascending=False)

st.dataframe(display_cols, use_container_width=True, hide_index=True, height=460)

# CSV export — emails-first format optimized for HubSpot static list import.
csv_df = display[[
    "contact_email", "company_name", "company_domain",
    "firm_type", "lead_category", "lead_status", "submitted_at",
]].rename(columns={
    "contact_email": "Email",
    "company_name": "Company",
    "company_domain": "Domain",
    "firm_type": "Firm Type",
    "lead_category": "Lead Category",
    "lead_status": "Status",
    "submitted_at": "Submitted At",
})

csv_bytes = csv_df.to_csv(index=False).encode("utf-8")
safe_name = "".join(c if c.isalnum() else "_" for c in selected_form_name)[:60]
filename = f"{safe_name}_{status_filter.replace(' ', '_')}.csv"

st.download_button(
    "⬇️ Download CSV (for preview / audit / sharing)",
    data=csv_bytes,
    file_name=filename,
    mime="text/csv",
    type="primary",
    disabled=display.empty,
    help="The contacts are already in HubSpot — this CSV is for previewing the list before you build it, sharing with the CMO, or keeping an offline snapshot. You don't import it back into HubSpot.",
)

st.markdown("---")
with st.expander("How to set up a drip campaign in HubSpot (no CSV needed)"):
    st.markdown(
        """
The contacts already live in HubSpot — you don't need to import anything. Build the audience directly using HubSpot's list filters:

1. HubSpot → **Contacts → Lists → Create list → Active list**.
2. Name it (e.g., `Drip — AI Notetaker leads`).
3. Filter 1: `Form submission` → `has filled out form` → *this form*.
4. Filter 2 (AND): `Firm Type` → `is any of` → RIA, Fintech, Bank/Trust, Custodian, Insurance, Broker-Dealer, Asset Manager.
5. Save → list populates with the same people you see in the table above.
6. **Automation → Workflows → Create workflow → Contact-based**.
7. Trigger: **List membership → is member of → [your list]**.
8. Build drip steps (delay → send email → branch on opens/clicks).

Because it's an **active list**, new form fills that match the firm_type filter enroll into the drip automatically going forward.

The CSV download above is only for offline use (preview, audit, share with CMO, snapshot in time) — it's not part of the drip-campaign setup.
        """
    )

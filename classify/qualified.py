"""What counts as a qualified lead.

Until 2026-07-22 the answer was a single test: `firm_type` in
{RIA, Broker-Dealer, Fintech}. That made "qualified leads" an industry filter
over everyone in HubSpot rather than a measure of demand. Measured over 90 days:
**40% of the 200 had zero engagement** — no form fill, no email open, no logged
contact, no deal — and **55% were WealthTech vendors**, which is a different sale
from advisory consulting. It also counted people, so two contacts at one firm
read as two leads.

Three changes, all Craig's calls:

1. **Engagement is required.** A contact must have done *something*. The bar is
   deliberately low — any of form fill, email open/click, logged contact, or an
   associated deal — because a referral you met in person and logged a note
   against is as real as a form fill, and requiring a form would drop them.
2. **The two sales motions are separated.** Advisory (RIA + Broker-Dealer) is
   consulting work. Vendor (Fintech) is the WTIS integration-scoring programme
   that wealthtech companies pay for. Averaging them meant a strong vendor month
   and a strong advisory month looked identical.
3. **Accounts, not contacts.** A firm counts once however many people from it
   are in the database.

Unqualified contacts are not discarded — they are still classified and still
appear on the Leads and Backlog pages. They just no longer inflate a headline.
"""
from __future__ import annotations

import pandas as pd

# The two motions, kept apart on purpose.
ADVISORY_CATEGORIES = {"RIA", "Broker-Dealer"}
VENDOR_CATEGORIES = {"Fintech"}          # WealthTech vendors — the WTIS programme
QUALIFIED_CATEGORIES = ADVISORY_CATEGORIES | VENDOR_CATEGORIES

# Any one of these means the contact did something. Deliberately broad — the
# point is to exclude contacts with *no* interaction at all, not to rank them.
ENGAGEMENT_COLUMNS = (
    "first_conversion_event_name",   # filled in a form
    "recent_conversion_event_name",
    "hs_email_last_open_date",       # opened marketing email
    "hs_email_last_click_date",      # clicked in one
    "notes_last_contacted",          # someone logged an interaction
)
ENGAGEMENT_COUNTERS = (
    "num_conversion_events",
    "num_associated_deals",
)


def _present(df: pd.DataFrame, column: str) -> pd.Series:
    """True where `column` holds a real value. Absent column → all False."""
    if column not in df.columns:
        return pd.Series(False, index=df.index)
    series = df[column]
    return series.notna() & (series.astype(str).str.strip() != "")


def _positive(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series(False, index=df.index)
    return pd.to_numeric(df[column], errors="coerce").fillna(0) > 0


def has_engagement(contacts: pd.DataFrame) -> pd.Series:
    """True per row where the contact has any recorded interaction."""
    if contacts.empty:
        return pd.Series(dtype=bool)
    signal = pd.Series(False, index=contacts.index)
    for column in ENGAGEMENT_COLUMNS:
        signal |= _present(contacts, column)
    for column in ENGAGEMENT_COUNTERS:
        signal |= _positive(contacts, column)
    return signal


def account_key(contacts: pd.DataFrame) -> pd.Series:
    """Identify the firm a contact belongs to.

    `company_id` when HubSpot has one, else the email domain — otherwise every
    contact without an associated company would count as its own account and
    reintroduce the inflation this is meant to remove.
    """
    if contacts.empty:
        return pd.Series(dtype=str)
    company = contacts["company_id"] if "company_id" in contacts.columns else None
    domain = (
        contacts["email"].fillna("").str.lower().str.split("@").str[-1]
        if "email" in contacts.columns
        else pd.Series("", index=contacts.index)
    )
    if company is None:
        return "domain:" + domain
    return company.where(company.notna() & (company.astype(str) != ""),
                         "domain:" + domain).astype(str)


def qualify(contacts: pd.DataFrame) -> pd.DataFrame:
    """Add `engaged`, `account`, and `motion` columns.

    `motion` is "advisory", "vendor", or None. Requires `lead_category` from
    `classify.leads.classify_dataframe`.
    """
    if contacts.empty:
        return contacts.assign(engaged=False, account=None, motion=None)

    out = contacts.copy()
    out["engaged"] = has_engagement(out)
    out["account"] = account_key(out)
    out["motion"] = out["lead_category"].map(
        lambda c: "advisory" if c in ADVISORY_CATEGORIES
        else "vendor" if c in VENDOR_CATEGORIES
        else None
    )
    return out


def count_accounts(contacts: pd.DataFrame, motion: str | None = None) -> int:
    """Distinct engaged firms, optionally restricted to one motion."""
    if contacts.empty:
        return 0
    mask = contacts["engaged"] & contacts["motion"].notna()
    if motion is not None:
        mask &= contacts["motion"] == motion
    return int(contacts.loc[mask, "account"].nunique())


def qualified_contacts(contacts: pd.DataFrame, motion: str | None = None) -> pd.DataFrame:
    """The engaged contacts behind the account count — for the drill-down table."""
    if contacts.empty:
        return contacts
    mask = contacts["engaged"] & contacts["motion"].notna()
    if motion is not None:
        mask &= contacts["motion"] == motion
    return contacts[mask]

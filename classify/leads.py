"""Lead classification.

A contact is a LEAD if their Company.firm_type (preferred) or Contact.firm_type
(fallback) is in the Ezra Group ICP set.

Company.firm_type uses curated lowercase internal values (clean).
Contact.firm_type uses inconsistent mixed-case labels (messy — has duplicates
like 'Fintech' / 'Fintech Vendor' and a stray 'Invite team members'). We
normalize both into a single canonical set.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

# Canonical lead categories. Keys are the dashboard's display labels.
LEAD_CATEGORIES = [
    "RIA",
    "Fintech",
    "Bank/Trust",
    "Custodian",
    "Insurance",
    "Broker-Dealer",
    "Asset Manager",
]

# Company.firm_type values that map to each canonical category.
COMPANY_FIRM_TYPE_TO_CATEGORY = {
    "ria": "RIA",
    "fintech": "Fintech",
    "banktrust": "Bank/Trust",
    "custodian": "Custodian",
    "insurance": "Insurance",
    "brokerdealer": "Broker-Dealer",
    "asset_manager": "Asset Manager",
}

# Contact.firm_type label values (the property uses display labels as values).
# As of 2026-05: Press/PR added; Fintech Vendor and "Invite team members" cleaned up.
CONTACT_FIRM_TYPE_TO_CATEGORY = {
    "RIA": "RIA",
    "Fintech": "Fintech",
    "Fintech Vendor": "Fintech",   # kept for legacy contacts that may still carry this value
    "Bank/Trust": "Bank/Trust",
    "Custodian": "Custodian",
    "Insurance": "Insurance",
    "Broker-Dealer": "Broker-Dealer",
    "Asset Manager": "Asset Manager",
}

# Non-lead firm_type values we recognize (still classified, just not ICP).
COMPANY_NON_LEAD = {
    "consultant", "hedge_fund", "presspr", "private_equityvc",
    "recruiter", "research", "roboadvisor", "tamp", "other",
}
CONTACT_NON_LEAD = {
    "Consultant", "Other", "Research", "Private Equity/VC", "Recruiter",
    "Press/PR",
    "Invite team members",  # legacy stray UI value, kept for safety
}

Status = Literal["lead", "non_lead", "unclassified"]


@dataclass
class Classification:
    status: Status
    category: str | None  # Canonical category if lead, raw value if non_lead, None if unclassified
    source: str           # "company" | "contact" | "none"


def _norm(v) -> str:
    """Normalize a possibly-NaN/None/float value to a stripped string."""
    if v is None:
        return ""
    # pandas NaN is a float that isn't equal to itself
    if isinstance(v, float) and v != v:
        return ""
    return str(v).strip()


def classify_row(contact_firm_type, company_firm_type) -> Classification:
    # Prefer company classification.
    cft = _norm(company_firm_type).lower()
    if cft and cft in COMPANY_FIRM_TYPE_TO_CATEGORY:
        return Classification("lead", COMPANY_FIRM_TYPE_TO_CATEGORY[cft], "company")
    if cft and cft in COMPANY_NON_LEAD:
        return Classification("non_lead", cft, "company")

    # Fall back to contact-level (label-as-value).
    ct = _norm(contact_firm_type)
    if ct and ct in CONTACT_FIRM_TYPE_TO_CATEGORY:
        return Classification("lead", CONTACT_FIRM_TYPE_TO_CATEGORY[ct], "contact")
    if ct and ct in CONTACT_NON_LEAD:
        return Classification("non_lead", ct, "contact")

    return Classification("unclassified", None, "none")


def classify_dataframe(contacts: pd.DataFrame, companies: pd.DataFrame) -> pd.DataFrame:
    """Add `lead_status`, `lead_category`, `classification_source` columns to contacts.

    `contacts` must have at least: id, firm_type, company_id.
    `companies` must have at least: id, firm_type.
    """
    company_lookup = companies.set_index("id")["firm_type"].to_dict() if not companies.empty else {}

    def _classify(row):
        cft = row.get("firm_type")
        company_id = row.get("company_id")
        company_ft = company_lookup.get(company_id) if company_id else None
        c = classify_row(cft, company_ft)
        return pd.Series({
            "lead_status": c.status,
            "lead_category": c.category,
            "classification_source": c.source,
        })

    classified = contacts.apply(_classify, axis=1)
    return pd.concat([contacts.reset_index(drop=True), classified.reset_index(drop=True)], axis=1)

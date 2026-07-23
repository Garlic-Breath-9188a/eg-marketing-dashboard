"""Resolve the company a HubSpot task or deal belongs to.

Both the Tasks page and the Daily Briefing rendered "None" in the Company
column for every HubSpot row until 2026-07-23 — a literal `"Company": None`
placeholder that was never wired up. HubSpot showed a company on those records
(e.g. "Draft proposal" → Primus Capital), so the column was not merely empty,
it was wrong.

Resolution order, most direct first:

1. **Direct company association.** A task can be attached straight to a company
   with no deal and no contact — the Primus Capital case was exactly this, so
   any deal/contact-only strategy would still have shown nothing.
2. **Via its deal** — the deal's `primary_company_id`.
3. **Via its contact** — the contact's `company_id`.

Returns None when nothing resolves, which is then genuinely "no company".
"""
from __future__ import annotations

import pandas as pd


def _first_id(value) -> str | None:
    """First ID from a comma-separated association column."""
    if value is None or (isinstance(value, float) and value != value):
        return None
    first = str(value).split(",")[0].strip()
    return first or None


class CompanyResolver:
    """Maps HubSpot tasks and deals to a company name.

    Built once per page render from the cached frames — no API calls.
    """

    def __init__(
        self,
        companies: pd.DataFrame,
        deals: pd.DataFrame | None = None,
        contacts: pd.DataFrame | None = None,
    ):
        self._names: dict[str, str] = {}
        if companies is not None and not companies.empty:
            self._names = {
                str(r["id"]): r["name"]
                for _, r in companies.iterrows()
                if r.get("name")
            }

        # deal id -> company id
        self._deal_company: dict[str, str] = {}
        if deals is not None and not deals.empty and "primary_company_id" in deals.columns:
            self._deal_company = {
                str(r["id"]): str(r["primary_company_id"])
                for _, r in deals.iterrows()
                if r.get("primary_company_id")
            }

        # contact id -> company id
        self._contact_company: dict[str, str] = {}
        if contacts is not None and not contacts.empty and "company_id" in contacts.columns:
            self._contact_company = {
                str(r["id"]): str(r["company_id"])
                for _, r in contacts.iterrows()
                if r.get("company_id")
            }

    def name_for_company_id(self, company_id) -> str | None:
        cid = _first_id(company_id)
        return self._names.get(cid) if cid else None

    def for_task(self, task_row) -> str | None:
        """Company for a HubSpot task, trying direct → deal → contact."""
        direct = _first_id(task_row.get("associated_company_ids"))
        if direct and direct in self._names:
            return self._names[direct]

        deal_id = _first_id(task_row.get("associated_deal_ids"))
        if deal_id:
            cid = self._deal_company.get(deal_id)
            if cid and cid in self._names:
                return self._names[cid]

        contact_id = _first_id(task_row.get("associated_contact_ids"))
        if contact_id:
            cid = self._contact_company.get(contact_id)
            if cid and cid in self._names:
                return self._names[cid]

        return None

    def for_deal(self, deal_row) -> str | None:
        return self.name_for_company_id(deal_row.get("primary_company_id"))

"""Helpers shared across ingest modules.

Kept here rather than in `classify/` because these touch the database, and
`classify/` is deliberately pure logic with no storage dependency.
"""
from __future__ import annotations

from classify import companies as companies_cls
from store import db


def company_matcher() -> companies_cls.CompanyMatcher:
    """Build a company matcher from the names already in the HubSpot cache.

    Needs no API call — companies are ingested by `ingest.hubspot`.

    Two exclusion passes, because neither alone is sufficient:

    * **By domain** — Ezra Group and its CMO/PR/dev-partner firms are not
      prospects, so tagging a task with them carries no information.
    * **By name** — ~20% of cached companies have a NULL domain, so a junk
      record ("test") or a self-named one would survive the domain filter.
    """
    domains = companies_cls.EXCLUDED_COMPANY_DOMAINS
    placeholders = ", ".join("?" for _ in domains)
    sql = (
        "SELECT DISTINCT name FROM companies "
        "WHERE name IS NOT NULL AND name != '' "
        f"AND (domain IS NULL OR LOWER(TRIM(domain)) NOT IN ({placeholders}))"
    )
    with db.connect() as conn:
        rows = conn.execute(sql, tuple(sorted(domains))).fetchall()

    unmatchable = companies_cls.UNMATCHABLE_COMPANY_NAMES
    names = [
        r["name"] for r in rows
        if r["name"].strip().lower() not in unmatchable
    ]
    return companies_cls.CompanyMatcher(names)

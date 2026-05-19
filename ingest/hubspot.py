"""HubSpot ingest layer.

Pulls contacts (with first/recent conversion source + firm_type) and their
associated companies. Writes into the SQLite cache. Designed to be idempotent —
a full refresh re-upserts everything; a delta refresh filters by createdate or
lastmodifieddate.
"""
from __future__ import annotations

import time
from typing import Iterator

import requests

from store import db

BASE = "https://api.hubapi.com"

CONTACT_PROPS = [
    "email",
    "firm_type",
    "lifecyclestage",
    "createdate",
    "recent_conversion_event_name",
    "first_conversion_event_name",
    "hs_analytics_source",
    "hs_analytics_source_data_1",
    "hs_analytics_source_data_2",
    "num_conversion_events",
]

COMPANY_PROPS = [
    "name",
    "domain",
    "firm_type",
]


class HubSpotClient:
    def __init__(self, token: str):
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        })

    def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{BASE}{path}"
        for attempt in range(3):
            resp = self.session.get(url, params=params, timeout=30)
            if resp.status_code == 429:
                time.sleep(1.5 ** attempt)
                continue
            resp.raise_for_status()
            return resp.json()
        resp.raise_for_status()
        return {}

    def iter_contacts(self) -> Iterator[dict]:
        """Yield raw contact objects with companies association."""
        after = None
        while True:
            params = {
                "limit": 100,
                "properties": ",".join(CONTACT_PROPS),
                "associations": "companies",
            }
            if after:
                params["after"] = after
            data = self._get("/crm/v3/objects/contacts", params=params)
            for c in data.get("results", []):
                yield c
            paging = data.get("paging", {}).get("next")
            if not paging:
                break
            after = paging["after"]

    def iter_companies(self, ids: list[str] | None = None) -> Iterator[dict]:
        """Yield company objects. If ids given, batch-read; else paginate all."""
        if ids:
            # Batch read up to 100 at a time
            for chunk_start in range(0, len(ids), 100):
                chunk = ids[chunk_start:chunk_start + 100]
                body = {
                    "properties": COMPANY_PROPS,
                    "inputs": [{"id": cid} for cid in chunk],
                }
                resp = self.session.post(
                    f"{BASE}/crm/v3/objects/companies/batch/read",
                    json=body,
                    timeout=30,
                )
                resp.raise_for_status()
                for c in resp.json().get("results", []):
                    yield c
            return
        after = None
        while True:
            params = {"limit": 100, "properties": ",".join(COMPANY_PROPS)}
            if after:
                params["after"] = after
            data = self._get("/crm/v3/objects/companies", params=params)
            for c in data.get("results", []):
                yield c
            paging = data.get("paging", {}).get("next")
            if not paging:
                break
            after = paging["after"]


def _first_company_id(contact: dict) -> str | None:
    assoc = contact.get("associations", {}).get("companies", {}).get("results", [])
    if assoc:
        return assoc[0].get("id")
    return None


def refresh(token: str) -> dict:
    """Pull everything fresh into SQLite. Returns counts."""
    db.init_db()
    client = HubSpotClient(token)
    fetched_at = db.now_iso()

    contact_rows: list[dict] = []
    company_ids: set[str] = set()
    for c in client.iter_contacts():
        props = c.get("properties", {})
        company_id = _first_company_id(c)
        if company_id:
            company_ids.add(company_id)
        contact_rows.append({
            "id": c["id"],
            "email": props.get("email"),
            "firm_type": props.get("firm_type"),
            "lifecyclestage": props.get("lifecyclestage"),
            "createdate": props.get("createdate"),
            "recent_conversion_event_name": props.get("recent_conversion_event_name"),
            "first_conversion_event_name": props.get("first_conversion_event_name"),
            "hs_analytics_source": props.get("hs_analytics_source"),
            "hs_analytics_source_data_1": props.get("hs_analytics_source_data_1"),
            "hs_analytics_source_data_2": props.get("hs_analytics_source_data_2"),
            "num_conversion_events": _to_int(props.get("num_conversion_events")),
            "company_id": company_id,
            "fetched_at": fetched_at,
        })

    db.upsert_contacts(contact_rows)

    company_rows = []
    for c in client.iter_companies(ids=list(company_ids)):
        props = c.get("properties", {})
        company_rows.append({
            "id": c["id"],
            "name": props.get("name"),
            "domain": props.get("domain"),
            "firm_type": props.get("firm_type"),
            "fetched_at": fetched_at,
        })
    db.upsert_companies(company_rows)

    db.set_meta("last_full_refresh", fetched_at)
    return {"contacts": len(contact_rows), "companies": len(company_rows)}


def _to_int(v) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None

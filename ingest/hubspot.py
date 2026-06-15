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
    "hs_lead_status",
    "createdate",
    "recent_conversion_event_name",
    "first_conversion_event_name",
    "hs_analytics_source",
    "hs_analytics_source_data_1",
    "hs_analytics_source_data_2",
    "num_conversion_events",
    "num_associated_deals",
    "notes_last_contacted",
    "hs_email_last_open_date",
    "hs_email_last_click_date",
    "hubspot_owner_id",
]

COMPANY_PROPS = [
    "name",
    "domain",
    "firm_type",
]

DEAL_PROPS = [
    "dealname",
    "amount",
    "dealstage",
    "pipeline",
    "closedate",
    "createdate",
    "hubspot_owner_id",
]

TASK_PROPS = [
    "hs_task_subject",
    "hs_task_status",
    "hs_task_priority",
    "hs_task_type",
    "hs_timestamp",
    "hs_task_completion_date",
    "hubspot_owner_id",
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

    def get_portal_id(self) -> str | None:
        """Return the HubSpot portal (hub) ID, used to build record deep-links.

        Degrades to None if the account-info scope isn't granted (401/403).
        """
        try:
            data = self._get("/account-info/v3/details")
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code in (401, 403):
                return None
            raise
        pid = data.get("portalId")
        return str(pid) if pid else None

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

    def iter_forms(self) -> Iterator[dict]:
        """Yield all marketing forms (id + name)."""
        after = None
        while True:
            params = {"limit": 100}
            if after:
                params["after"] = after
            data = self._get("/marketing/v3/forms", params=params)
            for f in data.get("results", []):
                yield f
            paging = data.get("paging", {}).get("next")
            if not paging:
                break
            after = paging["after"]

    def iter_form_submissions(self, form_id: str, max_pages: int = 200) -> Iterator[dict]:
        """Yield all submissions for a given form. Each submission has values[], submittedAt, conversionId."""
        after = None
        pages = 0
        while pages < max_pages:
            params = {"limit": 50}
            if after:
                params["after"] = after
            try:
                data = self._get(f"/form-integrations/v1/submissions/forms/{form_id}", params=params)
            except requests.HTTPError as e:
                # Some forms (e.g., archived or restricted) may return 404/403 — skip them.
                if e.response is not None and e.response.status_code in (403, 404):
                    return
                raise
            for s in data.get("results", []):
                yield s
            paging = data.get("paging", {}).get("next")
            if not paging:
                break
            after = paging["after"]
            pages += 1

    def get_deal_stage_metadata(self) -> dict:
        """Return {stage_id: {is_closed, is_won, label, pipeline_id}} for all deal pipelines.

        HubSpot deal stages use opaque per-portal IDs (numeric for custom pipelines,
        word-ish for the default one). The only reliable way to know whether a stage
        means "closed" is the stage's metadata.isClosed flag — hardcoding "closedwon"/
        "closedlost" only works for the default pipeline. probability == 1.0 → won.

        Degrades to {} if the pipelines scope is missing (401/403), in which case the
        caller stores NULL stage flags and the dashboard falls back to the legacy filter.
        """
        try:
            data = self._get("/crm/v3/pipelines/deals")
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code in (401, 403):
                return {}
            raise
        out: dict[str, dict] = {}
        for pipe in data.get("results", []):
            pid = pipe.get("id")
            for stage in pipe.get("stages", []):
                sid = stage.get("id")
                if not sid:
                    continue
                md = stage.get("metadata", {}) or {}
                is_closed = str(md.get("isClosed", "")).lower() == "true"
                prob = _to_float(md.get("probability"))
                is_won = bool(is_closed and prob is not None and prob >= 1.0)
                out[sid] = {
                    "is_closed": is_closed,
                    "is_won": is_won,
                    "label": stage.get("label"),
                    "pipeline_id": pid,
                }
        return out

    def iter_deals(self) -> Iterator[dict]:
        """Yield raw deal objects with contact + company associations."""
        after = None
        while True:
            params = {
                "limit": 100,
                "properties": ",".join(DEAL_PROPS),
                "associations": "contacts,companies",
            }
            if after:
                params["after"] = after
            try:
                data = self._get("/crm/v3/objects/deals", params=params)
            except requests.HTTPError as e:
                # Missing scope returns 403 — degrade gracefully
                if e.response is not None and e.response.status_code in (401, 403):
                    return
                raise
            for d in data.get("results", []):
                yield d
            paging = data.get("paging", {}).get("next")
            if not paging:
                break
            after = paging["after"]

    def iter_tasks(self) -> Iterator[dict]:
        """Yield raw task objects with deal + contact associations."""
        after = None
        while True:
            params = {
                "limit": 100,
                "properties": ",".join(TASK_PROPS),
                "associations": "deals,contacts",
            }
            if after:
                params["after"] = after
            try:
                data = self._get("/crm/v3/objects/tasks", params=params)
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code in (401, 403):
                    return
                raise
            for t in data.get("results", []):
                yield t
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


def refresh(token: str, progress=None) -> dict:
    """Pull everything fresh into SQLite. Returns counts.

    `progress` is an optional callable(stage: str, current: int, total: int)
    used by the Streamlit UI to render a progress bar.
    """
    db.init_db()
    client = HubSpotClient(token)
    fetched_at = db.now_iso()

    # Portal ID powers record deep-links in the dashboard. Best-effort; degrades silently.
    portal_id = client.get_portal_id()
    if portal_id:
        db.set_meta("hubspot_portal_id", portal_id)

    if progress:
        progress("Contacts", 0, 0)

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
            "hs_lead_status": props.get("hs_lead_status"),
            "createdate": props.get("createdate"),
            "recent_conversion_event_name": props.get("recent_conversion_event_name"),
            "first_conversion_event_name": props.get("first_conversion_event_name"),
            "hs_analytics_source": props.get("hs_analytics_source"),
            "hs_analytics_source_data_1": props.get("hs_analytics_source_data_1"),
            "hs_analytics_source_data_2": props.get("hs_analytics_source_data_2"),
            "num_conversion_events": _to_int(props.get("num_conversion_events")),
            "num_associated_deals": _to_int(props.get("num_associated_deals")),
            "notes_last_contacted": props.get("notes_last_contacted"),
            "hs_email_last_open_date": props.get("hs_email_last_open_date"),
            "hs_email_last_click_date": props.get("hs_email_last_click_date"),
            "hubspot_owner_id": props.get("hubspot_owner_id"),
            "hubspot_url": c.get("url"),
            "company_id": company_id,
            "fetched_at": fetched_at,
        })

    db.upsert_contacts(contact_rows)

    # Remove contacts that no longer exist in HubSpot (deleted/archived).
    # Safety: only delete if the new set is >= 50% of the existing — guards
    # against accidental nukes if the API returned a truncated response.
    current_ids = {r["id"] for r in contact_rows}
    existing_n = db.count_contacts()
    if existing_n == 0 or len(current_ids) >= existing_n * 0.5:
        deleted = db.delete_contacts_not_in(current_ids)
        if deleted and progress:
            progress(f"Removed {deleted} stale contacts", 0, 0)

    if progress:
        progress("Companies", 0, len(company_ids))

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

    # Forms + submissions
    if progress:
        progress("Forms", 0, 0)

    form_rows: list[dict] = []
    form_ids: list[str] = []
    for f in client.iter_forms():
        form_rows.append({
            "id": f["id"],
            "name": f.get("name") or f.get("displayName") or "(unnamed)",
            "fetched_at": fetched_at,
        })
        form_ids.append(f["id"])
    db.upsert_forms(form_rows)

    sub_rows: list[dict] = []
    for i, fid in enumerate(form_ids):
        if progress:
            progress(f"Submissions ({form_rows[i]['name'][:30]})", i + 1, len(form_ids))
        for s in client.iter_form_submissions(fid):
            email = None
            for kv in s.get("values", []):
                if kv.get("name") == "email":
                    email = kv.get("value")
                    break
            ts = s.get("submittedAt")
            sub_rows.append({
                "conversion_id": s.get("conversionId") or f"{fid}:{ts}:{email}",
                "form_id": fid,
                "contact_email": (email or "").lower() or None,
                "submitted_at": _ms_to_iso(ts),
                "fetched_at": fetched_at,
            })
    db.upsert_form_submissions(sub_rows)

    # Deals (requires crm.objects.deals.read scope — degrades gracefully if missing)
    if progress:
        progress("Deals", 0, 0)
    # Resolve which stages mean "closed" for THIS portal (handles custom pipelines).
    stage_meta = client.get_deal_stage_metadata()
    deal_rows: list[dict] = []
    for d in client.iter_deals():
        props = d.get("properties", {})
        assoc = d.get("associations", {}) or {}
        contacts_assoc = assoc.get("contacts", {}).get("results", [])
        companies_assoc = assoc.get("companies", {}).get("results", [])
        ds = props.get("dealstage")
        if stage_meta:
            sm = stage_meta.get(ds) or {}
            stage_is_closed = 1 if sm.get("is_closed") else 0
            stage_is_won = 1 if sm.get("is_won") else 0
            stage_label = sm.get("label")
        else:
            # No stage metadata available — leave NULL so the dashboard falls back.
            stage_is_closed = stage_is_won = stage_label = None
        deal_rows.append({
            "id": d["id"],
            "name": props.get("dealname"),
            "amount": _to_float(props.get("amount")),
            "dealstage": ds,
            "pipeline": props.get("pipeline"),
            "closedate": props.get("closedate"),
            "createdate": props.get("createdate"),
            "hubspot_owner_id": props.get("hubspot_owner_id"),
            "primary_contact_id": contacts_assoc[0].get("id") if contacts_assoc else None,
            "primary_company_id": companies_assoc[0].get("id") if companies_assoc else None,
            "hubspot_url": d.get("url"),
            "stage_is_closed": stage_is_closed,
            "stage_is_won": stage_is_won,
            "stage_label": stage_label,
            "fetched_at": fetched_at,
        })
    db.upsert_deals(deal_rows)

    # Tasks (requires crm.objects.tasks.read or engagements scope)
    if progress:
        progress("Tasks", 0, 0)
    task_rows: list[dict] = []
    for t in client.iter_tasks():
        props = t.get("properties", {})
        assoc = t.get("associations", {}) or {}
        deals_assoc = [d.get("id") for d in assoc.get("deals", {}).get("results", []) if d.get("id")]
        contacts_assoc = [c.get("id") for c in assoc.get("contacts", {}).get("results", []) if c.get("id")]
        task_rows.append({
            "id": t["id"],
            "subject": props.get("hs_task_subject"),
            "status": props.get("hs_task_status"),
            "priority": props.get("hs_task_priority"),
            "task_type": props.get("hs_task_type"),
            "due_at": _ms_to_iso(props.get("hs_timestamp")) or props.get("hs_timestamp"),
            "completed_at": props.get("hs_task_completion_date"),
            "hubspot_owner_id": props.get("hubspot_owner_id"),
            "associated_deal_ids": ",".join(deals_assoc) if deals_assoc else None,
            "associated_contact_ids": ",".join(contacts_assoc) if contacts_assoc else None,
            "fetched_at": fetched_at,
        })
    db.upsert_tasks(task_rows)

    db.set_meta("last_full_refresh", fetched_at)
    return {
        "contacts": len(contact_rows),
        "companies": len(company_rows),
        "forms": len(form_rows),
        "submissions": len(sub_rows),
        "deals": len(deal_rows),
        "tasks": len(task_rows),
    }


def _to_float(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _ms_to_iso(ms) -> str | None:
    if ms is None:
        return None
    try:
        from datetime import datetime, timezone
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None


def _to_int(v) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None

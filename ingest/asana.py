"""Asana ingest layer.

Pulls Craig's incomplete assigned tasks and writes them into the SQLite cache.
Ported from the Roland Express server (`fetchAsanaTasks` in `server.js`) so the
dashboard reads Asana directly instead of proxying through localhost:3001.

Each task is tagged with a canonical HubSpot company name where one can be
resolved from the task or project name — that's what lets Asana work show up
next to the HubSpot deal it belongs to.
"""
from __future__ import annotations

import time
from typing import Iterator

import requests

from classify import companies as companies_cls
from store import db

BASE = "https://app.asana.com/api/1.0"

TASK_FIELDS = ",".join([
    "name",
    "due_on",
    "assignee.name",
    "projects.name",
    "memberships.section.name",
    "parent.name",
    "permalink_url",
    "notes",
])

# Asana's max page size. Unlike `tasks/search` (which hard-caps at 100 with no
# pagination at all), `GET /tasks` returns a `next_page` token we can follow.
PAGE_LIMIT = 100

# Safety valve: stop after this many pages so a pagination bug can't spin
# forever. 50 pages = 5,000 tasks, far above any realistic assigned-task count.
MAX_PAGES = 50


class AsanaClient:
    def __init__(self, token: str):
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        })

    def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{BASE}{path}"
        for attempt in range(3):
            resp = self.session.get(url, params=params, timeout=30)
            # Asana returns 429 with a Retry-After on rate limit.
            if resp.status_code == 429:
                wait = resp.headers.get("Retry-After")
                time.sleep(float(wait) if wait else 1.5 ** attempt)
                continue
            resp.raise_for_status()
            return resp.json()
        resp.raise_for_status()
        return {}

    def get_me(self) -> dict:
        """Return the authenticated user (gid, name, workspaces)."""
        return self._get("/users/me").get("data", {})

    def iter_assigned_tasks(self, workspace_gid: str, user_gid: str) -> Iterator[dict]:
        """Yield every incomplete task assigned to the user, following pagination.

        Uses `GET /tasks` rather than `/workspaces/{gid}/tasks/search`. The search
        endpoint does not paginate and silently caps at 100 results — the Roland
        server used it and so under-reported whenever Craig had more than 100 open
        tasks. `completed_since=now` is Asana's idiom for "incomplete only".
        """
        params = {
            "assignee": user_gid,
            "workspace": workspace_gid,
            "completed_since": "now",
            "opt_fields": TASK_FIELDS,
            "limit": PAGE_LIMIT,
        }
        pages = 0
        while pages < MAX_PAGES:
            data = self._get("/tasks", params)
            yield from data.get("data", [])
            pages += 1

            next_page = data.get("next_page")
            if not next_page or not next_page.get("offset"):
                return
            params = {**params, "offset": next_page["offset"]}

        raise RuntimeError(
            f"Asana pagination exceeded {MAX_PAGES} pages — refusing to loop further"
        )


def _company_matcher() -> companies_cls.CompanyMatcher:
    """Build a matcher from the company names already in the HubSpot cache.

    Needs no extra API call — companies are ingested by `ingest.hubspot`.
    """
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT name FROM companies WHERE name IS NOT NULL AND name != ''"
        ).fetchall()
    return companies_cls.CompanyMatcher([r["name"] for r in rows])


def refresh(token: str, progress=None) -> dict:
    """Pull assigned Asana tasks into the cache. Returns counts."""
    client = AsanaClient(token)
    fetched_at = db.now_iso()

    if progress:
        progress("Asana: identifying user and workspace…")

    me = client.get_me()
    user_gid = me.get("gid")
    user_name = me.get("name")
    workspaces = me.get("workspaces") or []
    if not user_gid or not workspaces:
        # No workspace means the token is scoped to nothing useful. Degrade
        # rather than crash, matching how the HubSpot ingest handles a missing
        # scope: the dashboard shows 0 Asana tasks instead of erroring.
        return {"asana_tasks": 0, "skipped": "no workspace"}

    workspace_gid = workspaces[0].get("gid")
    matcher = _company_matcher()

    if progress:
        progress("Asana: fetching assigned tasks…")

    rows: list[dict] = []
    for t in client.iter_assigned_tasks(workspace_gid, user_gid):
        projects = t.get("projects") or []
        project = (projects[0] or {}).get("name", "") if projects else ""

        memberships = t.get("memberships") or []
        section = ""
        if memberships:
            section = ((memberships[0] or {}).get("section") or {}).get("name", "")

        gid = t.get("gid")
        notes = t.get("notes") or ""
        assignee = (t.get("assignee") or {}).get("name") or user_name
        parent = (t.get("parent") or {}).get("name")

        rows.append({
            "id": gid,
            "name": t.get("name"),
            "due_on": t.get("due_on"),
            "assignee": assignee,
            "project": project,
            "section": section,
            "parent_task": parent,
            # Roland truncated notes to 200 chars for its card UI. Keep the full
            # text here and let the page decide — the column is cheap and the
            # follow-up context is sometimes past the cutoff.
            "notes": notes,
            "url": t.get("permalink_url") or f"https://app.asana.com/0/0/{gid}/f",
            # Task name takes priority over project name: a task that explicitly
            # names a client should file under that client, not its board.
            "company": matcher.resolve([t.get("name"), project]),
            "fetched_at": fetched_at,
        })

    db.upsert_asana_tasks(rows)

    # Prune tasks completed or deleted in Asana. Same 50%-of-existing guard the
    # HubSpot ingest uses: a partial/failed fetch must never wipe the cache.
    task_ids = {r["id"] for r in rows if r["id"]}
    existing = db.count_asana_tasks()
    if task_ids and (existing == 0 or len(task_ids) >= existing * 0.5):
        db.delete_asana_tasks_not_in(task_ids)

    db.set_meta("last_asana_refresh", fetched_at)
    return {"asana_tasks": len(rows)}

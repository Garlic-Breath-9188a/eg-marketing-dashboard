"""AuthoredUp LinkedIn ingest.

Pulls actors (Craig's profile + Ezra Group company page) and posts with full
engagement metrics into local SQLite. Rate limit: 100 requests/hour, so we
paginate generously per call and cache aggressively.
"""
from __future__ import annotations

import time
from typing import Iterator

import requests

from store import db

BASE = "https://api.authoredup.com/external/api/v1"


def _to_list(data) -> list[dict]:
    """AuthoredUp returns either a flat array or a dict with items/results/data — normalize."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("items", "results", "data", "posts"):
            v = data.get(key)
            if isinstance(v, list):
                return v
    return []


class AuthoredUpClient:
    def __init__(self, api_key: str):
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        })

    def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{BASE}{path}"
        for attempt in range(3):
            resp = self.session.get(url, params=params, timeout=30)
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("retry-after", 5))
                time.sleep(min(retry_after, 30))
                continue
            resp.raise_for_status()
            return resp.json()
        resp.raise_for_status()
        return {}

    def list_actors(self) -> list[dict]:
        """Return list of actors (profiles + companies the user has access to)."""
        data = self._get("/actors", params={"include-types": ["profile", "company"]})
        return _to_list(data)

    def iter_post_pages(self, limit_per_page: int = 50) -> Iterator[dict]:
        """Yield full page responses for posts. Each page is a dict with
        'items', 'profiles', 'companies', 'groups', and 'nextPageOffset'.
        """
        offset = 0
        while True:
            data = self._get("/posts", params={"limit": limit_per_page, "offset": offset})
            if not isinstance(data, dict):
                break
            items = data.get("items", [])
            yield data
            if not items or len(items) < limit_per_page:
                break
            next_offset = data.get("nextPageOffset")
            offset = next_offset if next_offset is not None else offset + len(items)


def _i(v):
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _f(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _extract_post_row(post: dict, fetched_at: str, actor_name_lookup: dict) -> dict:
    """Flatten a post object into our SQLite row shape using the actual field names AuthoredUp returns."""
    # Posts reference exactly one of: actor_profile_id, actor_company_id, actor_group_id.
    actor_id = (
        post.get("actor_profile_id")
        or post.get("actor_company_id")
        or post.get("actor_group_id")
    )
    actor_name = actor_name_lookup.get(actor_id) if actor_id else None

    text_metrics = post.get("text_metrics") or {}

    return {
        "urn": post.get("urn") or post.get("activity_urn"),
        "actor_id": actor_id,
        "actor_name": actor_name,
        "text": (post.get("text") or "")[:5000],
        "content_type": post.get("content_type"),
        # Prefer the explicit publish time; fall back to normalized date or create time.
        "published_at": (
            post.get("post_published_at")
            or post.get("normalized_post_date")
            or post.get("post_created_at")
        ),
        "reaction_count": _i(post.get("reaction_count")),
        "comment_count": _i(post.get("comment_count")),
        "share_count": _i(post.get("share_count")),
        "impression_count": _i(post.get("impression_count")),
        "save_count": _i(post.get("save_count")),
        "send_count": _i(post.get("send_count")),
        "members_reached_count": _i(post.get("members_reached_count")),
        "profile_view_count": _i(post.get("profile_view_count")),
        "followers_gained_count": _i(post.get("followers_gained_count")),
        "engagement_rate": _f(post.get("engagement_rate")),
        "word_count": _i(text_metrics.get("words")),
        "fetched_at": fetched_at,
    }


def _profile_display_name(p: dict) -> str:
    parts = [p.get("first_name") or "", p.get("last_name") or ""]
    name = " ".join(s for s in parts if s).strip()
    return name or p.get("public_identifier") or p.get("id") or "(unknown profile)"


def refresh(api_key: str, progress=None) -> dict:
    """Pull actors + posts into SQLite. Returns counts."""
    db.init_db()
    client = AuthoredUpClient(api_key)
    fetched_at = db.now_iso()

    # Build actor lookup as we paginate posts. AuthoredUp returns referenced
    # entities (profiles/companies/groups) at the top level of each page.
    actor_name_lookup: dict[str, str] = {}
    actor_rows_by_id: dict[str, dict] = {}

    if progress:
        progress("LinkedIn posts", 0, 0)

    post_rows: list[dict] = []
    page_n = 0
    for page in client.iter_post_pages():
        page_n += 1
        for p in page.get("profiles", []) or []:
            pid = p.get("id")
            if not pid:
                continue
            name = _profile_display_name(p)
            actor_name_lookup[pid] = name
            actor_rows_by_id[pid] = {"id": pid, "type": "profile", "name": name, "fetched_at": fetched_at}
        for c in page.get("companies", []) or []:
            cid = c.get("id")
            if not cid:
                continue
            name = c.get("name") or cid
            actor_name_lookup[cid] = name
            actor_rows_by_id[cid] = {"id": cid, "type": "company", "name": name, "fetched_at": fetched_at}
        for g in page.get("groups", []) or []:
            gid = g.get("id")
            if not gid:
                continue
            name = g.get("name") or gid
            actor_name_lookup[gid] = name
            actor_rows_by_id[gid] = {"id": gid, "type": "group", "name": name, "fetched_at": fetched_at}

        for post in page.get("items", []) or []:
            row = _extract_post_row(post, fetched_at, actor_name_lookup)
            if row["urn"]:
                post_rows.append(row)

        if progress:
            progress(f"LinkedIn posts (page {page_n}, {len(post_rows)} so far)", page_n, 0)

    db.upsert_linkedin_actors(list(actor_rows_by_id.values()))
    db.upsert_linkedin_posts(post_rows)

    db.set_meta("last_linkedin_refresh", fetched_at)
    return {"actors": len(actor_rows_by_id), "posts": len(post_rows)}

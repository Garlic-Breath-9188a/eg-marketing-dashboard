"""WordPress / Jetpack ingest for WealthTechToday.com posts.

Two data sources combined:
1. **WordPress REST API** (`/wp-json/wp/v2/posts`) — post metadata: title, URL, date, categories, tags, author, excerpt.
   Public for most WordPress sites; auth needed for drafts/private posts.

2. **Jetpack Stats via the site's own API** (`/wp-json/jetpack/v4/stats-app/…`) — view counts.
   Jetpack proxies these to WordPress.com over the site's existing connection, so the
   WordPress application password below is the only credential needed. No WordPress.com
   OAuth app, no bearer token, nothing to expire.

Auth from `st.secrets`:
  - `WORDPRESS_BASE_URL`        — e.g. "https://wealthtechtoday.com"
  - `WORDPRESS_APP_PASSWORD`    — optional, for reading drafts; format "user:app-password" (base64-encoded in basic auth)
  - `WORDPRESS_USER`            — username for app password

`WPCOM_API_TOKEN` / `WPCOM_SITE` are no longer used — the site-proxied endpoint
replaced them on 2026-07-22.
"""
from __future__ import annotations

import base64
import json
import time
from typing import Iterator

import requests

from store import db


def _strip_html(text: str | None) -> str | None:
    """Strip tags and decode entities.

    WordPress returns titles pre-escaped, so "A & B" arrives as "A &#038; B".
    Without unescaping, that entity is shown literally in the dashboard.
    """
    if not text:
        return None
    import html
    import re
    return html.unescape(re.sub(r"<[^>]+>", "", text)).strip() or None


class WordPressClient:
    def __init__(self, base_url: str, app_password_basic_auth: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        if app_password_basic_auth:
            self.session.headers.update({"Authorization": f"Basic {app_password_basic_auth}"})

    def iter_posts(self, per_page: int = 100, max_pages: int = 50) -> Iterator[dict]:
        page = 1
        while page <= max_pages:
            url = f"{self.base_url}/wp-json/wp/v2/posts"
            params = {"per_page": per_page, "page": page, "_embed": "true", "status": "publish"}
            resp = self.session.get(url, params=params, timeout=30)
            if resp.status_code == 400:
                # WordPress returns 400 when paging past the end
                break
            resp.raise_for_status()
            posts = resp.json()
            if not posts:
                break
            for p in posts:
                yield p
            if len(posts) < per_page:
                break
            page += 1


class JetpackStatsClient:
    """Read Jetpack view counts through the site's own REST API.

    Jetpack exposes `/wp-json/jetpack/v4/stats-app/…` on the site itself, which
    proxies to WordPress.com using the site's existing Jetpack connection. That
    means the **WordPress application password already configured for post
    metadata is enough** — no WordPress.com OAuth app, no `WPCOM_API_TOKEN`, and
    nothing to expire.

    The earlier implementation called `public-api.wordpress.com` directly and
    needed an OAuth bearer token, which was never configured — which is why every
    post read 0 views. Craig asked whether the app was registered on
    wordpress.com or on his own site; checking that turned up this endpoint and
    made the whole OAuth flow unnecessary.

    `summarize=1` is what makes this usable: without it the API returns a
    per-day breakdown that has to be summed client-side, and it caps how far
    back a single response reaches.
    """

    # Views over a period. `id` is the WordPress post ID, matching wordpress_posts.id.
    _STATS_PATH = "/wp-json/jetpack/v4/stats-app/sites/{site_id}/stats/top-posts"
    _SITE_PATH = "/wp-json/jetpack/v4/site"

    # Jetpack Stats retains detail for a limited window; a long lookback is the
    # practical stand-in for all-time and is what the "all time" column shows.
    ALL_TIME_DAYS = 3650
    MAX_POSTS = 500

    def __init__(self, base_url: str, basic_auth: str | None):
        self.base = base_url.rstrip("/")
        self.session = requests.Session()
        if basic_auth:
            self.session.headers.update({"Authorization": f"Basic {basic_auth}"})
        self._site_id: str | None = None

    @property
    def site_id(self) -> str | None:
        """The WordPress.com blog ID Jetpack assigned to this site."""
        if self._site_id is None:
            try:
                resp = self.session.get(self.base + self._SITE_PATH, timeout=30)
                resp.raise_for_status()
                data = resp.json().get("data")
                if isinstance(data, str):
                    data = json.loads(data)
                self._site_id = str((data or {}).get("ID") or "") or None
            except (requests.RequestException, ValueError, TypeError):
                self._site_id = None
        return self._site_id

    def _views(self, days: int) -> dict:
        site_id = self.site_id
        if not site_id:
            return {}
        url = self.base + self._STATS_PATH.format(site_id=site_id)
        try:
            resp = self.session.get(
                url,
                params={"num": days, "summarize": 1, "max": self.MAX_POSTS},
                timeout=60,
            )
            resp.raise_for_status()
            entries = (resp.json().get("summary") or {}).get("postviews") or []
        except (requests.RequestException, ValueError):
            return {}

        result: dict[str, int] = {}
        for entry in entries:
            pid = str(entry.get("id") or "")
            # id 0 is the "Home page / Archives" pseudo-entry, not a post.
            if pid and pid != "0":
                result[pid] = int(entry.get("views") or 0)
        return result

    def top_posts(self, days: int = 30) -> dict:
        """{post_id: views} over the last `days`."""
        return self._views(days)

    def all_time_views(self) -> dict:
        """{post_id: views} over a long lookback — see ALL_TIME_DAYS."""
        return self._views(self.ALL_TIME_DAYS)


def _build_basic_auth(user: str | None, app_password: str | None) -> str | None:
    if not user or not app_password:
        return None
    creds = f"{user}:{app_password}".encode("utf-8")
    return base64.b64encode(creds).decode("ascii")


def refresh(secrets: dict, progress=None) -> dict:
    """Pull WordPress posts + (optional) Jetpack stats into SQLite."""
    db.init_db()

    base_url = secrets.get("WORDPRESS_BASE_URL")
    if not base_url:
        return {"posts": 0, "error": "WORDPRESS_BASE_URL not configured"}

    fetched_at = db.now_iso()
    wp_basic = _build_basic_auth(secrets.get("WORDPRESS_USER"), secrets.get("WORDPRESS_APP_PASSWORD"))
    wp = WordPressClient(base_url, wp_basic)

    if progress:
        progress("WordPress posts", 0, 0)

    post_rows: list[dict] = []
    for p in wp.iter_posts():
        embedded = p.get("_embedded", {}) or {}
        author_obj = (embedded.get("author") or [{}])[0]
        terms = embedded.get("wp:term") or []
        categories = []
        tags = []
        for term_group in terms:
            for term in term_group:
                if term.get("taxonomy") == "category":
                    categories.append(term.get("name"))
                elif term.get("taxonomy") == "post_tag":
                    tags.append(term.get("name"))

        title = _strip_html((p.get("title") or {}).get("rendered"))
        content = _strip_html((p.get("content") or {}).get("rendered")) or ""
        excerpt = _strip_html((p.get("excerpt") or {}).get("rendered"))

        post_rows.append({
            "id": str(p.get("id")),
            "title": title,
            "slug": p.get("slug"),
            "url": p.get("link"),
            "status": p.get("status"),
            "published_at": p.get("date_gmt") or p.get("date"),
            "modified_at": p.get("modified_gmt") or p.get("modified"),
            "author_id": str(author_obj.get("id")) if author_obj.get("id") else None,
            "author_name": author_obj.get("name"),
            "categories": ", ".join(c for c in categories if c) or None,
            "tags": ", ".join(t for t in tags if t) or None,
            "excerpt": (excerpt or "")[:500] if excerpt else None,
            "word_count": len(content.split()) if content else None,
            "views_30d": None,
            "views_all_time": None,
            "fetched_at": fetched_at,
        })

    # Pull Jetpack view counts through the site's own API. This needs only the
    # WordPress app password already used above — see JetpackStatsClient.
    if wp_basic:
        if progress:
            progress("Jetpack stats", 0, 0)
        jp = JetpackStatsClient(base_url, wp_basic)
        views_30d = jp.top_posts(days=30)
        views_all = jp.all_time_views()
        if views_30d or views_all:
            for r in post_rows:
                r["views_30d"] = views_30d.get(r["id"], 0)
                r["views_all_time"] = views_all.get(r["id"], 0)

    db.upsert_wordpress_posts(post_rows)
    db.set_meta("last_wordpress_refresh", fetched_at)
    return {"posts": len(post_rows)}

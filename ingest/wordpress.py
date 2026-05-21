"""WordPress / Jetpack ingest for WealthTechToday.com posts.

Two data sources combined:
1. **WordPress REST API** (`/wp-json/wp/v2/posts`) — post metadata: title, URL, date, categories, tags, author, excerpt.
   Public for most WordPress sites; auth needed for drafts/private posts.

2. **Jetpack Stats / WordPress.com Stats API** — view counts.
   Requires the site to be connected to WordPress.com (Jetpack) AND a WordPress.com OAuth bearer token.
   If the token is absent, we skip stats and just pull metadata.

Auth from `st.secrets`:
  - `WORDPRESS_BASE_URL`        — e.g. "https://wealthtechtoday.com"
  - `WORDPRESS_APP_PASSWORD`    — optional, for reading drafts; format "user:app-password" (base64-encoded in basic auth)
  - `WORDPRESS_USER`            — username for app password
  - `WPCOM_API_TOKEN`           — optional WordPress.com OAuth bearer for Stats API
  - `WPCOM_SITE`                — the site identifier on WordPress.com (e.g., "wealthtechtoday.com" or numeric site ID)
"""
from __future__ import annotations

import base64
import time
from typing import Iterator

import requests

from store import db


def _strip_html(text: str | None) -> str | None:
    if not text:
        return None
    import re
    return re.sub(r"<[^>]+>", "", text).strip() or None


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
    def __init__(self, token: str, site: str):
        self.token = token
        self.site = site
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}"})

    def top_posts(self, days: int = 30) -> dict:
        """Return a dict of {post_id: views} for the last `days`."""
        url = f"https://public-api.wordpress.com/rest/v1.1/sites/{self.site}/stats/top-posts"
        params = {"num": days, "max": 100}
        resp = self.session.get(url, params=params, timeout=30)
        if resp.status_code != 200:
            return {}
        data = resp.json()
        result = {}
        # WordPress.com returns {"days": {"YYYY-MM-DD": {"postviews": [{"id":..., "views":...}, ...]}, ...}}
        days_data = data.get("days", {})
        for day, day_payload in days_data.items():
            for entry in day_payload.get("postviews", []) or []:
                pid = str(entry.get("id"))
                if pid:
                    result[pid] = result.get(pid, 0) + int(entry.get("views") or 0)
        return result

    def all_time_views(self) -> dict:
        """Return a dict of {post_id: total_views} all-time."""
        url = f"https://public-api.wordpress.com/rest/v1.1/sites/{self.site}/stats/top-posts"
        params = {"period": "year", "num": 10, "max": 200}
        resp = self.session.get(url, params=params, timeout=30)
        if resp.status_code != 200:
            return {}
        data = resp.json()
        result = {}
        for entry in data.get("summary", {}).get("postviews", []) or []:
            pid = str(entry.get("id"))
            if pid:
                result[pid] = int(entry.get("views") or 0)
        return result


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

    # Pull Jetpack stats if a token is configured
    stats_token = secrets.get("WPCOM_API_TOKEN")
    stats_site = secrets.get("WPCOM_SITE")
    if stats_token and stats_site:
        if progress:
            progress("Jetpack stats", 0, 0)
        jp = JetpackStatsClient(stats_token, stats_site)
        views_30d = jp.top_posts(days=30)
        views_all = jp.all_time_views()
        for r in post_rows:
            r["views_30d"] = views_30d.get(r["id"], 0)
            r["views_all_time"] = views_all.get(r["id"], 0)

    db.upsert_wordpress_posts(post_rows)
    db.set_meta("last_wordpress_refresh", fetched_at)
    return {"posts": len(post_rows)}

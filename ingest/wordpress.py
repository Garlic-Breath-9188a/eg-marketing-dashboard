"""WordPress ingest for WealthTechToday.com posts.

**Post metadata** comes from the **WordPress.com public API** (v1.1,
`public-api.wordpress.com/rest/v1.1/sites/<domain>/posts`), NOT the site's own
`/wp-json`. The site is hosted on SiteGround, whose server-side Anti-Bot AI serves
datacenter IPs (Streamlit Cloud's egress) an `sgcaptcha` HTML challenge that a
headless client can't solve — so the site's own REST API is unreachable from Cloud
(confirmed with SiteGround support 2026-08-06; no `/wp-json/` path exclusion is
possible on their platform). The WordPress.com API is on WordPress.com's own infra,
needs no auth for public posts, and isn't behind SiteGround.

**View counts** (`JetpackStatsClient`) still call the site's own
`/wp-json/jetpack/v4/stats-app/…`, which means they only populate from an
un-blocked connection (e.g. local); on Streamlit Cloud they degrade to 0 because
that domain is behind the same SiteGround block. Getting counts on Cloud requires
a WordPress.com OAuth token (stats are private) — deferred until needed.

Config from `st.secrets`:
  - `WORDPRESS_BASE_URL`      — e.g. "https://wealthtechtoday.com" (domain → WP.com site id)
  - `WORDPRESS_USER` / `WORDPRESS_APP_PASSWORD` — only used by the (local-only) stats path
"""
from __future__ import annotations

import base64
import json
import time
from typing import Iterator

import requests

from store import db

# Some hosts/WAFs serve a bot-challenge HTML page (HTTP 200) to unfamiliar clients
# — especially from datacenter IPs like Streamlit Cloud. A real browser UA + an
# explicit JSON Accept header clears the lighter rules; harmless otherwise.
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


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


def _site_from_base_url(base_url: str) -> str:
    """wealthtechtoday.com from https://wealthtechtoday.com/ — the WordPress.com API
    accepts the bare domain as the site identifier."""
    return base_url.split("://", 1)[-1].strip("/").split("/")[0]


class WordPressClient:
    """Reads published posts from the **WordPress.com public API** (v1.1), not the
    site's own /wp-json. WealthTechToday is hosted on SiteGround, whose server-side
    Anti-Bot AI serves datacenter IPs (Streamlit Cloud) an `sgcaptcha` HTML challenge
    that a headless client can't solve — so the site's own REST API is unreachable
    from Cloud. The WordPress.com API runs on WordPress.com's own infra (the site is
    Jetpack-connected), needs no auth for public posts, and isn't behind SiteGround.
    """

    API = "https://public-api.wordpress.com/rest/v1.1"

    def __init__(self, base_url: str):
        self.site = _site_from_base_url(base_url)
        self.session = requests.Session()
        self.session.headers.update(_BROWSER_HEADERS)

    def iter_posts(self, per_page: int = 100, max_pages: int = 50) -> Iterator[dict]:
        offset = 0
        fetched = 0
        for _ in range(max_pages):
            url = f"{self.API}/sites/{self.site}/posts/"
            params = {"number": per_page, "offset": offset, "status": "publish"}
            resp = self.session.get(url, params=params, timeout=30)
            resp.raise_for_status()
            try:
                data = resp.json()
            except ValueError:
                ct = resp.headers.get("content-type", "unknown")
                snippet = " ".join((resp.text or "").split())[:200]
                raise RuntimeError(
                    f"WordPress.com API returned non-JSON (HTTP {resp.status_code}, "
                    f"{ct}) from {resp.url}. Body starts: {snippet!r}"
                )
            posts = data.get("posts") or []
            if not posts:
                break
            for p in posts:
                yield p
            fetched += len(posts)
            offset += len(posts)
            if fetched >= int(data.get("found") or 0) or len(posts) < per_page:
                break


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
        self.session.headers.update(_BROWSER_HEADERS)
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


class WpcomStatsClient:
    """View counts via the **WordPress.com API** with an OAuth token — the only
    stats path reachable from Streamlit Cloud (the site's own /wp-json is behind
    SiteGround's Anti-Bot AI). Stats are private, so this needs a WordPress.com
    OAuth access token (scope `stats`) in secrets as WPCOM_API_TOKEN.
    """

    API = "https://public-api.wordpress.com/rest/v1.1"
    ALL_TIME_DAYS = 3650
    MAX_POSTS = 500

    def __init__(self, site: str, token: str):
        self.site = site
        self.session = requests.Session()
        self.session.headers.update(_BROWSER_HEADERS)
        self.session.headers.update({"Authorization": f"Bearer {token}"})

    def _views(self, days: int) -> dict:
        url = f"{self.API}/sites/{self.site}/stats/top-posts"
        try:
            resp = self.session.get(
                url, params={"num": days, "summarize": 1, "max": self.MAX_POSTS}, timeout=60,
            )
            resp.raise_for_status()
            entries = (resp.json().get("summary") or {}).get("postviews") or []
        except (requests.RequestException, ValueError):
            return {}
        result: dict[str, int] = {}
        for entry in entries:
            pid = str(entry.get("id") or "")
            if pid and pid != "0":  # id 0 = Home/Archives pseudo-entry
                result[pid] = int(entry.get("views") or 0)
        return result

    def top_posts(self, days: int = 30) -> dict:
        return self._views(days)

    def all_time_views(self) -> dict:
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
    wp = WordPressClient(base_url)

    if progress:
        progress("WordPress posts", 0, 0)

    post_rows: list[dict] = []
    try:
        _posts_iter = list(wp.iter_posts())
    except Exception as e:
        return {"posts": 0, "error": str(e)}
    for p in _posts_iter:
        # WordPress.com v1.1 shape: categories/tags are {name: {...}} dicts,
        # author is an object, title/excerpt/content are HTML-escaped strings.
        author = p.get("author") or {}
        categories = list((p.get("categories") or {}).keys())
        tags = list((p.get("tags") or {}).keys())

        title = _strip_html(p.get("title"))
        content = _strip_html(p.get("content")) or ""
        excerpt = _strip_html(p.get("excerpt"))

        post_rows.append({
            "id": str(p.get("ID")),
            "title": title,
            "slug": p.get("slug"),
            "url": p.get("URL"),
            "status": p.get("status"),
            "published_at": p.get("date"),
            "modified_at": p.get("modified"),
            "author_id": str(author.get("ID")) if author.get("ID") else None,
            "author_name": author.get("name"),
            "categories": ", ".join(c for c in categories if c) or None,
            "tags": ", ".join(t for t in tags if t) or None,
            "excerpt": (excerpt or "")[:500] if excerpt else None,
            "word_count": len(content.split()) if content else None,
            "views_30d": None,
            "views_all_time": None,
            "fetched_at": fetched_at,
        })

    # View counts. Prefer the WordPress.com API with an OAuth token (reachable from
    # Streamlit Cloud); otherwise fall back to the site's own Jetpack endpoint via the
    # app password — which only works from an un-blocked connection (SiteGround's
    # Anti-Bot AI blocks Cloud), so it yields 0 there.
    site = _site_from_base_url(base_url)
    wpcom_token = secrets.get("WPCOM_API_TOKEN")
    views_30d: dict = {}
    views_all: dict = {}
    if wpcom_token:
        if progress:
            progress("WordPress.com stats", 0, 0)
        jp = WpcomStatsClient(site, wpcom_token)
        views_30d = jp.top_posts(days=30)
        views_all = jp.all_time_views()
    elif wp_basic:
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

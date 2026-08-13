#!/usr/bin/env python3
"""Refresh data/wp_views.json — WealthTechToday per-article Jetpack view counts.

The dashboard reads this committed snapshot for the Content page's Views columns.
Streamlit Cloud can't fetch these itself: its egress IP is blocked by SiteGround's
Anti-Bot AI, and the WordPress.com OAuth path hits "user cannot view stats". This
script runs from an *un-blocked* connection (a laptop, or GitHub Actions) using the
WordPress application password against the site's own Jetpack stats endpoint — the
path proven to work — and writes the snapshot the dashboard consumes.

Run locally:
    WORDPRESS_USER=... WORDPRESS_APP_PASSWORD='xxxx xxxx ...' python scripts/fetch_wp_views.py

Env: WORDPRESS_USER, WORDPRESS_APP_PASSWORD (required);
     WORDPRESS_BASE_URL (default https://wealthtechtoday.com), WPCOM_SITE_ID (default 205340970).
"""
from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

import requests

BASE = os.environ.get("WORDPRESS_BASE_URL", "https://wealthtechtoday.com").rstrip("/")
SITE_ID = os.environ.get("WPCOM_SITE_ID", "205340970")
OUT = Path(__file__).resolve().parent.parent / "data" / "wp_views.json"


def _views(days: int, auth: str) -> dict:
    url = f"{BASE}/wp-json/jetpack/v4/stats-app/sites/{SITE_ID}/stats/top-posts"
    resp = requests.get(
        url,
        headers={"Authorization": f"Basic {auth}", "User-Agent": "Mozilla/5.0"},
        params={"num": days, "summarize": 1, "max": 5000},
        timeout=90,
    )
    resp.raise_for_status()
    out = {}
    for e in (resp.json().get("summary") or {}).get("postviews") or []:
        pid = str(e.get("id") or "")
        if e.get("type") == "post" and pid not in ("", "0"):
            out[pid] = int(e.get("views") or 0)
    return out


def _from_secrets_toml(key: str) -> str | None:
    """Fallback: read a value from .streamlit/secrets.toml so the script runs on a
    machine (e.g. the Mac mini) without needing env vars set."""
    path = Path(__file__).resolve().parent.parent / ".streamlit" / "secrets.toml"
    try:
        for line in path.read_text().splitlines():
            if line.strip().startswith(key):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        return None
    return None


def main() -> None:
    user = os.environ.get("WORDPRESS_USER") or _from_secrets_toml("WORDPRESS_USER")
    app_pw = os.environ.get("WORDPRESS_APP_PASSWORD") or _from_secrets_toml("WORDPRESS_APP_PASSWORD")
    if not (user and app_pw):
        sys.exit("Set WORDPRESS_USER and WORDPRESS_APP_PASSWORD (env vars or .streamlit/secrets.toml).")
    auth = base64.b64encode(f"{user}:{app_pw}".encode()).decode()
    data = {
        "source": "jetpack stats-app via app password",
        "site": BASE,
        "views_all": _views(3650, auth),
        "views_30d": _views(30, auth),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=0, sort_keys=True))
    print(
        f"wrote {OUT} — {len(data['views_all'])} articles, "
        f"{sum(data['views_all'].values()):,} all-time views"
    )


if __name__ == "__main__":
    main()

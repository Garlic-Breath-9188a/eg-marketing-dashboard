#!/usr/bin/env python3
"""Walk through connecting Jetpack Stats, so the Content page can rank by views.

    python scripts/setup_jetpack_stats.py <client_id>

Prints the WordPress.com authorization URL, takes the token back (hidden input,
never echoed), verifies it actually returns stats, writes it to
`.streamlit/secrets.toml`, and refreshes WordPress.

Uses the implicit flow (`response_type=token`): WordPress.com puts the token
straight in the URL fragment, so there is no client-secret exchange and nothing
to keep beyond the token itself. The token is scoped to one blog via `blog=`.
"""
from __future__ import annotations

import getpass
import sys
import tomllib
import urllib.parse
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

SECRETS = REPO / ".streamlit" / "secrets.toml"
SITE = "wealthtechtoday.com"
AUTHORIZE = "https://public-api.wordpress.com/oauth2/authorize"
STATS_URL = f"https://public-api.wordpress.com/rest/v1.1/sites/{SITE}/stats/top-posts"

# Any URL registered on the app works — the token arrives in the fragment, which
# the browser never sends anywhere. The site's own URL is used because it is
# guaranteed to load and avoids localhost/https validation quirks.
REDIRECT = f"https://{SITE}"


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        print("First create an app at https://developer.wordpress.com/apps/ with:")
        print(f"    Website URL   {REDIRECT}")
        print(f"    Redirect URL  {REDIRECT}")
        print("    Type          Web")
        print("\nThen re-run this with the Client ID it gives you.")
        return 2

    client_id = sys.argv[1].strip()
    params = urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": REDIRECT,
        "response_type": "token",
        "blog": SITE,          # scope the token to this one site
    })

    print("\n1. Open this URL and click Approve:\n")
    print(f"   {AUTHORIZE}?{params}\n")
    print(f"2. You land on {REDIRECT}. Look at the browser address bar — it ends with")
    print("      #access_token=XXXXX&expires_in=...&token_type=bearer&site_id=...")
    print("   Copy just the XXXXX part (between 'access_token=' and the next '&').\n")

    token = getpass.getpass("3. Paste the token here (input hidden): ").strip()
    if not token:
        print("No token entered.", file=sys.stderr)
        return 1
    if token.startswith("#") or "access_token=" in token:
        print("\nThat looks like the whole fragment. Paste only the value after "
              "'access_token=' and before the next '&'.", file=sys.stderr)
        return 1

    print("\nVerifying against the stats API…")
    resp = requests.get(
        STATS_URL, headers={"Authorization": f"Bearer {token}"},
        params={"num": 1}, timeout=30,
    )
    if resp.status_code != 200:
        print(f"FAILED — HTTP {resp.status_code}: {resp.text[:200]}", file=sys.stderr)
        print("\nToken not written. Common causes: the redirect URL on the app does "
              f"not exactly match {REDIRECT}, or approval was for a different site.",
              file=sys.stderr)
        return 1
    print("OK — the token can read stats.")

    existing = tomllib.loads(SECRETS.read_text()) if SECRETS.exists() else {}
    if "WPCOM_API_TOKEN" in existing:
        print("\nWPCOM_API_TOKEN is already in secrets.toml — leaving it alone.")
        print("Remove that line first if you want to replace it.")
        return 1

    with SECRETS.open("a") as fh:
        fh.write("\n# Jetpack Stats — powers the views column on the Content page.\n")
        fh.write(f'WPCOM_API_TOKEN = "{token}"\n')
        fh.write(f'WPCOM_SITE = "{SITE}"\n')
    print(f"Written to {SECRETS} (gitignored).")

    print("\nRefreshing WordPress…")
    from ingest import wordpress  # noqa: E402 — after sys.path setup
    secrets = tomllib.loads(SECRETS.read_text())
    result = wordpress.refresh(secrets, progress=lambda *a: None)
    print(f"   {result}")

    from store import db  # noqa: E402
    with db.connect() as conn:
        n = conn.execute(
            "SELECT COUNT(*) n FROM wordpress_posts WHERE views_30d > 0"
        ).fetchone()["n"]
    print(f"\nPosts with view data: {n}")
    print("Open the Content page — it now sorts by views." if n else
          "No views came back. The site may have little recent traffic, or the "
          "Jetpack Stats module may be off in WP admin.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

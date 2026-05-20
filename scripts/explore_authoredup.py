"""One-off discovery: hit AuthoredUp endpoints, print response shapes.

Loads AUTHOREDUP_API_KEY from .streamlit/secrets.toml. Does NOT log the key.
Prints HTTP status, top-level keys, and the field names of the first item so
we can write the proper ingest based on real data instead of guesses.

Usage:
    cd "EG Marketing Dashboard"
    source .venv/bin/activate
    python3 scripts/explore_authoredup.py
"""
from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

import requests

BASES = [
    "https://api.authoredup.com",
    "https://app.authoredup.com/api",
    "https://authoredup.com/api",
]

# Different auth header conventions to try
AUTH_VARIANTS = [
    ("Bearer header", lambda k: {"Authorization": f"Bearer {k}"}),
    ("X-API-Key header", lambda k: {"X-API-Key": k}),
    ("Authorization key", lambda k: {"Authorization": k}),
    ("X-Auth-Token header", lambda k: {"X-Auth-Token": k}),
]

CANDIDATE_PATHS = [
    "/",
    "/v1",
    "/v1/",
    "/v1/posts",
    "/v1/posts/own",
    "/v1/users/me",
    "/v1/me",
    "/v1/me/posts",
    "/v1/users/me/posts",
    "/v1/profile",
    "/v1/profile/posts",
    "/v1/post-history",
    "/v1/actors",
    "/posts",
    "/users/me/posts",
    "/api/v1/posts",
]


def _load_key() -> str:
    secrets_path = Path(__file__).parent.parent / ".streamlit" / "secrets.toml"
    if not secrets_path.exists():
        print(f"❌ No secrets.toml at {secrets_path}")
        sys.exit(1)
    with open(secrets_path, "rb") as f:
        cfg = tomllib.load(f)
    key = cfg.get("AUTHOREDUP_API_KEY")
    if not key:
        print("❌ AUTHOREDUP_API_KEY not set in .streamlit/secrets.toml")
        sys.exit(1)
    return key


def _describe(data, label: str) -> None:
    """Print a redacted shape summary of the response."""
    print(f"  ↳ {label}")
    if isinstance(data, dict):
        keys = list(data.keys())
        print(f"     top-level keys: {keys}")
        # Look for an items array
        items_key = None
        for k in ("items", "data", "results", "posts"):
            if k in data and isinstance(data[k], list):
                items_key = k
                break
        if items_key and data[items_key]:
            first = data[items_key][0]
            print(f"     '{items_key}' array length: {len(data[items_key])}")
            if isinstance(first, dict):
                print(f"     first item keys: {list(first.keys())}")
                # Pretty-print the first item, truncated
                sample = json.dumps(first, indent=2, default=str)
                if len(sample) > 2000:
                    sample = sample[:2000] + "\n     ... (truncated)"
                print("     first item sample:")
                for line in sample.splitlines():
                    print(f"       {line}")
        else:
            sample = json.dumps(data, indent=2, default=str)
            if len(sample) > 1500:
                sample = sample[:1500] + "\n     ... (truncated)"
            for line in sample.splitlines():
                print(f"     {line}")
    elif isinstance(data, list):
        print(f"     list of {len(data)} items")
        if data:
            print(f"     first item keys: {list(data[0].keys()) if isinstance(data[0], dict) else type(data[0]).__name__}")


def _status_icon(code: int) -> str:
    if code == 200:
        return "✅"
    if code == 401 or code == 403:
        return "🔒"
    if code == 404:
        return "❌"
    return "⚠️"


def main():
    key = _load_key()
    print(f"Loaded API key (length {len(key)}, prefix {key[:4]}...)\n")

    found_working = False

    # First: check if any base URL is reachable WITHOUT auth (basic ping)
    print("--- Step 1: are any base URLs reachable at all? ---")
    for base in BASES:
        try:
            r = requests.get(base, timeout=10)
            print(f"  {_status_icon(r.status_code)} {r.status_code}  {base}  body[:80]: {r.text[:80]!r}")
        except requests.RequestException as e:
            print(f"  💥 {base}: {e}")
    print()

    # Then: try each auth variant against each base + path until we get a non-404
    print("--- Step 2: probing endpoints with different auth headers ---\n")
    for auth_name, auth_fn in AUTH_VARIANTS:
        headers = auth_fn(key)
        print(f"### Auth: {auth_name}")
        any_non_404 = False
        for base in BASES:
            for path in CANDIDATE_PATHS:
                url = f"{base}{path}"
                try:
                    r = requests.get(url, headers=headers, params={"limit": 2}, timeout=10)
                except requests.RequestException as e:
                    continue
                if r.status_code == 404:
                    continue
                any_non_404 = True
                icon = _status_icon(r.status_code)
                rl = r.headers.get("x-ratelimit-remaining", "—")
                print(f"  {icon} {r.status_code}  {base}{path}  (rate-limit: {rl})")
                if r.status_code == 200:
                    try:
                        data = r.json()
                        _describe(data, label=path)
                    except json.JSONDecodeError:
                        print(f"     (non-JSON: {r.text[:200]})")
                    found_working = True
                elif r.status_code in (401, 403):
                    print(f"     body: {r.text[:200]}")
                elif r.status_code >= 400:
                    print(f"     body: {r.text[:200]}")
        if not any_non_404:
            print("  (all 404 with this auth scheme)")
        print()
        if found_working:
            break

    if not found_working:
        print("\n--- No endpoint succeeded. ---")
        print("Likely causes:")
        print("  1. AuthoredUp API key needs to be requested separately from the subscription")
        print("     (check Settings → API in their UI for a key-generation page)")
        print("  2. The API uses a different base URL we haven't tried")
        print("  3. The key shown is actually a different credential type (e.g., session token)")
        print("\nNext step: check AuthoredUp's settings page or contact their support for the exact API base + auth format.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Headless refresh of every dashboard source. Intended for launchd/cron.

    python scripts/refresh_all.py            # only sources older than 24h
    python scripts/refresh_all.py --force    # everything, regardless of age
    python scripts/refresh_all.py --tasks    # task sources only

Reads credentials from `.streamlit/secrets.toml` — the same file Streamlit uses,
so there is no second place to keep tokens in sync.

Exit codes: 0 all good, 1 at least one source failed, 2 could not start.
"""
from __future__ import annotations

import argparse
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from ingest.refresh_all import TASK_SOURCES, refresh_all  # noqa: E402
from store import db  # noqa: E402

SECRETS = REPO / ".streamlit" / "secrets.toml"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true", help="refresh even if fresh")
    ap.add_argument("--tasks", action="store_true", help="task sources only")
    ap.add_argument("--max-age-hours", type=float, default=None)
    args = ap.parse_args()

    if not SECRETS.exists():
        print(f"ERROR: {SECRETS} not found", file=sys.stderr)
        return 2

    secrets = tomllib.loads(SECRETS.read_text())
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"[{stamp}] refresh starting (force={args.force}, tasks_only={args.tasks})")

    report = refresh_all(
        secrets,
        progress=lambda m: print(f"  {m}", flush=True),
        only_stale=not args.force,
        max_age_hours=args.max_age_hours,
        names=TASK_SOURCES if args.tasks else None,
    )

    print(report.summary())
    for table in ("contacts", "companies", "deals", "tasks", "asana_tasks",
                  "slack_items", "outlook_messages"):
        try:
            print(f"    {table:18} {db._count(table):6}")
        except Exception:
            pass

    if report.failed:
        print(f"FAILED: {len(report.failed)} source(s)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

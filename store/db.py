"""SQLite cache for HubSpot pulls.

We refresh from HubSpot on a TTL (default 1 hour) and keep a local copy so
dashboard reloads don't burn API quota. Schema is intentionally narrow — we
only persist the columns the dashboard reads.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "cache.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
    id TEXT PRIMARY KEY,
    email TEXT,
    firm_type TEXT,
    lifecyclestage TEXT,
    createdate TEXT,
    recent_conversion_event_name TEXT,
    first_conversion_event_name TEXT,
    hs_analytics_source TEXT,
    hs_analytics_source_data_1 TEXT,
    hs_analytics_source_data_2 TEXT,
    num_conversion_events INTEGER,
    company_id TEXT,
    fetched_at TEXT
);

CREATE TABLE IF NOT EXISTS companies (
    id TEXT PRIMARY KEY,
    name TEXT,
    domain TEXT,
    firm_type TEXT,
    fetched_at TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE INDEX IF NOT EXISTS idx_contacts_firm_type ON contacts(firm_type);
CREATE INDEX IF NOT EXISTS idx_contacts_createdate ON contacts(createdate);
CREATE INDEX IF NOT EXISTS idx_contacts_company ON contacts(company_id);
CREATE INDEX IF NOT EXISTS idx_companies_firm_type ON companies(firm_type);
"""


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_meta(key: str) -> str | None:
    with connect() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None


def set_meta(key: str, value: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def upsert_contacts(rows: list[dict]) -> None:
    if not rows:
        return
    cols = [
        "id", "email", "firm_type", "lifecyclestage", "createdate",
        "recent_conversion_event_name", "first_conversion_event_name",
        "hs_analytics_source", "hs_analytics_source_data_1",
        "hs_analytics_source_data_2", "num_conversion_events",
        "company_id", "fetched_at",
    ]
    placeholders = ", ".join("?" for _ in cols)
    updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "id")
    sql = (
        f"INSERT INTO contacts ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT(id) DO UPDATE SET {updates}"
    )
    with connect() as conn:
        conn.executemany(sql, [tuple(r.get(c) for c in cols) for r in rows])


def upsert_companies(rows: list[dict]) -> None:
    if not rows:
        return
    cols = ["id", "name", "domain", "firm_type", "fetched_at"]
    placeholders = ", ".join("?" for _ in cols)
    updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "id")
    sql = (
        f"INSERT INTO companies ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT(id) DO UPDATE SET {updates}"
    )
    with connect() as conn:
        conn.executemany(sql, [tuple(r.get(c) for c in cols) for r in rows])

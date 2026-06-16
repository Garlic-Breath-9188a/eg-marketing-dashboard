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
    hs_lead_status TEXT,
    createdate TEXT,
    recent_conversion_event_name TEXT,
    first_conversion_event_name TEXT,
    hs_analytics_source TEXT,
    hs_analytics_source_data_1 TEXT,
    hs_analytics_source_data_2 TEXT,
    num_conversion_events INTEGER,
    num_associated_deals INTEGER,
    notes_last_contacted TEXT,
    hs_email_last_open_date TEXT,
    hs_email_last_click_date TEXT,
    hubspot_owner_id TEXT,
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

CREATE TABLE IF NOT EXISTS forms (
    id TEXT PRIMARY KEY,
    name TEXT,
    fetched_at TEXT
);

CREATE TABLE IF NOT EXISTS form_submissions (
    conversion_id TEXT PRIMARY KEY,
    form_id TEXT,
    contact_email TEXT,
    submitted_at TEXT,
    fetched_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_submissions_form ON form_submissions(form_id);
CREATE INDEX IF NOT EXISTS idx_submissions_email ON form_submissions(contact_email);

CREATE TABLE IF NOT EXISTS dismissed_signals (
    signal_key TEXT PRIMARY KEY,
    dismissed_on TEXT  -- ISO date (YYYY-MM-DD) — signal hidden through this date inclusive
);

CREATE TABLE IF NOT EXISTS linkedin_actors (
    id TEXT PRIMARY KEY,
    type TEXT,            -- 'profile' | 'company' | 'group'
    name TEXT,
    fetched_at TEXT
);

CREATE TABLE IF NOT EXISTS linkedin_posts (
    urn TEXT PRIMARY KEY,
    actor_id TEXT,
    actor_name TEXT,
    text TEXT,
    content_type TEXT,
    published_at TEXT,
    reaction_count INTEGER,
    comment_count INTEGER,
    share_count INTEGER,
    impression_count INTEGER,
    save_count INTEGER,
    send_count INTEGER,
    members_reached_count INTEGER,
    profile_view_count INTEGER,
    followers_gained_count INTEGER,
    engagement_rate REAL,
    word_count INTEGER,
    fetched_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_linkedin_posts_actor ON linkedin_posts(actor_id);
CREATE INDEX IF NOT EXISTS idx_linkedin_posts_published ON linkedin_posts(published_at);

CREATE TABLE IF NOT EXISTS deals (
    id TEXT PRIMARY KEY,
    name TEXT,
    amount REAL,
    dealstage TEXT,
    pipeline TEXT,
    closedate TEXT,
    createdate TEXT,
    hubspot_owner_id TEXT,
    primary_contact_id TEXT,
    primary_company_id TEXT,
    hubspot_url TEXT,
    stage_is_closed INTEGER,
    stage_is_won INTEGER,
    stage_label TEXT,
    fetched_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_deals_stage ON deals(dealstage);
CREATE INDEX IF NOT EXISTS idx_deals_owner ON deals(hubspot_owner_id);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    subject TEXT,
    status TEXT,
    priority TEXT,
    task_type TEXT,
    due_at TEXT,
    completed_at TEXT,
    hubspot_owner_id TEXT,
    associated_deal_ids TEXT,      -- comma-separated
    associated_contact_ids TEXT,   -- comma-separated
    fetched_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(due_at);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);

CREATE TABLE IF NOT EXISTS wordpress_posts (
    id TEXT PRIMARY KEY,
    title TEXT,
    slug TEXT,
    url TEXT,
    status TEXT,
    published_at TEXT,
    modified_at TEXT,
    author_id TEXT,
    author_name TEXT,
    categories TEXT,  -- comma-separated names
    tags TEXT,         -- comma-separated names
    excerpt TEXT,
    word_count INTEGER,
    views_30d INTEGER,
    views_all_time INTEGER,
    fetched_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_wp_published ON wordpress_posts(published_at);
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
    _migrate()


def _migrate() -> None:
    """Add columns to existing tables when the schema evolves.

    SQLite's CREATE TABLE IF NOT EXISTS won't add new columns to an already-existing table,
    so we explicitly check and ALTER TABLE for newly added columns.
    """
    new_contact_cols = {
        "hs_lead_status": "TEXT",
        "num_associated_deals": "INTEGER",
        "notes_last_contacted": "TEXT",
        "hs_email_last_open_date": "TEXT",
        "hs_email_last_click_date": "TEXT",
        "hubspot_owner_id": "TEXT",
        "hubspot_url": "TEXT",
    }
    new_deal_cols = {
        "stage_is_closed": "INTEGER",
        "stage_is_won": "INTEGER",
        "stage_label": "TEXT",
    }
    with connect() as conn:
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(contacts)")}
        for col, col_type in new_contact_cols.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE contacts ADD COLUMN {col} {col_type}")
        existing_deal_cols = {row["name"] for row in conn.execute("PRAGMA table_info(deals)")}
        for col, col_type in new_deal_cols.items():
            if col not in existing_deal_cols:
                conn.execute(f"ALTER TABLE deals ADD COLUMN {col} {col_type}")


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
        "id", "email", "firm_type", "lifecyclestage", "hs_lead_status", "createdate",
        "recent_conversion_event_name", "first_conversion_event_name",
        "hs_analytics_source", "hs_analytics_source_data_1",
        "hs_analytics_source_data_2", "num_conversion_events", "num_associated_deals",
        "notes_last_contacted", "hs_email_last_open_date", "hs_email_last_click_date",
        "hubspot_owner_id", "hubspot_url", "company_id", "fetched_at",
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


def upsert_forms(rows: list[dict]) -> None:
    if not rows:
        return
    cols = ["id", "name", "fetched_at"]
    placeholders = ", ".join("?" for _ in cols)
    updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "id")
    sql = (
        f"INSERT INTO forms ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT(id) DO UPDATE SET {updates}"
    )
    with connect() as conn:
        conn.executemany(sql, [tuple(r.get(c) for c in cols) for r in rows])


def upsert_form_submissions(rows: list[dict]) -> None:
    if not rows:
        return
    cols = ["conversion_id", "form_id", "contact_email", "submitted_at", "fetched_at"]
    placeholders = ", ".join("?" for _ in cols)
    updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "conversion_id")
    sql = (
        f"INSERT INTO form_submissions ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT(conversion_id) DO UPDATE SET {updates}"
    )
    with connect() as conn:
        conn.executemany(sql, [tuple(r.get(c) for c in cols) for r in rows])


def delete_contacts_not_in(ids: set[str]) -> int:
    """Delete contacts in local cache that are NOT in the given set of HubSpot IDs.

    Used after a full refresh to remove contacts that were deleted/archived in HubSpot.
    Returns the number of rows deleted.
    """
    if not ids:
        return 0
    with connect() as conn:
        # SQLite IN clause has limits — chunk if needed (we likely have <10k contacts).
        placeholders = ",".join("?" for _ in ids)
        result = conn.execute(
            f"DELETE FROM contacts WHERE id NOT IN ({placeholders})",
            tuple(ids),
        )
        return result.rowcount


def count_contacts() -> int:
    with connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM contacts").fetchone()
        return int(row["n"])


def _count(table: str) -> int:
    with connect() as conn:
        row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
        return int(row["n"])


def count_deals() -> int:
    return _count("deals")


def count_tasks() -> int:
    return _count("tasks")


def _delete_not_in(table: str, ids: set[str]) -> int:
    """Delete rows in `table` whose id is NOT in the given set (removes records
    deleted/archived in HubSpot since the last pull). Returns rows deleted."""
    if not ids:
        return 0
    with connect() as conn:
        placeholders = ",".join("?" for _ in ids)
        result = conn.execute(
            f"DELETE FROM {table} WHERE id NOT IN ({placeholders})",
            tuple(ids),
        )
        return result.rowcount


def delete_deals_not_in(ids: set[str]) -> int:
    return _delete_not_in("deals", ids)


def delete_tasks_not_in(ids: set[str]) -> int:
    return _delete_not_in("tasks", ids)


def dismiss_signal(signal_key: str, dismissed_on: str) -> None:
    """Mark a signal as dismissed for the given date (YYYY-MM-DD)."""
    with connect() as conn:
        conn.execute(
            "INSERT INTO dismissed_signals (signal_key, dismissed_on) VALUES (?, ?) "
            "ON CONFLICT(signal_key) DO UPDATE SET dismissed_on=excluded.dismissed_on",
            (signal_key, dismissed_on),
        )


def active_dismissals(today: str) -> set[str]:
    """Return signal_keys that are dismissed and still hidden as of today (YYYY-MM-DD)."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT signal_key FROM dismissed_signals WHERE dismissed_on >= ?",
            (today,),
        ).fetchall()
    return {r["signal_key"] for r in rows}


def upsert_linkedin_actors(rows: list[dict]) -> None:
    if not rows:
        return
    cols = ["id", "type", "name", "fetched_at"]
    placeholders = ", ".join("?" for _ in cols)
    updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "id")
    sql = (
        f"INSERT INTO linkedin_actors ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT(id) DO UPDATE SET {updates}"
    )
    with connect() as conn:
        conn.executemany(sql, [tuple(r.get(c) for c in cols) for r in rows])


def upsert_linkedin_posts(rows: list[dict]) -> None:
    if not rows:
        return
    cols = [
        "urn", "actor_id", "actor_name", "text", "content_type", "published_at",
        "reaction_count", "comment_count", "share_count", "impression_count",
        "save_count", "send_count", "members_reached_count", "profile_view_count",
        "followers_gained_count", "engagement_rate", "word_count", "fetched_at",
    ]
    placeholders = ", ".join("?" for _ in cols)
    updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "urn")
    sql = (
        f"INSERT INTO linkedin_posts ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT(urn) DO UPDATE SET {updates}"
    )
    with connect() as conn:
        conn.executemany(sql, [tuple(r.get(c) for c in cols) for r in rows])


def upsert_deals(rows: list[dict]) -> None:
    if not rows:
        return
    cols = [
        "id", "name", "amount", "dealstage", "pipeline", "closedate", "createdate",
        "hubspot_owner_id", "primary_contact_id", "primary_company_id", "hubspot_url",
        "stage_is_closed", "stage_is_won", "stage_label",
        "fetched_at",
    ]
    placeholders = ", ".join("?" for _ in cols)
    updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "id")
    sql = (
        f"INSERT INTO deals ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT(id) DO UPDATE SET {updates}"
    )
    with connect() as conn:
        conn.executemany(sql, [tuple(r.get(c) for c in cols) for r in rows])


def upsert_tasks(rows: list[dict]) -> None:
    if not rows:
        return
    cols = [
        "id", "subject", "status", "priority", "task_type", "due_at", "completed_at",
        "hubspot_owner_id", "associated_deal_ids", "associated_contact_ids", "fetched_at",
    ]
    placeholders = ", ".join("?" for _ in cols)
    updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "id")
    sql = (
        f"INSERT INTO tasks ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT(id) DO UPDATE SET {updates}"
    )
    with connect() as conn:
        conn.executemany(sql, [tuple(r.get(c) for c in cols) for r in rows])


def upsert_wordpress_posts(rows: list[dict]) -> None:
    if not rows:
        return
    cols = [
        "id", "title", "slug", "url", "status", "published_at", "modified_at",
        "author_id", "author_name", "categories", "tags", "excerpt", "word_count",
        "views_30d", "views_all_time", "fetched_at",
    ]
    placeholders = ", ".join("?" for _ in cols)
    updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "id")
    sql = (
        f"INSERT INTO wordpress_posts ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT(id) DO UPDATE SET {updates}"
    )
    with connect() as conn:
        conn.executemany(sql, [tuple(r.get(c) for c in cols) for r in rows])

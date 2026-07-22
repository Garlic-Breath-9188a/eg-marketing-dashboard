"""Which HubSpot deals are open, and which tasks are real work.

Extracted from `app.py` so the Tasks page can apply the same rules. Both are
harder than they look in this portal and the history is worth keeping:

* **Open deals.** The portal uses custom numeric stage IDs across three
  pipelines, so the literal `closedwon`/`closedlost` test matched nothing and
  every deal read as open. The per-deal `hs_is_closed` flag (stored as
  `stage_is_closed`) is authoritative, but a Closed Lost stage in one custom
  pipeline was not setting it, so the stage *label* is checked too. Every known
  signal is OR-ed together — a cache refreshed before any one of them was
  ingested still filters correctly.
* **Active tasks.** New HubSpot portals ship demo rows titled "(Sample task) …"
  which otherwise pollute the overdue queue.
"""
from __future__ import annotations

import pandas as pd

# Legacy default-pipeline literals.
CLOSED_STAGES = {"closedwon", "closedlost"}

# Known Closed Won/Lost stage IDs across this portal's 3 pipelines (WTIS,
# Vendor/Research, Wealth Management). Fallback for caches refreshed before the
# per-deal hs_is_closed flag was ingested.
CLOSED_STAGE_IDS = {
    "1317293194", "1317293195",
    "1317544073", "1317544074",
    "1317694355", "1317694356",
}

# HubSpot task statuses that mean "not on my plate".
INACTIVE_TASK_STATUSES = {"COMPLETED", "DEFERRED"}

SAMPLE_TASK_PREFIX = "(Sample task)"


def open_deals(deals: pd.DataFrame) -> pd.DataFrame:
    """Return deals that are not closed, by any available signal."""
    if deals.empty:
        return pd.DataFrame()

    closed = pd.Series(False, index=deals.index)
    if "stage_is_closed" in deals.columns:
        closed = deals["stage_is_closed"].fillna(0).astype(int) == 1
    if "stage_label" in deals.columns:
        closed = closed | deals["stage_label"].fillna("").str.contains(
            "closed", case=False, na=False
        )
    if "dealstage" in deals.columns:
        ds = deals["dealstage"].fillna("").astype(str)
        closed = closed | ds.str.lower().isin(CLOSED_STAGES) | ds.isin(CLOSED_STAGE_IDS)
    return deals[~closed].copy()


def active_tasks(tasks: pd.DataFrame) -> pd.DataFrame:
    """Return HubSpot tasks that are still outstanding real work."""
    if tasks.empty or "status" not in tasks.columns or "due_at" not in tasks.columns:
        return pd.DataFrame()

    out = tasks[
        ~tasks["status"].fillna("").str.upper().isin(INACTIVE_TASK_STATUSES)
    ].copy()
    if "subject" in out.columns:
        out = out[~out["subject"].fillna("").str.startswith(SAMPLE_TASK_PREFIX)]
    return out

"""Refresh every source, skipping the ones that are still fresh.

One orchestrator serving two callers:

* `scripts/refresh_all.py` — headless, for a launchd/cron job on the Mac.
* `app.py` — on page load, so the deployed Streamlit Cloud instance stays
  current without any external scheduler. Cloud has no persistent disk and no
  cron, so its SQLite cache starts empty on every redeploy; auto-refresh is the
  only thing that can populate it.

Each source is independent: one failing (an expired token, a missing scope)
must not stop the others. Failures are collected and reported, never raised.

The per-source `progress` callbacks have inconsistent signatures — HubSpot's
takes `(stage, current, total)`, the rest take a single message — so everything
is funnelled through a `*args` adapter here rather than changing five modules.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ingest import asana, authoredup, hubspot, outlook, slack, wordpress
from store import db


@dataclass
class SourceSpec:
    name: str
    meta_key: str
    # (secrets, progress) -> dict of counts
    run: object
    # Secret keys that must be present, else the source is skipped as unconfigured.
    requires: tuple[str, ...] = ()


SOURCES: list[SourceSpec] = [
    SourceSpec(
        "HubSpot", "last_full_refresh",
        lambda s, p: hubspot.refresh(s["HUBSPOT_TOKEN"], progress=p),
        ("HUBSPOT_TOKEN",),
    ),
    SourceSpec(
        "Asana", "last_asana_refresh",
        lambda s, p: asana.refresh(s["ASANA_ACCESS_TOKEN"], progress=p),
        ("ASANA_ACCESS_TOKEN",),
    ),
    SourceSpec(
        "Slack", "last_slack_refresh",
        lambda s, p: slack.refresh(s["SLACK_TOKEN"], progress=p),
        ("SLACK_TOKEN",),
    ),
    SourceSpec(
        "Outlook", "last_outlook_refresh",
        lambda s, p: outlook.refresh(s, progress=p),
        ("MS_CLIENT_ID", "MS_TENANT_ID", "MS_CLIENT_SECRET", "MS_REFRESH_TOKEN"),
    ),
    SourceSpec(
        "AuthoredUp", "last_linkedin_refresh",
        lambda s, p: authoredup.refresh(s["AUTHOREDUP_API_KEY"], progress=p),
        ("AUTHOREDUP_API_KEY",),
    ),
    SourceSpec(
        "WordPress", "last_wordpress_refresh",
        lambda s, p: wordpress.refresh(dict(s), progress=p),
        ("WORDPRESS_BASE_URL",),
    ),
]

# Task sources only — what the Tasks page and briefing depend on. AuthoredUp and
# WordPress move slowly and are not worth blocking a page load for.
TASK_SOURCES = {"HubSpot", "Asana", "Slack", "Outlook"}


@dataclass
class RefreshReport:
    refreshed: dict[str, dict] = field(default_factory=dict)
    skipped_fresh: list[str] = field(default_factory=list)
    skipped_unconfigured: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)

    @property
    def did_anything(self) -> bool:
        return bool(self.refreshed or self.failed)

    def summary(self) -> str:
        parts = []
        for name, counts in self.refreshed.items():
            detail = ", ".join(f"{v} {k}" for k, v in counts.items() if isinstance(v, int))
            parts.append(f"{name} ({detail})" if detail else name)
        line = "Refreshed: " + ("; ".join(parts) if parts else "nothing")
        if self.failed:
            line += " · Failed: " + ", ".join(f"{k} ({v})" for k, v in self.failed.items())
        if self.skipped_fresh:
            line += " · Still fresh: " + ", ".join(self.skipped_fresh)
        if self.skipped_unconfigured:
            line += " · Not configured: " + ", ".join(self.skipped_unconfigured)
        return line


def refresh_all(
    secrets,
    progress=None,
    *,
    only_stale: bool = True,
    max_age_hours: float | None = None,
    names: set[str] | None = None,
) -> RefreshReport:
    """Refresh sources. Returns a report; never raises on a source failure."""
    db.init_db()
    threshold = db.STALE_AFTER_HOURS if max_age_hours is None else max_age_hours
    report = RefreshReport()

    def _emit(message: str) -> None:
        if progress:
            progress(message)

    for spec in SOURCES:
        if names is not None and spec.name not in names:
            continue
        if any(not secrets.get(k) for k in spec.requires):
            report.skipped_unconfigured.append(spec.name)
            continue

        age = db.age_hours(spec.meta_key)
        if only_stale and age is not None and age <= threshold:
            report.skipped_fresh.append(spec.name)
            continue

        _emit(f"Refreshing {spec.name}…")
        try:
            # Adapter: absorbs both progress signatures without touching callers.
            report.refreshed[spec.name] = spec.run(secrets, lambda *a: None)
        except Exception as e:  # noqa: BLE001 — one bad source must not stop the rest
            report.failed[spec.name] = f"{type(e).__name__}: {e}"[:160]
            _emit(f"{spec.name} failed — continuing")

    return report

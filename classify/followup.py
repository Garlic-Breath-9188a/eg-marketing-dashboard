"""How overdue a prospect follow-up is.

Business days, not calendar days: a Friday afternoon call followed up on Monday
morning is fine, and a calendar-day threshold would flag it red over a weekend
where nothing was expected to happen.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

# Craig's chosen thresholds: green under 2 business days, amber 2–4, red past 4.
AMBER_AFTER_BUSINESS_DAYS = 2
RED_AFTER_BUSINESS_DAYS = 4


def business_days_between(start: date, end: date) -> int:
    """Weekdays from `start` to `end`, excluding the start day itself.

    Holidays are not modelled — Ezra Group is small and Craig's calendar doesn't
    follow a fixed holiday schedule, so a fixed list would be wrong more often
    than the weekend rule is.
    """
    if end <= start:
        return 0
    days = 0
    cursor = start
    while cursor < end:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:  # Mon–Fri
            days += 1
    return days


@dataclass(frozen=True)
class FollowUpStatus:
    business_days: int
    level: str      # "sent" | "ok" | "due" | "overdue"
    label: str

    @property
    def needs_attention(self) -> bool:
        return self.level in {"due", "overdue"}


def status(call_at: datetime | None, followed_up_at: datetime | None,
           *, now: datetime | None = None) -> FollowUpStatus:
    """Classify one prospect call's follow-up state."""
    now = now or datetime.now(timezone.utc)

    if followed_up_at is not None:
        elapsed = business_days_between(call_at.date(), followed_up_at.date()) if call_at else 0
        if elapsed == 0:
            return FollowUpStatus(0, "sent", "Followed up same day")
        return FollowUpStatus(
            elapsed, "sent", f"Followed up after {elapsed} business day"
            + ("s" if elapsed != 1 else "")
        )

    if call_at is None:
        return FollowUpStatus(0, "due", "No call date recorded")

    elapsed = business_days_between(call_at.date(), now.date())
    if elapsed > RED_AFTER_BUSINESS_DAYS:
        return FollowUpStatus(elapsed, "overdue", f"{elapsed} business days, no follow-up")
    if elapsed >= AMBER_AFTER_BUSINESS_DAYS:
        return FollowUpStatus(elapsed, "due", f"{elapsed} business days, no follow-up")
    if elapsed == 0:
        return FollowUpStatus(0, "ok", "Called today")
    return FollowUpStatus(
        elapsed, "ok", f"{elapsed} business day" + ("s" if elapsed != 1 else "") + " ago"
    )

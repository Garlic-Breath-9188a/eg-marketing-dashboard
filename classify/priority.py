"""Rank tasks from any source onto one comparable scale.

Ported from `computeSmartPriority` in the Roland Express server. **Lower score
sorts first** — it reads as "distance from needing attention", not importance.

The curve is deliberately not monotonic in days-overdue. Something due today
outranks something three days overdue, and a task a month late sorts below one
due next week. That is intentional: badly overdue items are usually stalled or
abandoned rather than urgent, and letting them permanently occupy the top of the
list is what made the old dashboard easy to ignore.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

# (inclusive_low, inclusive_high, score, tier). None = unbounded.
# Ordered most-urgent first; the first matching band wins.
_BANDS: list[tuple[int | None, int | None, int, str]] = [
    (0, 0, 0, "today"),
    (1, 1, 20, "soon"),
    (-3, -1, 40, "recent-overdue"),
    (2, 3, 50, "soon"),
    (-7, -4, 70, "overdue"),
    (4, 7, 80, "upcoming"),
    (-14, -8, 120, "stale"),
    (8, None, 130, "future"),
    (-30, -15, 150, "stale"),
    (None, -31, 200, "very-stale"),
]

# Lower bonus = higher priority. Unknown priorities fall through to medium.
_PRIORITY_BONUS = {
    "critical": 0,
    "high": 5,
    "important": 5,
    "medium": 10,
    "low": 15,
}
_DEFAULT_BONUS = 10

# Score for an item with no date at all — worse than anything due this week,
# better than anything badly overdue.
_NO_DATE_SCORE = 100


@dataclass(frozen=True)
class Priority:
    score: int
    label: str
    tier: str

    @property
    def is_overdue(self) -> bool:
        return self.tier in {"recent-overdue", "overdue", "stale", "very-stale"}


def _band(diff_days: int) -> tuple[int, str]:
    for low, high, score, tier in _BANDS:
        if (low is None or diff_days >= low) and (high is None or diff_days <= high):
            return score, tier
    return _NO_DATE_SCORE, "none"


def _label(diff_days: int, date_word: str) -> str:
    if diff_days == 0:
        return f"{date_word} today"
    if diff_days == 1:
        return f"{date_word} tomorrow"
    if diff_days > 1:
        return f"{date_word} in {diff_days}d"
    return f"{abs(diff_days)}d overdue"


def _is_missing(value) -> bool:
    """True for None and for pandas NaT/NaN, without importing pandas.

    Callers pull dates out of dataframes, where "no date" arrives as NaT rather
    than None. NaT and NaN are the only values that compare unequal to
    themselves, which is what makes this check work.
    """
    return value is None or value != value


def compute(
    due: date | None,
    priority: str | None = None,
    *,
    today: date | None = None,
    is_deal: bool = False,
) -> Priority:
    """Score one item. `is_deal` switches the wording to "Close" for deals."""
    date_word = "Close" if is_deal else "Due"
    bonus = _PRIORITY_BONUS.get((priority or "").strip().lower(), _DEFAULT_BONUS)

    if _is_missing(due):
        no_date = "No close date" if is_deal else "No due date"
        return Priority(_NO_DATE_SCORE + bonus, no_date, "none")

    diff = (due - (today or date.today())).days
    score, tier = _band(diff)
    return Priority(score + bonus, _label(diff, date_word), tier)

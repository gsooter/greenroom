"""Shared date-parsing helpers for venue calendar scrapers.

Most venues that publish only a human-readable calendar date (e.g.
``Apr 17``, ``May 3 - 7:00 PM``) omit the year entirely, since the
site author assumes readers look at the calendar in the current week.
For scraping we need full datetimes, so this module centralizes the
year-inference and common format parsing so every venue scraper
handles it the same way.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

MONTH_NUMBERS: dict[str, int] = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


def infer_year(
    month: int,
    day: int,
    *,
    today: date | None = None,
    grace_days: int = 3,
) -> int:
    """Choose the most likely year for a month/day from a rolling calendar.

    Venues that publish year-less "upcoming shows" calendars sometimes
    leave last night's show on the page for a day or two. Without a
    grace period, a naive "if past → next year" rule would push those
    shows into the following year. The default ``grace_days=3`` keeps
    very recent past dates in the current year so freshly-ended shows
    stay accurate; anything older than that is assumed to be next year.

    Args:
        month: 1-12 month value.
        day: 1-31 day value.
        today: Optional reference date. Defaults to ``date.today()`` so
            callers and tests can control the rollover behavior.
        grace_days: Number of days before ``today`` that should still
            be treated as current-year. Defaults to 3.

    Returns:
        The year that best matches the date when read as "upcoming."
    """
    reference = today or date.today()
    threshold = reference - timedelta(days=grace_days)
    current_year = reference.year
    try:
        candidate = date(current_year, month, day)
    except ValueError:
        return current_year
    if candidate < threshold:
        return current_year + 1
    return current_year


def parse_month_name(value: str) -> int | None:
    """Convert a month name or abbreviation to a 1-12 integer.

    Args:
        value: Month name (case-insensitive), full or abbreviated.

    Returns:
        The 1-12 month number, or None when the input is unrecognized.
    """
    if not isinstance(value, str):
        return None
    return MONTH_NUMBERS.get(value.strip().lower().rstrip("."))


_TIME_PATTERN = re.compile(
    r"(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<meridiem>[AaPp][Mm])"
)


def parse_clock_time(value: str | None) -> tuple[int, int] | None:
    """Parse a human clock time like ``8 pm`` or ``10:30 PM`` to (hour, minute).

    Args:
        value: Raw time string scraped from the page. May be None.

    Returns:
        Tuple of (hour, minute) in 24-hour form, or None when the
        string cannot be parsed.
    """
    if not value:
        return None
    match = _TIME_PATTERN.search(value)
    if match is None:
        return None
    hour = int(match.group("hour"))
    minute = int(match.group("minute") or 0)
    meridiem = match.group("meridiem").lower()
    if meridiem == "pm" and hour != 12:
        hour += 12
    if meridiem == "am" and hour == 12:
        hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def build_event_datetime(
    *,
    month: int,
    day: int,
    hour: int = 20,
    minute: int = 0,
    today: date | None = None,
) -> datetime:
    """Combine a calendar month/day with a time into a full ``datetime``.

    Uses :func:`infer_year` to pick the year, so this is only safe for
    upcoming-show calendars. Time defaults to 8:00 PM — a reasonable
    fallback for music venues when the page omits show time.

    Args:
        month: 1-12 month value.
        day: 1-31 day value.
        hour: 0-23 hour value. Defaults to 20 (8pm).
        minute: 0-59 minute value. Defaults to 0.
        today: Optional reference date for year inference. Defaults to
            ``date.today()``.

    Returns:
        A naive ``datetime`` in the venue's local time.
    """
    year = infer_year(month, day, today=today)
    return datetime(year, month, day, hour, minute)

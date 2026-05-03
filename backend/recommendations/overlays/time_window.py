"""Time-window overlay — boost shows that are coming up soon.

Concert ticket purchasing is concentrated in a 2-12 week window
before show date. The time-window overlay keeps that window flat at
1.0, lightly downweights mid-distance shows (3-12 months), and
heavily downweights far-future tour announcements.

Curve, in plain English:

* Tonight to 3 days out (last-minute discovery): 1.0
* 3 days to 2 weeks (this-week / next-week planning): 1.0
* 2 weeks to 3 months (peak ticket-buying window): 1.0
* 3 months to 6 months: 0.85 — still on the radar, less urgent
* 6 months to 12 months: 0.65 — announcement / on-sale awareness
* 12+ months out: 0.40 — mostly noise; far-future tours
* Past events: 0.0 — should never reach scoring; engine filters
  past events anyway

The flat 1.0 zone from tonight through 3 months is intentional.
Within that range, ranking is driven entirely by taste-match
strength, not by date — so a great show 8 weeks out shouldn't be
downweighted compared to a mediocre show next week.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from backend.data.models.events import Event

# Curve breakpoints, in days. Each tuple is ``(upper_bound_days,
# multiplier)`` and the table is consulted in order — the first
# bucket whose upper bound the event falls under is the answer.
# Past events use the explicit ``PAST_EVENT_MULTIPLIER`` below.
_CURVE_BREAKPOINTS: tuple[tuple[float, float], ...] = (
    (90.0, 1.0),  # 0-3 months: peak window, full weight
    (180.0, 0.85),  # 3-6 months: still relevant, less urgent
    (365.0, 0.65),  # 6-12 months: announcement / on-sale awareness
)

# Far-future shows beyond the last breakpoint get this minimum.
_FAR_FUTURE_MULTIPLIER: float = 0.4

# Past events should be filtered before scoring; we keep this
# constant for the engine's sanity-check filter and for any
# in-progress event the upstream catalog hasn't moved to PAST yet.
PAST_EVENT_MULTIPLIER: float = 0.0

__all__ = [
    "PAST_EVENT_MULTIPLIER",
    "compute_time_window_multiplier",
]


def compute_time_window_multiplier(event: Event, now: datetime) -> float:
    """Compute the time-window multiplier for ``event`` relative to ``now``.

    The function clips the comparison to whole days but keeps
    fractional precision below the day boundary so an event ending
    at 23:59 the day-of doesn't tip into the next bucket.

    Args:
        event: The candidate event. Must have ``starts_at``
            populated with a timezone-aware datetime; the engine
            never persists naive timestamps.
        now: Current datetime, supplied by the engine so tests can
            pin the reference point.

    Returns:
        Multiplier in ``[0.0, 1.0]``.

        * ``0.0`` for past events.
        * ``1.0`` for events within the next ~3 months.
        * ``0.85`` 3-6 months out.
        * ``0.65`` 6-12 months out.
        * ``0.40`` further out than 12 months.
    """
    starts_at = event.starts_at
    if starts_at is None:
        return _FAR_FUTURE_MULTIPLIER
    delta = starts_at - now
    days = delta.total_seconds() / 86400.0
    if days < 0:
        return PAST_EVENT_MULTIPLIER
    for upper_days, multiplier in _CURVE_BREAKPOINTS:
        if days <= upper_days:
            return multiplier
    return _FAR_FUTURE_MULTIPLIER

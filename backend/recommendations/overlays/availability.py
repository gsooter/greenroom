"""Availability overlay — downweight (but don't drop) sold-out shows.

The availability overlay reads ``event.status`` and applies a
multiplier between 0.0 and 1.0. Sold-out shows still surface (users
want awareness for resale tracking, waitlist signups, or simply
knowing what they missed) but rank below comparable available
shows. Cancelled events score 0.0 so the engine filter drops them.

The dictionary :data:`AVAILABILITY_MULTIPLIERS` is the single source
of truth for the per-state multiplier. New states must be added
deliberately — an unmapped state logs a warning and falls back to
1.0 rather than silently treating as zero, because the failure mode
of "treat unknown like sold out" would quietly suppress entire
classes of recommendation if the upstream catalog adds a new state
faster than this map gets updated.

Mapping rationale:

* ``available`` (1.0): full weight. The default state for every
  confirmed-available show.
* ``low`` (0.95) / ``very_low`` (0.90): "few left" / "almost sold
  out." Only a slight adjustment because scarcity creates urgency
  for some users and inconvenience for others — those effects
  largely cancel.
* ``sold_out`` (0.45): big penalty but still surfaces. Users want
  to know about shows they could chase via resale or waitlist.
* ``cancelled`` (0.0): not real anymore — drop entirely.
* ``postponed`` (0.6): real event, uncertain date. Moderate
  downweight that reflects the planning friction.
* ``unknown`` (0.85): no data. Light hedge so a missing-data event
  doesn't outrank a confirmed-available one.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from backend.data.models.events import EventStatus

if TYPE_CHECKING:
    from backend.data.models.events import Event

logger = logging.getLogger(__name__)

AVAILABILITY_MULTIPLIERS: dict[str, float] = {
    "available": 1.0,
    "low": 0.95,
    "very_low": 0.90,
    "sold_out": 0.45,
    "cancelled": 0.0,
    "postponed": 0.6,
    "unknown": 0.85,
}

# Default multiplier when an event's resolved availability state is
# not present in the table. Logged separately so we can find the
# unmapped states quickly without a guessing game.
_FALLBACK_MULTIPLIER: float = 1.0

__all__ = [
    "AVAILABILITY_MULTIPLIERS",
    "compute_availability_multiplier",
    "resolve_availability_state",
]


def resolve_availability_state(event: Event) -> str:
    """Map an event onto a key for :data:`AVAILABILITY_MULTIPLIERS`.

    Today this is a thin shim over ``event.status``: the
    :class:`~backend.data.models.events.EventStatus` enum carries
    the catalog-level state and the overlay table mirrors it. The
    helper exists as its own function so a future enrichment step
    (e.g. "few left" pulled from a SeatGeek snapshot) can change
    the resolution rule without touching the multiplier lookup.

    A ``None`` status maps to ``"unknown"`` (a small hedge), but
    every other unmapped value falls through to its string
    representation so the multiplier-lookup fallback in
    :func:`compute_availability_multiplier` fires and logs a
    warning. That's the trip-wire that surfaces "we added a new
    EventStatus and forgot to teach the overlay about it" issues.

    Args:
        event: The candidate event.

    Returns:
        The availability key.
    """
    status = getattr(event, "status", None)
    if status is None:
        return "unknown"
    if status == EventStatus.CANCELLED:
        return "cancelled"
    if status == EventStatus.SOLD_OUT:
        return "sold_out"
    if status == EventStatus.POSTPONED:
        return "postponed"
    if status == EventStatus.CONFIRMED:
        return "available"
    if status == EventStatus.PAST:
        # The engine filters past events before scoring, but if one
        # slips through the catalog still has a real status. Falling
        # through to ``"past"`` (which is not in the multiplier
        # table) trips the warn-and-fallback branch so the operator
        # sees the leak.
        return "past"
    # Anything else — unmapped EventStatus member, raw string, etc.
    # — is surfaced verbatim so the lookup fallback fires.
    return str(getattr(status, "value", status))


def compute_availability_multiplier(event: Event) -> float:
    """Compute the availability multiplier for ``event``.

    Reads the event's current status, maps it to an availability
    key via :func:`resolve_availability_state`, and returns the
    matching entry from :data:`AVAILABILITY_MULTIPLIERS`. An
    unmapped state logs a warning and returns 1.0 rather than
    crashing or silently treating as zero — see the module
    docstring for the rationale.

    Args:
        event: The candidate event.

    Returns:
        Multiplier in ``[0.0, 1.0]``.
    """
    state = resolve_availability_state(event)
    multiplier = AVAILABILITY_MULTIPLIERS.get(state)
    if multiplier is None:
        logger.warning(
            "availability_overlay.unmapped_state",
            extra={
                "event_id": getattr(event, "id", None),
                "state": state,
            },
        )
        return _FALLBACK_MULTIPLIER
    return multiplier

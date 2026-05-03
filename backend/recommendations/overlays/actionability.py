"""Actionability overlay — boost shows the user can actually attend.

The actionability overlay multiplies the combined per-event base
score by a value between 0.4 and 1.0 based on the event's location
relative to the user's preferred city and region. The intent is to
surface shows users will plausibly travel to without filtering out
the long tail entirely — a great show in a same-region city still
appears, ranked just below comparable in-city shows.

Multiplier table:

* :data:`CITY_MATCH_MULTIPLIER` (1.00) — event venue is in the
  user's preferred city.
* :data:`SAME_REGION_MULTIPLIER` (0.85) — different city, same
  region (e.g. DC user, Baltimore show). Light downweight; the city
  is still close enough to be plausible.
* :data:`DIFFERENT_REGION_MULTIPLIER` (0.40) — different region
  entirely. Strong downweight, never zero — a user who explicitly
  wants the other region can change their preferred city.
* :data:`NO_CITY_PREFERENCE_MULTIPLIER` (0.95) — user hasn't picked
  a preferred city. Slightly below 1.0 so users with a real
  preference always rank ahead of the "no preference" baseline once
  they set one.

The overlay reads the event's region via ``event.venue.city.region_id``
(Decision 061). The engine resolves the user's preferred-city region
once per scoring run and passes it into every event's overlay
computation, so the overlay never has to issue its own database
query — see :mod:`backend.recommendations.engine`.

The values are deliberately conservative. Even a 0.4 multiplier on
a strong recommendation can still rank higher than a 1.0 multiplier
on a weak one, so a Richmond show by an artist the user loves still
surfaces, just below DC shows by artists they love.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import uuid

    from backend.data.models.events import Event

logger = logging.getLogger(__name__)

CITY_MATCH_MULTIPLIER: float = 1.0
SAME_REGION_MULTIPLIER: float = 0.85
DIFFERENT_REGION_MULTIPLIER: float = 0.4
NO_CITY_PREFERENCE_MULTIPLIER: float = 0.95

__all__ = [
    "CITY_MATCH_MULTIPLIER",
    "DIFFERENT_REGION_MULTIPLIER",
    "NO_CITY_PREFERENCE_MULTIPLIER",
    "SAME_REGION_MULTIPLIER",
    "compute_actionability_multiplier",
]


def compute_actionability_multiplier(
    event: Event,
    user_preferred_city_id: uuid.UUID | None,
    user_preferred_city_region_id: uuid.UUID | None,
) -> float:
    """Compute the actionability multiplier for ``event`` and the user.

    Resolution order, short-circuiting on the first match:

    1. User has no preferred city set →
       :data:`NO_CITY_PREFERENCE_MULTIPLIER`.
    2. Event venue is in the user's preferred city →
       :data:`CITY_MATCH_MULTIPLIER`.
    3. Event venue's city is in the same region as the user's
       preferred city → :data:`SAME_REGION_MULTIPLIER`.
    4. Otherwise → :data:`DIFFERENT_REGION_MULTIPLIER`.

    The function is intentionally lookup-only and pure: the engine
    pre-loads ``event.venue.city`` (selectin/joined load) and resolves
    the user's region once at the start of the scoring run, so this
    helper never issues a database query.

    When the event's venue or city aren't loaded, or the city has no
    ``region_id`` recorded, the helper logs a warning and falls back
    to :data:`DIFFERENT_REGION_MULTIPLIER`. The post-migration
    invariant is that every city has a region, so a warning here is
    a real anomaly worth investigating, not a routine occurrence.

    Args:
        event: The candidate event being scored. Must have
            ``venue.city`` loaded (selectin/joined). The function
            falls back gracefully if not, but the engine should
            never call it with a partially-loaded event.
        user_preferred_city_id: UUID of the user's preferred city,
            or ``None`` when unset.
        user_preferred_city_region_id: UUID of the region the
            user's preferred city belongs to, or ``None`` when the
            user has no city preference. The engine resolves this
            once per scoring run and passes the same value into
            every event's overlay computation.

    Returns:
        Multiplier in the range ``[DIFFERENT_REGION_MULTIPLIER,
        CITY_MATCH_MULTIPLIER]`` to apply to the per-event base
        score.
    """
    if user_preferred_city_id is None:
        return NO_CITY_PREFERENCE_MULTIPLIER

    venue = getattr(event, "venue", None)
    city = getattr(venue, "city", None) if venue is not None else None
    if city is None:
        logger.warning(
            "actionability_overlay.event_missing_city",
            extra={"event_id": getattr(event, "id", None)},
        )
        return DIFFERENT_REGION_MULTIPLIER

    if city.id == user_preferred_city_id:
        return CITY_MATCH_MULTIPLIER

    event_region_id = getattr(city, "region_id", None)
    if event_region_id is None:
        logger.warning(
            "actionability_overlay.city_missing_region",
            extra={
                "event_id": getattr(event, "id", None),
                "city_id": city.id,
            },
        )
        return DIFFERENT_REGION_MULTIPLIER

    if (
        user_preferred_city_region_id is not None
        and event_region_id == user_preferred_city_region_id
    ):
        return SAME_REGION_MULTIPLIER

    return DIFFERENT_REGION_MULTIPLIER

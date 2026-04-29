"""Followed-venue scorer.

Scores events at venues the user has explicitly followed during
onboarding (Step 2) or anywhere else the follow control is exposed.

Distinct from :class:`VenueAffinityScorer`, which infers venue interest
from saved-event counts. Following is a deliberate "I care about what's
playing here" signal, separate from "I happened to bookmark a few shows
that turned out to be at the same venue." The two are additive — a user
who both follows DC9 *and* has saved three shows there gets boosted by
both scorers, which is the intended behavior.

Score weights are intentionally lower than artist-level signals so a
strong artist match at an unfamiliar venue still outranks a generic
"same venue you follow" hit. They sit slightly above
:class:`VenueAffinityScorer` because an explicit follow is a stronger
declaration than implicit save behavior.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import uuid

    from backend.data.models.events import Event

SCORER_NAME = "followed_venue"

# Single-tier score — every followed venue is worth the same boost.
# Saturating logic isn't useful here (you either follow it or you don't,
# unlike saved-event counts which range 1..N).
_FOLLOW_SCORE = 0.45

__all__ = ["SCORER_NAME", "FollowedVenueScorer"]


class FollowedVenueScorer:
    """Score events at venues the user has explicitly followed.

    Attributes:
        name: Scorer identifier used in the ``score_breakdown`` JSONB.
    """

    name: str = SCORER_NAME

    def __init__(self, followed_venues: dict[uuid.UUID, str]) -> None:
        """Bind the precomputed followed-venue id → name map.

        Args:
            followed_venues: Mapping of ``venue_id`` → display name for
                every venue the user follows. Pass an empty dict when
                the user follows no venues; the scorer abstains on
                every event.
        """
        self._followed = followed_venues

    def score(self, event: Event) -> dict[str, Any] | None:
        """Score one event for the bound user.

        Args:
            event: The candidate event.

        Returns:
            ``{score, matched_venue_id, matched_venue_name}`` when the
            event's venue is one the user follows, ``None`` otherwise.
        """
        if not self._followed:
            return None
        venue_id = getattr(event, "venue_id", None)
        if venue_id is None:
            return None
        name = self._followed.get(venue_id)
        if name is None:
            return None
        return {
            "score": _FOLLOW_SCORE,
            "matched_venue_id": str(venue_id),
            "matched_venue_name": name,
        }

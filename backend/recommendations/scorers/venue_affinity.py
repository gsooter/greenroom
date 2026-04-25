"""Venue-affinity scorer.

Boosts events at venues the user has previously saved shows from. The
intuition: if you've saved three shows at Black Cat, the next Black Cat
show is more relevant to you than a random show at a venue you've never
been to — even if the artist isn't in your music-service rotation.

This scorer fills a gap left by ``ArtistMatchScorer``: a user who
follows niche local acts that don't appear on Spotify, or who hasn't
connected a music service yet but is actively bookmarking shows, would
otherwise see an empty For-You feed.

Score weights are intentionally lower than artist matches (the strongest
signal) so a great artist match at an unfamiliar venue still outranks a
mediocre venue affinity hit. The curve saturates fast — three saved
shows is the same boost as ten — because past a few visits, the venue
is a known quantity and additional saves don't add much information.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import uuid

    from backend.data.models.events import Event

SCORER_NAME = "venue_affinity"

# Per-saved-show contribution. The first save is worth more than each
# subsequent — a single save means "I've heard of this venue", three+
# means "I keep coming back here", which is when the score plateaus.
_BASE_SCORE = 0.2
_PER_EXTRA_SAVE = 0.1
_MAX_SCORE = 0.4

__all__ = ["SCORER_NAME", "VenueAffinityScorer"]


class VenueAffinityScorer:
    """Score events by overlap with venues the user has saved shows from.

    The engine builds the venue-affinity map once per scoring pass via
    :func:`backend.data.repositories.users.list_saved_venue_affinity` so
    individual ``score`` calls are O(1). The map carries the venue's
    display name alongside the count so the reason builder can render
    "You've saved shows at X" without re-querying.

    Attributes:
        name: Scorer identifier used in the ``score_breakdown`` JSONB.
    """

    name: str = SCORER_NAME

    def __init__(self, venue_affinity: dict[uuid.UUID, dict[str, Any]]) -> None:
        """Bind the precomputed venue → save-count map.

        Args:
            venue_affinity: Mapping of ``venue_id`` to a dict with at
                least a ``count`` key (number of saved shows the user
                has at that venue) and a ``name`` key (the venue's
                display name). Pass an empty dict when the user has no
                saved events; the scorer will then abstain on every
                event.
        """
        self._affinity = venue_affinity

    def score(self, event: Event) -> dict[str, Any] | None:
        """Score one event for the bound user.

        Abstains (returns None) on three conditions:

        * the user has no saved-venue history at all,
        * the event is venueless (a non-venue field promo, etc.), or
        * the event's venue isn't one the user has ever saved a show at.

        Args:
            event: The candidate event.

        Returns:
            ``{score, matched_venue_id, matched_venue_name, saved_count}``
            when this scorer has an opinion, ``None`` when it abstains.
        """
        if not self._affinity:
            return None

        venue_id = getattr(event, "venue_id", None)
        if venue_id is None:
            return None

        record = self._affinity.get(venue_id)
        if record is None:
            return None

        count = int(record.get("count", 0))
        if count <= 0:
            return None

        score = min(_BASE_SCORE + _PER_EXTRA_SAVE * (count - 1), _MAX_SCORE)
        return {
            "score": score,
            "matched_venue_id": str(venue_id),
            "matched_venue_name": record.get("name"),
            "saved_count": count,
        }

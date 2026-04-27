"""Followed-artist scorer.

Scores events by overlap with artists the user has explicitly followed
during onboarding (Step 1) or anywhere else the follow control is
exposed. Distinct from :class:`ArtistMatchScorer` in two ways:

* Signal source. Followed artists are an explicit "I want to see this"
  declaration, not derived from listening history. A user can follow
  acts they've never streamed (a friend's band, a touring act they want
  to catch) and that intent should still surface.

* Strength. An explicit follow is a stronger signal than a Spotify
  artist-name overlap but slightly weaker than a Spotify-id match
  (which proves the user has actually been listening). The score sits
  in between accordingly.

The engine builds a precomputed ``signals`` payload (Spotify ids,
normalized names, and display labels for chips) once per scoring pass
via :func:`backend.data.repositories.users.list_followed_artist_signals`,
so individual ``score`` calls are O(1).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.core.text import normalize_artist_name as _normalize

if TYPE_CHECKING:
    from backend.data.models.events import Event

SCORER_NAME = "followed_artist"

# Score weights — higher than venue affinity (0.2-0.4) since an explicit
# artist follow is a stronger taste signal than venue history. Slightly
# lower than the artist-match Spotify-id tier (1.0) so a user who both
# follows AND streams the same artist still ranks as the strongest
# possible match (ArtistMatchScorer fires too and the totals sum).
_ID_MATCH_SCORE = 0.9
_NAME_MATCH_SCORE = 0.8

__all__ = ["SCORER_NAME", "FollowedArtistScorer"]


class FollowedArtistScorer:
    """Score events by overlap with artists the user explicitly follows.

    Instantiated per scoring pass with the precomputed signal payload.

    Attributes:
        name: Scorer identifier used in the ``score_breakdown`` JSONB.
    """

    name: str = SCORER_NAME

    def __init__(self, signals: dict[str, Any]) -> None:
        """Bind the precomputed followed-artist signal payload.

        Args:
            signals: Mapping with three keys:

                * ``spotify_ids`` (``dict[str, str]``) — Spotify artist
                  id → display name for every followed artist whose
                  artist row carries a ``spotify_id``.
                * ``names`` (``dict[str, str]``) — normalized artist
                  name → display name for every followed artist.
                * ``labels`` (``dict[uuid.UUID, str]``) — unused at
                  scoring time; included so callers can use the same
                  payload for the advanced filters panel.

                Pass an empty mapping (or omit the keys) when the user
                follows no artists; the scorer abstains on every event.
        """
        spotify_ids = signals.get("spotify_ids") if signals else None
        names = signals.get("names") if signals else None
        self._spotify_ids: dict[str, str] = spotify_ids or {}
        self._names: dict[str, str] = names or {}

    def score(self, event: Event) -> dict[str, Any] | None:
        """Score one event for the bound user.

        Abstains when the user follows no artists or when neither the
        event's ``spotify_artist_ids`` nor its ``artists`` (display
        name list) intersect the followed set.

        Args:
            event: The candidate event.

        Returns:
            ``{score, matched_artists}`` where ``matched_artists`` is a
            list of ``{name, match}`` dicts (one per followed artist
            matched on this event), or ``None`` when no overlap exists.
        """
        if not self._spotify_ids and not self._names:
            return None

        matched: list[dict[str, str]] = []
        seen_names: set[str] = set()
        best = 0.0

        for artist_id in event.spotify_artist_ids or []:
            name = self._spotify_ids.get(artist_id)
            if name is None:
                continue
            normalized = _normalize(name)
            if normalized in seen_names:
                continue
            seen_names.add(normalized)
            matched.append({"name": name, "match": "spotify_id"})
            best = max(best, _ID_MATCH_SCORE)

        for raw in event.artists or []:
            if not isinstance(raw, str):
                continue
            key = _normalize(raw)
            display = self._names.get(key)
            if display is None or key in seen_names:
                continue
            seen_names.add(key)
            matched.append({"name": display, "match": "artist_name"})
            best = max(best, _NAME_MATCH_SCORE)

        if not matched:
            return None

        return {"score": best, "matched_artists": matched}

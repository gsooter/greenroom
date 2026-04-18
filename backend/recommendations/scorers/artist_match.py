"""Artist-match scorer.

Scores an event by direct overlap between the user's top Spotify artists
and the event's performer list:

* A Spotify artist-id match is a strong signal → score 1.0.
* An artist-name match (normalized) is almost as strong → score 0.85.
  Name matching lets us recommend against scraped events that never got
  Spotify IDs attached, which today is most of them.

The per-event breakdown includes the artist name(s) that matched so the
frontend can render "You listen to X" reason chips without a second
lookup.
"""

from __future__ import annotations

import unicodedata
from typing import Any

from backend.data.models.events import Event
from backend.data.models.users import User


SCORER_NAME = "artist_match"
_ID_MATCH_SCORE = 1.0
_NAME_MATCH_SCORE = 0.85


def _normalize(name: str) -> str:
    """Lowercase + strip diacritics + collapse whitespace.

    Matches "Beyoncé", "beyonce", " Beyonce  " to the same key so a
    Ticketmaster listing that spells artists differently than Spotify
    still matches.

    Args:
        name: Raw artist name string.

    Returns:
        A normalized lookup key.
    """
    stripped = unicodedata.normalize("NFKD", name)
    ascii_only = "".join(c for c in stripped if not unicodedata.combining(c))
    return " ".join(ascii_only.lower().split())


class ArtistMatchScorer:
    """Score events by overlap with a user's top Spotify artists.

    Instantiated per scoring pass — caches normalized name → Spotify
    artist dict so repeated scoring across hundreds of events doesn't
    re-normalize the same 50 top-artist names.

    Attributes:
        name: Scorer identifier used in the ``score_breakdown`` JSONB.
    """

    name: str = SCORER_NAME

    def __init__(self, user: User) -> None:
        """Build lookup tables from the user's cached Spotify data.

        Both the medium-term top-artists list and the recently-played
        list are merged into the same lookup tables. The user asked for
        flat 1.0 / 0.85 weights across the two sources for now — we
        can revisit if we want to nudge recent matches higher later.

        Args:
            user: The user we're generating recommendations for.
        """
        self._id_to_artist: dict[str, dict[str, Any]] = {}
        self._name_to_artist: dict[str, dict[str, Any]] = {}
        sources: list[list[dict[str, Any]] | None] = [
            user.spotify_top_artists,
            user.spotify_recent_artists,
        ]
        for source in sources:
            for artist in source or []:
                if not isinstance(artist, dict):
                    continue
                artist_id = artist.get("id")
                if isinstance(artist_id, str) and artist_id:
                    # First write wins, which means top-artist entries
                    # take precedence over recently-played entries for
                    # reason-chip labels (top artists are richer — they
                    # include genres and image URLs).
                    self._id_to_artist.setdefault(artist_id, artist)
                name = artist.get("name")
                if isinstance(name, str) and name.strip():
                    self._name_to_artist.setdefault(_normalize(name), artist)

    def score(self, event: Event) -> dict[str, Any] | None:
        """Score a single event for the bound user.

        Args:
            event: The :class:`Event` to score.

        Returns:
            A breakdown dict ``{score, matched_artists}`` when this
            scorer has an opinion, or None when there is no overlap at
            all (callers should skip unscored events entirely).
        """
        matched: list[dict[str, Any]] = []
        best_score = 0.0

        for artist_id in event.spotify_artist_ids or []:
            hit = self._id_to_artist.get(artist_id)
            if hit is not None:
                matched.append({"name": hit.get("name"), "match": "spotify_id"})
                best_score = max(best_score, _ID_MATCH_SCORE)

        already_matched_names = {
            _normalize(m["name"]) for m in matched if m.get("name")
        }
        for artist_name in event.artists or []:
            if not isinstance(artist_name, str):
                continue
            key = _normalize(artist_name)
            if key in already_matched_names:
                continue
            hit = self._name_to_artist.get(key)
            if hit is not None:
                matched.append(
                    {"name": hit.get("name"), "match": "artist_name"}
                )
                best_score = max(best_score, _NAME_MATCH_SCORE)

        if not matched:
            return None

        return {
            "score": best_score,
            "matched_artists": matched,
        }

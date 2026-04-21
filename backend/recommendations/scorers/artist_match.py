"""Artist-match scorer.

Scores an event by direct overlap between the user's top artists from
any connected music service (Spotify, Tidal, Apple Music) and the
event's performer list, with a genre-overlap fallback for events whose
performers don't appear in the user's listening history:

* A Spotify artist-id match is a strong signal → score 1.0.
* An artist-name match (normalized) is almost as strong → score 0.85.
  Name matching lets us recommend against scraped events that never got
  Spotify IDs attached, which today is most of them — and it is the
  only way Tidal/Apple Music artists can match, since their provider
  ids do not overlap with Spotify's.
* A genre-only overlap is a soft signal → score 0.5. Used when neither
  an id nor a name match lands but the event's genre tags intersect the
  genres of the user's top Spotify artists. This catches e.g. "you
  listen to a bunch of indie artists and here's an indie show at
  Black Cat by a band you haven't heard of."

The per-event breakdown includes the artist name(s) or genre(s) that
matched so the frontend can render "You listen to X" / "Because you
like <genre>" reason chips without a second lookup.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.core.text import normalize_artist_name as _normalize

if TYPE_CHECKING:
    from backend.data.models.events import Event
    from backend.data.models.users import User

SCORER_NAME = "artist_match"
_ID_MATCH_SCORE = 1.0
_NAME_MATCH_SCORE = 0.85
_GENRE_MATCH_SCORE = 0.5

__all__ = ["SCORER_NAME", "ArtistMatchScorer", "_normalize"]


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
        """Build lookup tables from the user's cached music-service data.

        All connected services contribute to the same lookup tables:
        Spotify top + recent, Tidal top, Apple Music top. Spotify ids
        and names land first so they take precedence when a match could
        come from multiple services (Spotify data is richest for the
        reason-chip UI).

        Args:
            user: The user we're generating recommendations for.
        """
        self._id_to_artist: dict[str, dict[str, Any]] = {}
        self._name_to_artist: dict[str, dict[str, Any]] = {}
        self._user_genres: set[str] = set()
        sources: list[list[dict[str, Any]] | None] = [
            user.spotify_top_artists,
            user.spotify_recent_artists,
            user.tidal_top_artists,
            user.apple_top_artists,
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
                for genre in artist.get("genres") or []:
                    if isinstance(genre, str) and genre.strip():
                        self._user_genres.add(genre.strip().lower())

    def score(self, event: Event) -> dict[str, Any] | None:
        """Score a single event for the bound user.

        Args:
            event: The :class:`Event` to score.

        Returns:
            A breakdown dict when this scorer has an opinion, or None
            when there is no overlap at all (callers should skip
            unscored events entirely). The shape is
            ``{score, matched_artists}`` for id/name matches, and
            ``{score, matched_artists: [], matched_genres}`` for the
            genre-only fallback.
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
                matched.append({"name": hit.get("name"), "match": "artist_name"})
                best_score = max(best_score, _NAME_MATCH_SCORE)

        if matched:
            return {
                "score": best_score,
                "matched_artists": matched,
            }

        matched_genres = [
            genre
            for genre in event.genres or []
            if isinstance(genre, str)
            and genre.strip()
            and genre.strip().lower() in self._user_genres
        ]
        if matched_genres:
            return {
                "score": _GENRE_MATCH_SCORE,
                "matched_artists": [],
                "matched_genres": matched_genres,
            }

        return None

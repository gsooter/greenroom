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
* A genre-only overlap is a soft signal → score 0.5. This tier fires
  when neither an id nor a name match lands but the event's genre tags
  intersect either:

  - the genres of the user's top Spotify artists (derived from listening
    history), or
  - the substring aliases for the user's onboarding genre picks from
    :data:`backend.core.genres.GENRE_SPOTIFY_ALIASES` (explicit taste
    signal, available even before the user connects a music service).

  Either path catches "you said you like indie rock / your top artists
  are indie acts, and here's an indie show at Black Cat by a band you
  haven't heard of."

The per-event breakdown includes the artist name(s), genre(s), and
preference slug(s) that matched so the frontend can render
"You listen to X" / "Because you like Indie Rock" chips without a
second lookup.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.core.genres import GENRE_LABELS, GENRE_SPOTIFY_ALIASES
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

        Also captures the user's onboarding genre picks as a list of
        ``(slug, label, aliases)`` tuples so the fallback tier can
        surface matches for users who haven't connected a music service
        yet — the taste step is the only strong signal we have about
        them on day one, and dropping it on the floor leaves the
        For-You page empty.

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

        self._preference_aliases: list[tuple[str, str, tuple[str, ...]]] = []
        for slug in user.genre_preferences or []:
            if not isinstance(slug, str):
                continue
            aliases = GENRE_SPOTIFY_ALIASES.get(slug)
            label = GENRE_LABELS.get(slug)
            if not aliases or not label:
                continue
            self._preference_aliases.append((slug, label, aliases))

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

        cleaned_event_genres = [
            genre.strip()
            for genre in event.genres or []
            if isinstance(genre, str) and genre.strip()
        ]
        matched_genres = [
            genre
            for genre in cleaned_event_genres
            if genre.lower() in self._user_genres
        ]
        matched_preferences = self._match_preference_aliases(cleaned_event_genres)

        if matched_genres or matched_preferences:
            payload: dict[str, Any] = {
                "score": _GENRE_MATCH_SCORE,
                "matched_artists": [],
            }
            if matched_genres:
                payload["matched_genres"] = matched_genres
            if matched_preferences:
                payload["matched_preferences"] = matched_preferences
            return payload

        return None

    def _match_preference_aliases(
        self, cleaned_event_genres: list[str]
    ) -> list[dict[str, str]]:
        """Match the event's genre tags against the user's onboarding picks.

        For each preference slug the user selected during onboarding, we
        check whether any of its substring aliases appears inside any
        of the event's genre tags (case-insensitive). At most one match
        per slug is emitted so a user who picked both "alternative" and
        "indie-rock" and an event tagged "indie rock" doesn't generate
        two redundant chips for the same underlying signal.

        Args:
            cleaned_event_genres: Already-whitespace-stripped genre
                strings from :attr:`Event.genres`.

        Returns:
            List of ``{slug, label, event_genre}`` dicts — one per
            matched preference slug. Empty when the user selected no
            genres or none of their slugs touch this event's tags.
        """
        if not self._preference_aliases or not cleaned_event_genres:
            return []
        lowered = [(genre, genre.lower()) for genre in cleaned_event_genres]
        hits: list[dict[str, str]] = []
        for slug, label, aliases in self._preference_aliases:
            for original, lower in lowered:
                if any(alias in lower for alias in aliases):
                    hits.append({"slug": slug, "label": label, "event_genre": original})
                    break
        return hits

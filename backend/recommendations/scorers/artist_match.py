"""Artist-match scorer.

Scores an event by direct overlap between the user's top artists from
any connected music service (Spotify, Tidal, Apple Music) and the
event's performer list, with a canonical-genre overlap fallback for
events whose performers don't appear in the user's listening history:

* A Spotify artist-id match is a strong signal → score 1.0.
* An artist-name match (normalized) is almost as strong → score 0.85.
  Name matching lets us recommend against scraped events that never got
  Spotify IDs attached, which today is most of them — and it is the
  only way Tidal/Apple Music artists can match, since their provider
  ids do not overlap with Spotify's.
* A canonical-genre overlap is a soft signal → score 0.5. This tier
  fires when neither an id nor a name match lands but the canonical
  genres of the event's performers intersect either:

  - the canonicalized genres of the user's top music-service artists
    (derived from listening history), or
  - the canonical labels of the user's onboarding genre picks from
    :data:`backend.core.genres.GENRE_LABELS` (explicit taste signal,
    available even before the user connects a music service).

  Either path catches "you said you like indie rock / your top artists
  are indie acts, and here's an indie show at Black Cat by a band you
  haven't heard of."

Canonical genres come from the artists table's ``canonical_genres``
column (Sprint 1C), populated nightly by
:mod:`backend.services.genre_normalization_tasks`. The engine
pre-fetches a name → canonical genres map before scoring so the scorer
itself does no I/O.

The per-event breakdown includes the artist name(s), genre(s), and
preference slug(s) that matched so the frontend can render
"You listen to X" / "Because you like Indie Rock" chips without a
second lookup.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.core.genres import GENRE_LABELS
from backend.core.text import normalize_artist_name as _normalize
from backend.services.genre_normalization import map_tags_to_canonical

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

    def __init__(
        self,
        user: User,
        *,
        artist_canonical_genres: dict[str, list[str]] | None = None,
    ) -> None:
        """Build lookup tables from the user's cached music-service data.

        All connected services contribute to the same lookup tables:
        Spotify top + recent, Tidal top, Apple Music top. Spotify ids
        and names land first so they take precedence when a match could
        come from multiple services (Spotify data is richest for the
        reason-chip UI).

        The user's raw service-provided genres are funneled through
        :func:`backend.services.genre_normalization.map_tags_to_canonical`
        so they share a label space with the artists table's
        ``canonical_genres``. Onboarding picks are mapped to their
        :data:`GENRE_LABELS` form (e.g. ``indie-rock`` → ``Indie
        Rock``), again to land in canonical space — that lets the
        fallback tier surface matches for users who haven't connected a
        music service yet without any substring/alias matching.

        Args:
            user: The user we're generating recommendations for.
            artist_canonical_genres: Pre-fetched lookup map keyed by
                normalized artist name. The engine builds this once
                per scoring pass from the artists table so individual
                ``score()`` calls stay session-free.
        """
        self._id_to_artist: dict[str, dict[str, Any]] = {}
        self._name_to_artist: dict[str, dict[str, Any]] = {}
        raw_user_genres: list[str] = []
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
                        raw_user_genres.append(genre)

        self._user_canonical_genres: set[str] = set(
            map_tags_to_canonical(raw_user_genres).keys()
        )

        self._preference_labels: list[tuple[str, str]] = []
        for slug in user.genre_preferences or []:
            if not isinstance(slug, str):
                continue
            label = GENRE_LABELS.get(slug)
            if label:
                self._preference_labels.append((slug, label))

        self._artist_canonical_genres: dict[str, list[str]] = (
            artist_canonical_genres or {}
        )

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

        event_canonical = self._collect_event_canonical_genres(event)
        if not event_canonical:
            return None

        matched_genres = sorted(event_canonical & self._user_canonical_genres)
        matched_preferences = [
            {"slug": slug, "label": label, "event_genre": label}
            for slug, label in self._preference_labels
            if label in event_canonical
        ]

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

    def _collect_event_canonical_genres(self, event: Event) -> set[str]:
        """Union the canonical genres of every artist named on the event.

        Artist names are looked up in the pre-fetched
        :attr:`_artist_canonical_genres` map keyed by normalized name —
        same casing/diacritic primitive used elsewhere in the recommender
        so "Phoebe Bridgers" / "phoebe bridgers" / "PHOEBE BRIDGERS"
        collapse to a single hit.

        Args:
            event: The candidate :class:`Event` row.

        Returns:
            Set of canonical genre labels covering every performer on
            this event. Empty when no artist on the event has any
            canonical genres recorded.
        """
        if not self._artist_canonical_genres:
            return set()
        out: set[str] = set()
        for artist_name in event.artists or []:
            if not isinstance(artist_name, str):
                continue
            key = _normalize(artist_name)
            for canonical in self._artist_canonical_genres.get(key, ()):
                out.add(canonical)
        return out

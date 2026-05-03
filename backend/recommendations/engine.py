"""Recommendation engine orchestrator.

Runs all registered scorers against a user's candidate event set, sums
and normalizes the per-scorer contributions to 0.0-1.0, and persists
the top-N results with a ``score_breakdown`` JSONB so users can see
why a show was recommended and we can analyze scorer impact later
(Decision 007).

Current scorers:

* :class:`backend.recommendations.scorers.artist_match.ArtistMatchScorer`
* :class:`backend.recommendations.scorers.followed_artist.FollowedArtistScorer`
* :class:`backend.recommendations.scorers.similar_artist.SimilarArtistScorer`
  — slots between exact artist matches and the genre-overlap fallback;
  derives "you might like X because they're similar to Y" from the
  Last.fm ``artist_similarity`` table.
* :class:`backend.recommendations.scorers.followed_venue.FollowedVenueScorer`
* :class:`backend.recommendations.scorers.venue_affinity.VenueAffinityScorer`

Adding a new scorer is a matter of implementing the protocol below and
adding it to ``_build_scorers`` — no other file changes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy import select

from backend.core.text import normalize_artist_name as _normalize
from backend.data.models.events import Event, EventStatus
from backend.data.repositories import artists as artists_repo
from backend.data.repositories import follows as follows_repo
from backend.data.repositories import users as users_repo
from backend.recommendations.scorers.artist_match import ArtistMatchScorer
from backend.recommendations.scorers.followed_artist import FollowedArtistScorer
from backend.recommendations.scorers.followed_venue import FollowedVenueScorer
from backend.recommendations.scorers.similar_artist import (
    DIRECT_FOLLOW_WEIGHT,
    MINIMUM_SIMILARITY_SCORE,
    MINIMUM_TAG_JACCARD,
    RECENT_LISTEN_WEIGHT,
    TOP_ARTIST_WEIGHT,
    SimilarArtistScorer,
)
from backend.recommendations.scorers.venue_affinity import VenueAffinityScorer
from backend.services.artist_similarity import find_artists_by_tag_similarity

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from backend.data.models.users import User

# Cap how many upcoming events we score per user. The full candidate
# set is small today (hundreds) but this guards the worst case so a
# runaway scrape can't make the engine O(users * events).
_MAX_EVENTS_TO_SCORE = 1000

# Target size of the persisted recommendation list. 60 is enough to
# populate the For-You page with pagination headroom without writing
# rows nobody will ever look at.
_DEFAULT_RECS_PER_USER = 60


class Scorer(Protocol):
    """Protocol every scorer must satisfy.

    Attributes:
        name: Short identifier (e.g. ``"artist_match"``) used as a key
            in the stored ``score_breakdown`` JSONB.
    """

    name: str

    def score(self, event: Event) -> dict[str, Any] | None:
        """Score a single event.

        Args:
            event: The candidate event.

        Returns:
            A dict with at minimum ``{"score": float}`` when this
            scorer has an opinion, or ``None`` to abstain.
        """
        ...


def generate_for_user(
    session: Session,
    user: User,
    *,
    limit: int = _DEFAULT_RECS_PER_USER,
) -> int:
    """Regenerate the persisted recommendation list for ``user``.

    Clears any existing rows for the user, scores the upcoming event
    catalog, and writes the top ``limit`` rows. The caller is
    responsible for ``session.commit()`` so this composes cleanly with
    both a Flask request and a Celery task.

    The function short-circuits and writes zero rows only when the user
    has neither cached music-service artists nor any onboarding genre
    preferences to match on — without either signal the scorers have
    nothing to compare against and every event would be a cold miss.

    Args:
        session: Active SQLAlchemy session.
        user: The user to generate recommendations for.
        limit: Maximum number of recommendation rows to persist.

    Returns:
        The number of recommendation rows written.
    """
    users_repo.delete_recommendations_for_user(session, user.id)

    venue_affinity = users_repo.list_saved_venue_affinity(session, user.id)
    followed_artists = follows_repo.list_followed_artist_signals(session, user.id)
    followed_venues = follows_repo.list_followed_venue_labels(session, user.id)

    if not (
        user.spotify_top_artists
        or user.spotify_recent_artists
        or user.tidal_top_artists
        or user.apple_top_artists
        or user.genre_preferences
        or venue_affinity
        or followed_artists.get("spotify_ids")
        or followed_artists.get("names")
        or followed_venues
    ):
        return 0

    events = _fetch_scoreable_events(session)
    artist_canonical_genres = _fetch_artist_canonical_genres(session, events)
    (
        anchor_signals,
        similar_by_anchor,
        anchor_artist_ids,
    ) = _fetch_similar_artist_signals(
        session,
        user=user,
        followed_artists=followed_artists,
    )
    tag_similar_by_anchor = _fetch_tag_similar_signals(
        session,
        anchor_signals=anchor_signals,
        anchor_artist_ids=anchor_artist_ids,
        candidate_events=events,
    )
    scorers = _build_scorers(
        user,
        venue_affinity,
        followed_artists=followed_artists,
        followed_venues=followed_venues,
        artist_canonical_genres=artist_canonical_genres,
        anchor_signals=anchor_signals,
        similar_by_anchor=similar_by_anchor,
        tag_similar_by_anchor=tag_similar_by_anchor,
    )

    scored: list[tuple[float, dict[str, Any], Event]] = []
    for event in events:
        breakdown: dict[str, Any] = {}
        total = 0.0
        for scorer in scorers:
            result = scorer.score(event)
            if result is None:
                continue
            breakdown[scorer.name] = result
            total += float(result.get("score", 0.0))
        if not breakdown:
            continue
        normalized = min(total, 1.0)
        breakdown["_match_reasons"] = _build_match_reasons(breakdown)
        scored.append((normalized, breakdown, event))

    scored.sort(key=lambda row: (-row[0], row[2].starts_at))
    deduped = _dedupe_by_show(scored)
    top = deduped[:limit]

    for score, breakdown, event in top:
        users_repo.create_recommendation(
            session,
            user_id=user.id,
            event_id=event.id,
            score=score,
            score_breakdown=breakdown,
        )
    return len(top)


def _fetch_scoreable_events(
    session: Session,
    *,
    limit: int = _MAX_EVENTS_TO_SCORE,
) -> list[Event]:
    """Return upcoming, non-cancelled events ordered by start time.

    Scored from the database rather than the repository list helper so
    the engine can apply its own limit without pagination ceremony.

    Args:
        session: Active SQLAlchemy session.
        limit: Maximum number of rows to fetch.

    Returns:
        List of candidate :class:`Event` rows.
    """
    now = datetime.now(UTC)
    stmt = (
        select(Event)
        .where(Event.starts_at >= now)
        .where(Event.status != EventStatus.CANCELLED)
        .order_by(Event.starts_at.asc())
        .limit(limit)
    )
    return list(session.execute(stmt).scalars().all())


def _build_scorers(
    user: User,
    venue_affinity: dict[Any, dict[str, Any]],
    *,
    followed_artists: dict[str, Any] | None = None,
    followed_venues: dict[Any, str] | None = None,
    artist_canonical_genres: dict[str, list[str]] | None = None,
    anchor_signals: dict[str, tuple[str, float]] | None = None,
    similar_by_anchor: dict[str, list[dict[str, Any]]] | None = None,
    tag_similar_by_anchor: dict[str, list[dict[str, Any]]] | None = None,
) -> list[Scorer]:
    """Instantiate the active scorer set for a user.

    Order doesn't affect totals (scores sum) but does decide tie-breaks
    in the breakdown dict's iteration. Strongest signals lead — artist
    matches first, then explicit follows (artist then venue), then
    saved-venue affinity. The UI truncates reason chips, so this order
    keeps the most recognizable reason visible.

    Args:
        user: The user we are scoring events for.
        venue_affinity: Precomputed map of saved-venue counts. Pass an
            empty dict when the user has no saved events; the venue
            scorer will then abstain on every event.
        followed_artists: Precomputed signal payload from
            :func:`follows_repo.list_followed_artist_signals`. Pass
            ``None`` or an empty mapping for users who follow no
            artists.
        followed_venues: Precomputed ``venue_id`` → display name map for
            venues the user follows. Pass ``None`` or an empty mapping
            for users who follow no venues.
        artist_canonical_genres: Pre-fetched normalized name → canonical
            genres map covering every artist named on the candidate
            event set. Drives the genre fallback inside
            :class:`ArtistMatchScorer` without any per-event lookup.

    Returns:
        List of scorer instances ready to call :meth:`Scorer.score`.
    """
    return [
        ArtistMatchScorer(user, artist_canonical_genres=artist_canonical_genres or {}),
        FollowedArtistScorer(followed_artists or {}),
        SimilarArtistScorer(
            anchor_signals=anchor_signals or {},
            similar_by_anchor=similar_by_anchor or {},
            tag_similar_by_anchor=tag_similar_by_anchor or {},
        ),
        FollowedVenueScorer(followed_venues or {}),
        VenueAffinityScorer(venue_affinity),
    ]


def _fetch_artist_canonical_genres(
    session: Session, events: list[Event]
) -> dict[str, list[str]]:
    """Build a normalized name → canonical genres map for the candidate set.

    Walks every artist name referenced by the candidate events,
    normalizes each one, and asks the artists repo for the canonical
    genres recorded against any matching row. Artists with no canonical
    genres recorded are omitted — they can't contribute to the genre
    fallback.

    Args:
        session: Active SQLAlchemy session.
        events: The scoreable event set.

    Returns:
        Lookup map from normalized artist name to canonical genres
        list, ready to inject into :class:`ArtistMatchScorer`.
    """
    names: set[str] = set()
    for event in events:
        for artist_name in event.artists or []:
            if isinstance(artist_name, str) and artist_name.strip():
                names.add(_normalize(artist_name))
    if not names:
        return {}
    return artists_repo.get_canonical_genres_by_normalized_name(session, sorted(names))


def _fetch_similar_artist_signals(
    session: Session,
    *,
    user: User,
    followed_artists: dict[str, Any],
) -> tuple[
    dict[str, tuple[str, float]],
    dict[str, list[dict[str, Any]]],
    dict[str, Any],
]:
    """Build the anchor signal map and similarity lookup for the scorer.

    Anchors come from three sources in priority order:

    1. Artists the user explicitly follows
       (:data:`DIRECT_FOLLOW_WEIGHT`).
    2. Top artists from any connected music service
       (:data:`TOP_ARTIST_WEIGHT`).
    3. Recently-played artists from Spotify
       (:data:`RECENT_LISTEN_WEIGHT`).

    For each anchor, we resolve a UUID against the ``artists`` table by
    normalized name and fetch any ``artist_similarity`` rows in one
    batched query. Anchors without a matching artist row contribute no
    similarity data — the similarity table is keyed on real artists.

    Args:
        session: Active SQLAlchemy session.
        user: The user we're generating recommendations for.
        followed_artists: Output of
            :func:`follows_repo.list_followed_artist_signals`.

    Returns:
        A tuple of ``(anchor_signals, similar_by_anchor,
        anchor_artist_ids)``:

            * ``anchor_signals``: ``{normalized_anchor_name: (display,
              weight)}`` covering every anchor with a matching artist
              row.
            * ``similar_by_anchor``: ``{normalized_anchor_name:
              [{similar_artist_name, similarity_score}, ...]}``.
            * ``anchor_artist_ids``: ``{normalized_anchor_name:
              artist_uuid}`` for downstream consumers (today, the
              tag-overlap query path).
    """
    # Build candidate (normalized name → (display, weight)) honoring the
    # priority order: a name that appears as both followed and top
    # keeps the higher (followed) weight.
    candidates: dict[str, tuple[str, float]] = {}

    follow_names = followed_artists.get("names") if followed_artists else None
    if isinstance(follow_names, dict):
        for normalized, display in follow_names.items():
            if isinstance(normalized, str) and isinstance(display, str):
                candidates[normalized] = (display, DIRECT_FOLLOW_WEIGHT)

    top_sources: list[list[dict[str, Any]] | None] = [
        user.spotify_top_artists,
        user.tidal_top_artists,
        user.apple_top_artists,
    ]
    for source in top_sources:
        for entry in source or []:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            normalized = _normalize(name)
            if not normalized or normalized in candidates:
                continue
            candidates[normalized] = (name, TOP_ARTIST_WEIGHT)

    for entry in user.spotify_recent_artists or []:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        normalized = _normalize(name)
        if not normalized or normalized in candidates:
            continue
        candidates[normalized] = (name, RECENT_LISTEN_WEIGHT)

    if not candidates:
        return {}, {}, {}

    name_to_artist_id = artists_repo.list_artist_ids_by_normalized_names(
        session, list(candidates.keys())
    )
    if not name_to_artist_id:
        return {}, {}, {}

    artist_id_to_anchor_key: dict[Any, str] = {}
    anchor_signals: dict[str, tuple[str, float]] = {}
    anchor_artist_ids: dict[str, Any] = {}
    for normalized, (display, weight) in candidates.items():
        artist_id = name_to_artist_id.get(normalized)
        if artist_id is None:
            continue
        artist_id_to_anchor_key[artist_id] = normalized
        anchor_signals[normalized] = (display, weight)
        anchor_artist_ids[normalized] = artist_id

    if not anchor_signals:
        return {}, {}, {}

    edges = artists_repo.list_similar_artists_for_anchors(
        session,
        list(artist_id_to_anchor_key.keys()),
        minimum_score=MINIMUM_SIMILARITY_SCORE,
    )
    similar_by_anchor: dict[str, list[dict[str, Any]]] = {}
    for source_id, similar_name, score in edges:
        anchor_key = artist_id_to_anchor_key.get(source_id)
        if anchor_key is None:
            continue
        similar_by_anchor.setdefault(anchor_key, []).append(
            {"similar_artist_name": similar_name, "similarity_score": score}
        )
    return anchor_signals, similar_by_anchor, anchor_artist_ids


def _fetch_tag_similar_signals(
    session: Session,
    *,
    anchor_signals: dict[str, tuple[str, float]],
    anchor_artist_ids: dict[str, Any],
    candidate_events: list[Event],
) -> dict[str, list[dict[str, Any]]]:
    """Build the tag-overlap similarity map for the SimilarArtistScorer.

    For each anchor with a matching artist row, queries
    :func:`backend.services.artist_similarity.find_artists_by_tag_similarity`
    and keeps only entries whose artist name is named on at least one
    candidate event — irrelevant overlap rows are filtered out so the
    flattened scoring index stays small.

    Tag overlap is computed at query time off the GIN-indexed
    ``granular_tags`` column, so the per-anchor cost is one query
    against a pre-filtered candidate set. Anchors with no granular
    tags or no candidates contribute nothing.

    Args:
        session: Active SQLAlchemy session.
        anchor_signals: ``{normalized_anchor_name: (display, weight)}``
            from :func:`_fetch_similar_artist_signals`.
        anchor_artist_ids: ``{normalized_anchor_name: artist_uuid}``
            companion map keyed by the same anchor names.
        candidate_events: The scoreable event set; used to build the
            set of performer names worth fetching tag-similar matches
            for.

    Returns:
        Mapping from normalized anchor name to a list of payloads in
        the same shape as ``similar_by_anchor`` — each entry has
        ``similar_artist_name`` and ``similarity_score`` (Jaccard
        ratio in 0.0-1.0). Empty dict when no anchor has tag-overlap
        matches against the event set.
    """
    if not anchor_signals or not anchor_artist_ids or not candidate_events:
        return {}

    candidate_names: set[str] = set()
    for event in candidate_events:
        for performer in event.artists or []:
            if isinstance(performer, str) and performer.strip():
                candidate_names.add(_normalize(performer))
    if not candidate_names:
        return {}

    # Drop anchors that are themselves in candidate_names to mirror the
    # scorer's anchor-skip behavior — we never want to score an event
    # artist against the user's own anchor as "tag-similar."
    out: dict[str, list[dict[str, Any]]] = {}
    for anchor_key, anchor_id in anchor_artist_ids.items():
        results = find_artists_by_tag_similarity(
            session,
            anchor_id,
            min_overlap=3,
        )
        if not results:
            continue
        payloads: list[dict[str, Any]] = []
        for result in results:
            normalized = _normalize(result.artist_name)
            if not normalized or normalized not in candidate_names:
                continue
            if result.jaccard_score < MINIMUM_TAG_JACCARD:
                continue
            payloads.append(
                {
                    "similar_artist_name": result.artist_name,
                    "similarity_score": result.jaccard_score,
                }
            )
        if payloads:
            out[anchor_key] = payloads
    return out


def _dedupe_by_show(
    scored: list[tuple[float, dict[str, Any], Event]],
) -> list[tuple[float, dict[str, Any], Event]]:
    """Collapse duplicate Event rows that represent the same real-world show.

    Ticketmaster occasionally surfaces the same show under two external
    IDs (e.g. a presale listing plus the general-sale listing), and
    each becomes its own ``events`` row via the scraper. Without
    dedupe, both end up as separate recommendation cards on the
    For-You page. We collapse on ``(venue_id, normalized title,
    starts_at)`` and keep the first occurrence, which — because the
    input is already sorted by (-score, starts_at) — is the highest-
    scoring copy.

    Args:
        scored: Sorted scored rows from :func:`generate_for_user`.

    Returns:
        The same rows with duplicates removed, order preserved.
    """
    seen: set[tuple[Any, str, Any]] = set()
    unique: list[tuple[float, dict[str, Any], Event]] = []
    for row in scored:
        event = row[2]
        key = (
            event.venue_id,
            (event.title or "").strip().lower(),
            event.starts_at,
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def _build_match_reasons(breakdown: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten per-scorer output into a single UI-facing reason list.

    The frontend renders one row of chips per card ("You listen to X",
    "Because you like Indie Rock") regardless of which scorer produced
    them, so the engine collapses the nested breakdown into a flat list
    up front. Reasons are ordered strongest-first — artist matches,
    then onboarding genre picks, then top-artist genre overlap — so the
    UI can truncate to the first N chips without dropping the best
    signal.

    Args:
        breakdown: The in-progress breakdown dict for one event.

    Returns:
        List of ``{scorer, ...}`` dicts describing why this event was
        surfaced.
    """
    reasons: list[dict[str, Any]] = []
    artist_match = breakdown.get("artist_match")
    followed_artist = breakdown.get("followed_artist")
    followed_venue = breakdown.get("followed_venue")
    venue_affinity = breakdown.get("venue_affinity")

    if not isinstance(artist_match, dict):
        artist_match = {}

    seen_artist_names: set[str] = set()
    for matched in artist_match.get("matched_artists", []) or []:
        name = matched.get("name")
        if not name:
            continue
        seen_artist_names.add(name.lower())
        reasons.append(
            {
                "scorer": "artist_match",
                "kind": matched.get("match", "artist_name"),
                "label": f"You listen to {name}",
                "artist_name": name,
            }
        )

    if isinstance(followed_artist, dict):
        for matched in followed_artist.get("matched_artists", []) or []:
            name = matched.get("name") if isinstance(matched, dict) else None
            if not name or name.lower() in seen_artist_names:
                continue
            seen_artist_names.add(name.lower())
            reasons.append(
                {
                    "scorer": "followed_artist",
                    "kind": "followed_artist",
                    "label": f"You follow {name}",
                    "artist_name": name,
                }
            )

    similar_artist = breakdown.get("similar_artist")
    if isinstance(similar_artist, dict):
        for matched in similar_artist.get("matched_similar_artists", []) or []:
            if not isinstance(matched, dict):
                continue
            name = matched.get("name")
            anchor_name = matched.get("anchor_name")
            if not name or name.lower() in seen_artist_names:
                continue
            if not anchor_name:
                continue
            seen_artist_names.add(name.lower())
            match_kind = matched.get("match_kind", "lastfm")
            if match_kind == "tag_overlap":
                kind = "tag_overlap"
                label = f"Shares tags with {anchor_name}"
            else:
                kind = "similar_artist"
                label = f"Similar to {anchor_name}"
            reasons.append(
                {
                    "scorer": "similar_artist",
                    "kind": kind,
                    "label": label,
                    "artist_name": name,
                    "anchor_name": anchor_name,
                }
            )

    seen_preference_slugs: set[str] = set()
    for preference in artist_match.get("matched_preferences", []) or []:
        if not isinstance(preference, dict):
            continue
        slug = preference.get("slug")
        label = preference.get("label")
        if not slug or not label or slug in seen_preference_slugs:
            continue
        seen_preference_slugs.add(slug)
        reasons.append(
            {
                "scorer": "artist_match",
                "kind": "genre_preference",
                "label": f"Because you like {label}",
                "genre_slug": slug,
            }
        )

    for genre in artist_match.get("matched_genres", []) or []:
        if not isinstance(genre, str) or not genre.strip():
            continue
        reasons.append(
            {
                "scorer": "artist_match",
                "kind": "genre_overlap",
                "label": f"Matches genre: {genre}",
                "genre": genre,
            }
        )

    seen_venue_names: set[str] = set()
    if isinstance(followed_venue, dict):
        venue_name = followed_venue.get("matched_venue_name")
        if isinstance(venue_name, str) and venue_name.strip():
            seen_venue_names.add(venue_name.lower())
            reasons.append(
                {
                    "scorer": "followed_venue",
                    "kind": "followed_venue",
                    "label": f"You follow {venue_name}",
                    "venue_name": venue_name,
                }
            )

    if isinstance(venue_affinity, dict):
        venue_name = venue_affinity.get("matched_venue_name")
        if (
            isinstance(venue_name, str)
            and venue_name.strip()
            and venue_name.lower() not in seen_venue_names
        ):
            reasons.append(
                {
                    "scorer": "venue_affinity",
                    "kind": "saved_venue",
                    "label": f"You've saved shows at {venue_name}",
                    "venue_name": venue_name,
                }
            )

    return reasons

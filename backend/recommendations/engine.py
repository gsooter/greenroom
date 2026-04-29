"""Recommendation engine orchestrator.

Runs all registered scorers against a user's candidate event set, sums
and normalizes the per-scorer contributions to 0.0-1.0, and persists
the top-N results with a ``score_breakdown`` JSONB so users can see
why a show was recommended and we can analyze scorer impact later
(Decision 007).

Current scorers:

* :class:`backend.recommendations.scorers.artist_match.ArtistMatchScorer`

Adding a new scorer is a matter of implementing the protocol below and
adding it to ``_build_scorers`` — no other file changes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy import select

from backend.data.models.events import Event, EventStatus
from backend.data.repositories import follows as follows_repo
from backend.data.repositories import users as users_repo
from backend.recommendations.scorers.artist_match import ArtistMatchScorer
from backend.recommendations.scorers.followed_artist import FollowedArtistScorer
from backend.recommendations.scorers.followed_venue import FollowedVenueScorer
from backend.recommendations.scorers.venue_affinity import VenueAffinityScorer

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
    scorers = _build_scorers(
        user,
        venue_affinity,
        followed_artists=followed_artists,
        followed_venues=followed_venues,
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

    Returns:
        List of scorer instances ready to call :meth:`Scorer.score`.
    """
    return [
        ArtistMatchScorer(user),
        FollowedArtistScorer(followed_artists or {}),
        FollowedVenueScorer(followed_venues or {}),
        VenueAffinityScorer(venue_affinity),
    ]


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

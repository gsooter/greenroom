"""Manual smoke test for the DMV-aware ranking overlay.

Runs the recommendation engine for one real user plus three
synthetic personas, prints the top recommendations with full score
breakdowns, and contrasts overlay-applied vs base-only ranking so a
reviewer can see at a glance which shows moved up and which moved
down because of the overlay.

Usage:
    python -m backend.scripts.smoke_overlay

Designed to be idempotent: synthetic users are upserted by email
prefix so re-running doesn't pile up rows. Recommendations are
written into the live recommendations table; if you don't want
that, export ``GREENROOM_SMOKE_DRY_RUN=1`` before running.
"""

from __future__ import annotations

import os
import uuid
from typing import TYPE_CHECKING, Any, NamedTuple

from sqlalchemy import select

from backend.core.database import get_session_factory
from backend.data.models.cities import City
from backend.data.models.users import User
from backend.recommendations import engine as engine_module

if TYPE_CHECKING:
    from backend.data.models.events import Event
from backend.recommendations.engine import (
    _build_match_reasons,
    _build_scorers,
    _fetch_artist_canonical_genres,
    _fetch_scoreable_events,
    _fetch_similar_artist_signals,
    _fetch_tag_similar_signals,
    _resolve_user_region_id,
)
from backend.recommendations.overlays.actionability import (
    compute_actionability_multiplier,
)
from backend.recommendations.overlays.availability import (
    compute_availability_multiplier,
)
from backend.recommendations.overlays.time_window import (
    compute_time_window_multiplier,
)


class ScoredEvent(NamedTuple):
    """One scored event with both base and final scores for comparison."""

    event: Event
    base_score: float
    actionability: float
    time_window: float
    availability: float
    final_score: float
    breakdown: dict[str, Any]


def _score_user_events(
    session: Any, user: User, events: list[Event]
) -> list[ScoredEvent]:
    """Score every event for ``user`` and capture both base and overlay results.

    Mirrors the engine's scoring loop but exposes the per-event
    components separately so the smoke script can show the contrast
    between base ranking and overlay-applied ranking.

    Args:
        session: Active SQLAlchemy session.
        user: User row to score events for.
        events: Pre-fetched candidate event list.

    Returns:
        List of :class:`ScoredEvent` for every event with at least
        one scorer match.
    """
    from datetime import UTC, datetime

    venue_affinity = engine_module.users_repo.list_saved_venue_affinity(
        session, user.id
    )
    followed_artists = engine_module.follows_repo.list_followed_artist_signals(
        session, user.id
    )
    followed_venues = engine_module.follows_repo.list_followed_venue_labels(
        session, user.id
    )
    canonical_genres = _fetch_artist_canonical_genres(session, events)
    anchor_signals, similar_by_anchor, anchor_artist_ids = (
        _fetch_similar_artist_signals(
            session, user=user, followed_artists=followed_artists
        )
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
        artist_canonical_genres=canonical_genres,
        anchor_signals=anchor_signals,
        similar_by_anchor=similar_by_anchor,
        tag_similar_by_anchor=tag_similar_by_anchor,
    )
    user_region_id = _resolve_user_region_id(session, user.city_id)
    now = datetime.now(UTC)

    out: list[ScoredEvent] = []
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
        base = min(total, 1.0)
        actionability = compute_actionability_multiplier(
            event, user.city_id, user_region_id
        )
        time_window = compute_time_window_multiplier(event, now)
        availability = compute_availability_multiplier(event)
        final = base * actionability * time_window * availability
        breakdown["base"] = base
        breakdown["actionability"] = actionability
        breakdown["time_window"] = time_window
        breakdown["availability"] = availability
        breakdown["_match_reasons"] = _build_match_reasons(breakdown)
        out.append(
            ScoredEvent(
                event=event,
                base_score=base,
                actionability=actionability,
                time_window=time_window,
                availability=availability,
                final_score=final,
                breakdown=breakdown,
            )
        )
    return out


def _print_top(label: str, scored: list[ScoredEvent], top_n: int = 10) -> None:
    """Print the top ``top_n`` recommendations with full breakdowns.

    Args:
        label: Heading shown above the listing.
        scored: Output of :func:`_score_user_events`, already filtered.
        top_n: Number of rows to print.
    """
    ranked = sorted(scored, key=lambda s: -s.final_score)[:top_n]
    print(f"\n=== {label} (top {len(ranked)}) ===")
    for idx, item in enumerate(ranked, 1):
        venue = item.event.venue
        city_name = venue.city.name if venue and venue.city else "?"
        venue_name = venue.name if venue else "?"
        date = item.event.starts_at.strftime("%Y-%m-%d")
        reasons = item.breakdown.get("_match_reasons") or []
        reason_labels = ", ".join(r["label"] for r in reasons[:3])
        print(
            f"{idx:2d}. {item.final_score:.3f} | base={item.base_score:.2f} "
            f"act={item.actionability:.2f} time={item.time_window:.2f} "
            f"avail={item.availability:.2f}"
        )
        print(f"    {item.event.title[:60]:60s}  @ {venue_name} ({city_name}) {date}")
        if reason_labels:
            print(f"    reasons: {reason_labels}")


def _movement_summary(scored: list[ScoredEvent]) -> None:
    """Print one example that moved up and one that moved down.

    Compares ranking on ``final_score`` vs ``base_score``. Pivots
    are only meaningful when the overlay actually shifts rank — so
    we print the most-moved rows in each direction.

    Args:
        scored: Per-user scored event list.
    """
    if len(scored) < 2:
        return
    by_final = sorted(scored, key=lambda s: -s.final_score)
    by_base = sorted(scored, key=lambda s: -s.base_score)
    final_rank = {item.event.id: idx for idx, item in enumerate(by_final)}
    base_rank = {item.event.id: idx for idx, item in enumerate(by_base)}
    movements = [
        (item, base_rank[item.event.id] - final_rank[item.event.id]) for item in scored
    ]
    movements.sort(key=lambda pair: pair[1], reverse=True)
    moved_up = movements[0] if movements and movements[0][1] > 0 else None
    movements.sort(key=lambda pair: pair[1])
    moved_down = movements[0] if movements and movements[0][1] < 0 else None
    if moved_up is not None:
        item, delta = moved_up
        venue_name = item.event.venue.name if item.event.venue else "?"
        city_name = (
            item.event.venue.city.name
            if item.event.venue and item.event.venue.city
            else "?"
        )
        date = item.event.starts_at.strftime("%Y-%m-%d")
        print(
            f"\n  MOVED UP +{delta}: {item.event.title[:60]}\n"
            f"    @ {venue_name} ({city_name}) {date}\n"
            f"    base={item.base_score:.3f} final={item.final_score:.3f} "
            f"act={item.actionability:.2f} time={item.time_window:.2f} "
            f"avail={item.availability:.2f}"
        )
    if moved_down is not None:
        item, delta = moved_down
        venue_name = item.event.venue.name if item.event.venue else "?"
        city_name = (
            item.event.venue.city.name
            if item.event.venue and item.event.venue.city
            else "?"
        )
        date = item.event.starts_at.strftime("%Y-%m-%d")
        print(
            f"\n  MOVED DOWN {delta}: {item.event.title[:60]}\n"
            f"    @ {venue_name} ({city_name}) {date}\n"
            f"    base={item.base_score:.3f} final={item.final_score:.3f} "
            f"act={item.actionability:.2f} time={item.time_window:.2f} "
            f"avail={item.availability:.2f}"
        )


def _success_metric_check(scored: list[ScoredEvent]) -> tuple[bool, list[str]]:
    """Verify the spec's success metric for one user.

    The spec asks: top 5 recommendations are all upcoming DMV shows
    where availability is not "sold out", AND at least 1 of the top
    10 would have ranked lower without the overlay (a relaxed
    per-user view of the 3-out-of-10-users gate).

    Args:
        scored: Per-user scored event list.

    Returns:
        Tuple of (passed, notes). ``notes`` is a small list of
        human-readable observations so the smoke output is
        self-explanatory.
    """
    notes: list[str] = []
    by_final = sorted(scored, key=lambda s: -s.final_score)
    top5 = by_final[:5]
    top10 = by_final[:10]
    if not top5:
        notes.append("user has no scored events; nothing to check")
        return False, notes
    all_dmv = all(item.actionability != 0.4 for item in top5)
    not_sold_out = all(item.availability != 0.45 for item in top5)
    notes.append(f"top-5 all DMV (no different-region picks): {all_dmv}")
    notes.append(f"top-5 none sold out: {not_sold_out}")
    by_base = sorted(scored, key=lambda s: -s.base_score)
    base_top10_ids = {item.event.id for item in by_base[:10]}
    overlay_top10_ids = {item.event.id for item in top10}
    moved_in = overlay_top10_ids - base_top10_ids
    notes.append(
        f"events in overlay-top-10 that were not in base-top-10: {len(moved_in)}"
    )
    passed = all_dmv and not_sold_out
    return passed, notes


def _ensure_synthetic_user(
    session: Any,
    *,
    email: str,
    city_id: uuid.UUID | None,
    genre_preferences: list[str] | None,
    spotify_top_artists: list[dict[str, Any]] | None = None,
) -> User:
    """Upsert a synthetic user keyed by email.

    Args:
        session: Active SQLAlchemy session.
        email: Stable email; used as the upsert key.
        city_id: Preferred city UUID for the user, or ``None``.
        genre_preferences: Genre slugs the user picked at onboarding.
        spotify_top_artists: Mock cached top-artist list.

    Returns:
        The persisted User row.
    """
    user = session.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is None:
        user = User(
            id=uuid.uuid4(),
            email=email,
            display_name=f"smoke-{email.split('@')[0]}",
            is_active=True,
        )
        session.add(user)
    user.city_id = city_id
    user.genre_preferences = genre_preferences
    user.spotify_top_artists = spotify_top_artists
    user.spotify_top_artist_ids = (
        [a["id"] for a in spotify_top_artists if a.get("id")]
        if spotify_top_artists
        else None
    )
    session.flush()
    return user


def main() -> None:
    """Run the smoke test against the live dev database."""
    dry_run = bool(os.environ.get("GREENROOM_SMOKE_DRY_RUN"))
    print(f"Smoke test starting (dry_run={dry_run})")
    session = get_session_factory()()
    try:
        events = _fetch_scoreable_events(session)
        print(f"Loaded {len(events)} scoreable events")

        # 1. Real account: garrett.sooter@gmail.com (DC, has genre prefs)
        garrett = session.execute(
            select(User).where(User.email == "garrett.sooter@gmail.com")
        ).scalar_one_or_none()
        if garrett is not None:
            scored = _score_user_events(session, garrett, events)
            _print_top("Garrett (DC, genre prefs only)", scored)
            _movement_summary(scored)
            ok, notes = _success_metric_check(scored)
            print(f"\n  success metric passed: {ok}")
            for note in notes:
                print(f"    - {note}")

        # Synthetic users — DC indie rock fan with Spotify cache, Richmond
        # jazz fan, no-preference electronic fan, thin-signal user.
        dc_city_id = session.execute(
            select(City.id).where(City.slug == "washington-dc")
        ).scalar_one()
        richmond_city_id = session.execute(
            select(City.id).where(City.slug == "richmond-va")
        ).scalar_one()

        # 2. DC indie rock fan with Spotify cache
        dc_fan = _ensure_synthetic_user(
            session,
            email="smoke-dc-indie@greenroom.test",
            city_id=dc_city_id,
            genre_preferences=["indie-rock", "alternative", "punk"],
            spotify_top_artists=[
                {"id": "phoebe", "name": "Phoebe Bridgers"},
                {"id": "yard", "name": "Yard Act"},
                {"id": "wednesday", "name": "Wednesday"},
            ],
        )
        scored = _score_user_events(session, dc_fan, events)
        _print_top("Synthetic DC indie-rock fan", scored, top_n=5)
        _movement_summary(scored)

        # 3. Richmond jazz fan
        rva_fan = _ensure_synthetic_user(
            session,
            email="smoke-rva-jazz@greenroom.test",
            city_id=richmond_city_id,
            genre_preferences=["jazz", "soul", "r-and-b"],
            spotify_top_artists=[
                {"id": "kamasi", "name": "Kamasi Washington"},
                {"id": "sb", "name": "Snarky Puppy"},
                {"id": "thundercat", "name": "Thundercat"},
            ],
        )
        scored = _score_user_events(session, rva_fan, events)
        _print_top("Synthetic Richmond jazz fan", scored, top_n=5)
        _movement_summary(scored)

        # 4. No-preference electronic fan
        nopref_fan = _ensure_synthetic_user(
            session,
            email="smoke-nopref-electronic@greenroom.test",
            city_id=None,
            genre_preferences=["electronic", "house", "techno"],
            spotify_top_artists=[
                {"id": "fk", "name": "Four Tet"},
                {"id": "fjkj", "name": "Floating Points"},
            ],
        )
        scored = _score_user_events(session, nopref_fan, events)
        _print_top("Synthetic no-preference electronic fan", scored, top_n=5)
        _movement_summary(scored)

        if dry_run:
            session.rollback()
        else:
            session.commit()
    finally:
        session.close()


if __name__ == "__main__":
    main()

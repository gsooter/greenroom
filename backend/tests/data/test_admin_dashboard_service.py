"""Tests for :mod:`backend.services.admin_dashboard`.

The dashboard service is heavily SQL-bound — counts, time-window
aggregates, leaderboards. Tests use the real Postgres test database
through the shared rolled-back-transaction session fixture.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from backend.data.models.artist_similarity import ArtistSimilarity
from backend.data.models.artists import Artist
from backend.data.models.cities import City
from backend.data.models.events import Event, EventStatus
from backend.data.models.hydration_log import HydrationLog
from backend.data.models.venues import Venue
from backend.services.admin_dashboard import (
    best_hydration_candidates,
    build_dashboard_snapshot,
    most_hydrated_leaderboard,
    serialize_dashboard_snapshot,
)


def _make_artist(
    session: Session,
    *,
    name: str,
    hydration_source: str | None = None,
) -> Artist:
    """Insert and return an :class:`Artist` row."""
    artist = Artist(
        name=name,
        normalized_name=name.lower().strip(),
        genres=[],
        hydration_source=hydration_source,
    )
    session.add(artist)
    session.flush()
    return artist


def test_dashboard_snapshot_reports_artist_split(session: Session) -> None:
    _make_artist(session, name="A")
    _make_artist(session, name="B")
    _make_artist(session, name="Hydrated", hydration_source="similar_artist")

    snap = build_dashboard_snapshot(session)

    assert snap.artists.total == 3
    assert snap.artists.breakdown["original"] == 2
    assert snap.artists.breakdown["hydrated"] == 1


def test_dashboard_snapshot_reports_event_split(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_event: Callable[..., Event],
) -> None:
    city = make_city()
    venue = make_venue(city=city)
    now = datetime.now(UTC)
    make_event(venue=venue, starts_at=now + timedelta(days=2))
    make_event(venue=venue, starts_at=now - timedelta(days=2))
    make_event(
        venue=venue,
        starts_at=now + timedelta(days=5),
        status=EventStatus.CANCELLED,
    )

    snap = build_dashboard_snapshot(session)
    assert snap.events.total == 3
    assert snap.events.breakdown["upcoming"] == 1
    assert snap.events.breakdown["past"] == 1
    assert snap.events.breakdown["cancelled"] == 1


def test_dashboard_snapshot_reports_venue_split(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
) -> None:
    city = make_city()
    make_venue(city=city, slug="active1")
    make_venue(city=city, slug="active2")
    make_venue(city=city, slug="inactive1", is_active=False)

    snap = build_dashboard_snapshot(session)
    assert snap.venues.total == 3
    assert snap.venues.breakdown["active"] == 2
    assert snap.venues.breakdown["inactive"] == 1


def test_dashboard_activity_reports_recent_users_and_events(
    session: Session,
    make_user: Callable[..., object],
) -> None:
    make_user()
    make_user()

    snap = build_dashboard_snapshot(session)
    assert any(w.label == "24 hours" for w in snap.activity)
    twentyfour = next(w for w in snap.activity if w.label == "24 hours")
    assert twentyfour.new_users >= 2


def test_dashboard_includes_three_activity_windows(session: Session) -> None:
    snap = build_dashboard_snapshot(session)
    labels = [w.label for w in snap.activity]
    assert labels == ["24 hours", "7 days", "30 days"]


def test_dashboard_includes_health_signals(session: Session) -> None:
    snap = build_dashboard_snapshot(session)
    labels = [h.label for h in snap.health]
    assert "Last successful scrape" in labels
    assert "Push delivery (24h)" in labels
    assert "Email bounce rate (7d)" in labels
    assert "Recommendation cache hit rate" in labels


def test_dashboard_daily_hydration_remaining_starts_at_cap(
    session: Session,
) -> None:
    snap = build_dashboard_snapshot(session)
    # No HydrationLog rows yet → full cap remaining.
    assert snap.daily_hydration_remaining == 100


def test_most_hydrated_leaderboard_orders_by_total_added(
    session: Session,
) -> None:
    a = _make_artist(session, name="Caamp")
    b = _make_artist(session, name="Phoebe Bridgers")
    session.add(
        HydrationLog(
            source_artist_id=a.id,
            admin_email="ops@x",
            candidate_artists=[],
            added_artist_ids=[uuid.uuid4(), uuid.uuid4(), uuid.uuid4()],
        )
    )
    session.add(
        HydrationLog(
            source_artist_id=b.id,
            admin_email="ops@x",
            candidate_artists=[],
            added_artist_ids=[uuid.uuid4()],
        )
    )
    session.flush()

    rows = most_hydrated_leaderboard(session, days=30)
    assert [r.artist_name for r in rows] == ["Caamp", "Phoebe Bridgers"]
    assert rows[0].hydration_count == 3
    assert rows[1].hydration_count == 1


def test_best_hydration_candidates_returns_top_unresolved_count(
    session: Session,
) -> None:
    seed = _make_artist(session, name="Seed Artist")
    other = _make_artist(session, name="Other")

    # 4 unresolved similars above threshold for Seed.
    for i, score in enumerate([0.95, 0.90, 0.85, 0.80]):
        session.add(
            ArtistSimilarity(
                source_artist_id=seed.id,
                similar_artist_name=f"Cand {i}",
                similar_artist_mbid=None,
                similar_artist_id=None,
                similarity_score=Decimal(f"{score:.3f}"),
                source="lastfm",
            )
        )
    # 1 below threshold — should be excluded.
    session.add(
        ArtistSimilarity(
            source_artist_id=seed.id,
            similar_artist_name="Weak",
            similar_artist_mbid=None,
            similar_artist_id=None,
            similarity_score=Decimal("0.10"),
            source="lastfm",
        )
    )
    # 1 already resolved — should be excluded too.
    session.add(
        ArtistSimilarity(
            source_artist_id=seed.id,
            similar_artist_name="Resolved",
            similar_artist_mbid=None,
            similar_artist_id=other.id,
            similarity_score=Decimal("0.92"),
            source="lastfm",
        )
    )
    # 1 unresolved similar for Other (at threshold).
    session.add(
        ArtistSimilarity(
            source_artist_id=other.id,
            similar_artist_name="Cand X",
            similar_artist_mbid=None,
            similar_artist_id=None,
            similarity_score=Decimal("0.55"),
            source="lastfm",
        )
    )
    session.flush()

    rows = best_hydration_candidates(session)
    assert rows[0].artist_name == "Seed Artist"
    assert rows[0].candidate_count == 4
    assert rows[0].top_candidate_name == "Cand 0"
    # Other appears second.
    assert any(r.artist_name == "Other" for r in rows)


def test_serializer_emits_expected_top_level_keys(session: Session) -> None:
    snap = build_dashboard_snapshot(session)
    body = serialize_dashboard_snapshot(snap)
    expected = {
        "users",
        "artists",
        "events",
        "venues",
        "music_connections",
        "push_subscriptions",
        "email_enabled_users",
        "activity",
        "health",
        "most_hydrated",
        "best_candidates",
        "daily_hydration_remaining",
    }
    assert expected.issubset(body.keys())

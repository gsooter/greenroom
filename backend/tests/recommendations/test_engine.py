"""Unit tests for :mod:`backend.recommendations.engine`.

The engine owns four concerns worth covering in isolation:

1. Short-circuit when the user has no cached Spotify artists.
2. Idempotent regeneration — existing rows are cleared before writing.
3. Per-event scoring loop — scorer abstention drops the event, totals
   cap at 1.0, sort is score-desc then start-time-asc.
4. ``_build_match_reasons`` flattens artist-match output into the UI
   chip format.

SQLAlchemy and the real repository are patched out so no database is
required.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.recommendations import engine as engine_module
from backend.recommendations.engine import (
    _build_match_reasons,
    generate_for_user,
)


@dataclass
class _FakeUser:
    """Minimal User stand-in exposing only the fields the engine reads."""

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    spotify_top_artists: list[dict[str, Any]] | None = None
    spotify_recent_artists: list[dict[str, Any]] | None = None
    tidal_top_artists: list[dict[str, Any]] | None = None
    apple_top_artists: list[dict[str, Any]] | None = None
    genre_preferences: list[str] | None = None


@dataclass
class _FakeEvent:
    """Minimal Event stand-in for engine unit tests."""

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    venue_id: uuid.UUID = field(default_factory=uuid.uuid4)
    title: str = "Untitled Show"
    starts_at: datetime = field(
        default_factory=lambda: datetime.now(UTC) + timedelta(days=1)
    )
    artists: list[str] | None = field(default_factory=list)
    spotify_artist_ids: list[str] | None = field(default_factory=list)
    genres: list[str] | None = field(default_factory=list)


@pytest.fixture
def patched_engine(monkeypatch: pytest.MonkeyPatch) -> dict[str, MagicMock]:
    """Wire MagicMocks around every IO-touching engine collaborator.

    Returns:
        Dict with ``delete``, ``create``, and ``fetch`` mocks so tests
        can assert call counts and arguments.
    """
    delete_mock = MagicMock(name="delete_recommendations_for_user")
    create_mock = MagicMock(name="create_recommendation")
    fetch_mock = MagicMock(name="_fetch_scoreable_events", return_value=[])
    affinity_mock = MagicMock(name="list_saved_venue_affinity", return_value={})
    monkeypatch.setattr(
        engine_module.users_repo,
        "delete_recommendations_for_user",
        delete_mock,
    )
    monkeypatch.setattr(engine_module.users_repo, "create_recommendation", create_mock)
    monkeypatch.setattr(
        engine_module.users_repo, "list_saved_venue_affinity", affinity_mock
    )
    monkeypatch.setattr(engine_module, "_fetch_scoreable_events", fetch_mock)
    return {
        "delete": delete_mock,
        "create": create_mock,
        "fetch": fetch_mock,
        "affinity": affinity_mock,
    }


def test_generate_returns_zero_when_user_has_no_top_artists(
    patched_engine: dict[str, MagicMock],
) -> None:
    """A user with no music cache and no preferences short-circuits."""
    session = MagicMock()
    user = _FakeUser(spotify_top_artists=None)
    result = generate_for_user(session, user)  # type: ignore[arg-type]
    assert result == 0
    patched_engine["delete"].assert_called_once_with(session, user.id)
    patched_engine["create"].assert_not_called()
    patched_engine["fetch"].assert_not_called()


def test_generate_runs_for_user_with_only_genre_preferences(
    patched_engine: dict[str, MagicMock],
) -> None:
    """Onboarding taste picks alone are enough to enter the scoring loop.

    A freshly onboarded user who hasn't connected Spotify yet still has
    genre slugs from Step 1 of /welcome. If the engine short-circuits
    on them, For-You shows zero rows and the graduation moment from
    onboarding feels broken.
    """
    session = MagicMock()
    user = _FakeUser(genre_preferences=["indie-rock"])
    event = _FakeEvent(artists=["Unknown"], genres=["indie rock"])
    patched_engine["fetch"].return_value = [event]

    result = generate_for_user(session, user)  # type: ignore[arg-type]

    assert result == 1
    call = patched_engine["create"].call_args_list[0]
    assert call.kwargs["event_id"] == event.id
    assert call.kwargs["score"] == 0.5
    reasons = call.kwargs["score_breakdown"]["_match_reasons"]
    assert reasons == [
        {
            "scorer": "artist_match",
            "kind": "genre_preference",
            "label": "Because you like Indie Rock",
            "genre_slug": "indie-rock",
        }
    ]


def test_generate_clears_prior_rows_before_writing(
    patched_engine: dict[str, MagicMock],
) -> None:
    """Even when nothing matches, the engine deletes prior rows idempotently."""
    session = MagicMock()
    user = _FakeUser(spotify_top_artists=[{"name": "Unknown"}])
    patched_engine["fetch"].return_value = [_FakeEvent(artists=["Some Other Band"])]
    result = generate_for_user(session, user)  # type: ignore[arg-type]
    assert result == 0
    patched_engine["delete"].assert_called_once_with(session, user.id)
    patched_engine["create"].assert_not_called()


def test_generate_persists_scored_events_in_score_desc_order(
    patched_engine: dict[str, MagicMock],
) -> None:
    """Events are written top-score-first with reasons flattened into breakdown."""
    session = MagicMock()
    user = _FakeUser(
        spotify_top_artists=[
            {"id": "id-a", "name": "Strong Match"},
            {"name": "Weak Match"},
        ]
    )
    strong = _FakeEvent(
        spotify_artist_ids=["id-a"],
        starts_at=datetime.now(UTC) + timedelta(days=5),
    )
    weak = _FakeEvent(
        artists=["Weak Match"],
        starts_at=datetime.now(UTC) + timedelta(days=1),
    )
    no_match = _FakeEvent(artists=["Nope"])
    patched_engine["fetch"].return_value = [weak, no_match, strong]

    result = generate_for_user(session, user)  # type: ignore[arg-type]

    assert result == 2
    create_calls = patched_engine["create"].call_args_list
    assert [c.kwargs["event_id"] for c in create_calls] == [strong.id, weak.id]
    assert create_calls[0].kwargs["score"] == 1.0
    assert create_calls[1].kwargs["score"] == 0.85
    breakdown = create_calls[0].kwargs["score_breakdown"]
    assert breakdown["artist_match"]["score"] == 1.0
    assert breakdown["_match_reasons"][0]["label"] == "You listen to Strong Match"


def test_generate_caps_total_score_at_one(
    patched_engine: dict[str, MagicMock],
) -> None:
    """Sum of scorer outputs never exceeds 1.0 when persisted."""
    session = MagicMock()
    user = _FakeUser(spotify_top_artists=[{"id": "id-a", "name": "Strong"}])
    event = _FakeEvent(spotify_artist_ids=["id-a"])
    patched_engine["fetch"].return_value = [event]

    class InflatingScorer:
        name = "inflate"

        def score(self, _event: Any) -> dict[str, Any]:
            return {"score": 5.0}

    monkeypatch_scorers = [
        engine_module.ArtistMatchScorer(user),  # type: ignore[arg-type]
        InflatingScorer(),
    ]
    # Replace _build_scorers so both scorers run for this case.
    original = engine_module._build_scorers
    engine_module._build_scorers = lambda _user, _affinity: monkeypatch_scorers  # type: ignore[assignment,return-value]
    try:
        result = generate_for_user(session, user)  # type: ignore[arg-type]
    finally:
        engine_module._build_scorers = original

    assert result == 1
    score = patched_engine["create"].call_args_list[0].kwargs["score"]
    assert score == 1.0


def test_generate_respects_limit(
    patched_engine: dict[str, MagicMock],
) -> None:
    """``limit`` kwarg caps how many rows we persist even when more match."""
    session = MagicMock()
    user = _FakeUser(spotify_top_artists=[{"id": "id-a", "name": "Solo"}])
    events = [_FakeEvent(spotify_artist_ids=["id-a"]) for _ in range(10)]
    patched_engine["fetch"].return_value = events

    result = generate_for_user(session, user, limit=3)  # type: ignore[arg-type]

    assert result == 3
    assert patched_engine["create"].call_count == 3


def test_generate_dedupes_same_show_at_same_venue(
    patched_engine: dict[str, MagicMock],
) -> None:
    """Two Event rows with the same (venue_id, title, starts_at) collapse.

    Ticketmaster occasionally emits two external IDs for the same show.
    Both end up in ``events`` and both would score identically; without
    deduping they'd both reach the user as separate rec cards.
    """
    session = MagicMock()
    user = _FakeUser(spotify_top_artists=[{"id": "id-a", "name": "Matched"}])
    shared_venue = uuid.uuid4()
    shared_time = datetime.now(UTC) + timedelta(days=3)
    dupe_a = _FakeEvent(
        venue_id=shared_venue,
        title="Matched at 9:30 Club",
        starts_at=shared_time,
        spotify_artist_ids=["id-a"],
    )
    dupe_b = _FakeEvent(
        venue_id=shared_venue,
        title="Matched at 9:30 Club",
        starts_at=shared_time,
        spotify_artist_ids=["id-a"],
    )
    unique = _FakeEvent(
        title="Different Show",
        starts_at=shared_time + timedelta(days=1),
        spotify_artist_ids=["id-a"],
    )
    patched_engine["fetch"].return_value = [dupe_a, dupe_b, unique]

    result = generate_for_user(session, user)  # type: ignore[arg-type]

    assert result == 2
    persisted_ids = [
        c.kwargs["event_id"] for c in patched_engine["create"].call_args_list
    ]
    assert dupe_b.id not in persisted_ids
    assert dupe_a.id in persisted_ids
    assert unique.id in persisted_ids


def test_build_match_reasons_flattens_artist_match_block() -> None:
    """Reason list is one entry per matched artist with the UI label."""
    breakdown = {
        "artist_match": {
            "score": 1.0,
            "matched_artists": [
                {"name": "A", "match": "spotify_id"},
                {"name": "B", "match": "artist_name"},
                {"match": "artist_name"},  # missing name — skipped
            ],
        }
    }
    reasons = _build_match_reasons(breakdown)
    assert reasons == [
        {
            "scorer": "artist_match",
            "kind": "spotify_id",
            "label": "You listen to A",
            "artist_name": "A",
        },
        {
            "scorer": "artist_match",
            "kind": "artist_name",
            "label": "You listen to B",
            "artist_name": "B",
        },
    ]


def test_build_match_reasons_handles_missing_artist_match_block() -> None:
    """A breakdown with no artist_match key returns an empty list."""
    assert _build_match_reasons({}) == []
    assert _build_match_reasons({"artist_match": "not-a-dict"}) == []


def test_build_match_reasons_surfaces_genre_preferences_and_overlap() -> None:
    """Preference and top-artist-genre matches produce their own chips."""
    breakdown = {
        "artist_match": {
            "score": 0.5,
            "matched_artists": [],
            "matched_preferences": [
                {"slug": "indie-rock", "label": "Indie Rock", "event_genre": "indie"},
                {"slug": "indie-rock", "label": "Indie Rock", "event_genre": "pop"},
                {"slug": "punk", "label": "Punk", "event_genre": "post-punk"},
            ],
            "matched_genres": ["shoegaze"],
        }
    }
    reasons = _build_match_reasons(breakdown)
    assert reasons == [
        {
            "scorer": "artist_match",
            "kind": "genre_preference",
            "label": "Because you like Indie Rock",
            "genre_slug": "indie-rock",
        },
        {
            "scorer": "artist_match",
            "kind": "genre_preference",
            "label": "Because you like Punk",
            "genre_slug": "punk",
        },
        {
            "scorer": "artist_match",
            "kind": "genre_overlap",
            "label": "Matches genre: shoegaze",
            "genre": "shoegaze",
        },
    ]


def test_build_match_reasons_orders_artists_before_preferences() -> None:
    """Artist chips come first so UI truncation keeps the strongest signal."""
    breakdown = {
        "artist_match": {
            "score": 1.0,
            "matched_artists": [{"name": "A", "match": "spotify_id"}],
            "matched_preferences": [
                {"slug": "punk", "label": "Punk", "event_genre": "punk"}
            ],
        }
    }
    reasons = _build_match_reasons(breakdown)
    assert [r["kind"] for r in reasons] == ["spotify_id", "genre_preference"]


def test_build_match_reasons_surfaces_venue_affinity_chip() -> None:
    """A venue-affinity scorer block produces a saved-venue chip."""
    breakdown = {
        "venue_affinity": {
            "score": 0.3,
            "matched_venue_id": str(uuid.uuid4()),
            "matched_venue_name": "Black Cat",
            "saved_count": 2,
        }
    }
    reasons = _build_match_reasons(breakdown)
    assert reasons == [
        {
            "scorer": "venue_affinity",
            "kind": "saved_venue",
            "label": "You've saved shows at Black Cat",
            "venue_name": "Black Cat",
        }
    ]


def test_build_match_reasons_skips_venue_affinity_without_name() -> None:
    """A venue block missing a usable name is silently skipped."""
    assert _build_match_reasons({"venue_affinity": {"score": 0.2}}) == []
    assert (
        _build_match_reasons(
            {"venue_affinity": {"score": 0.2, "matched_venue_name": "   "}}
        )
        == []
    )


def test_generate_runs_for_user_with_only_saved_venue_affinity(
    patched_engine: dict[str, MagicMock],
) -> None:
    """A user with no music cache still gets recs from saved-venue history.

    A login-only user who has never connected Spotify but actively saves
    shows at the same venue should see venue-affinity matches on
    For-You instead of an empty page.
    """
    session = MagicMock()
    user = _FakeUser()
    saved_venue = uuid.uuid4()
    patched_engine["affinity"].return_value = {
        saved_venue: {"count": 3, "name": "Black Cat"},
    }
    matched = _FakeEvent(venue_id=saved_venue, artists=["Unknown"])
    other = _FakeEvent(artists=["Unknown"])
    patched_engine["fetch"].return_value = [matched, other]

    result = generate_for_user(session, user)  # type: ignore[arg-type]

    assert result == 1
    call = patched_engine["create"].call_args_list[0]
    assert call.kwargs["event_id"] == matched.id
    assert call.kwargs["score"] == pytest.approx(0.4)
    breakdown = call.kwargs["score_breakdown"]
    assert breakdown["venue_affinity"]["matched_venue_name"] == "Black Cat"
    assert breakdown["_match_reasons"] == [
        {
            "scorer": "venue_affinity",
            "kind": "saved_venue",
            "label": "You've saved shows at Black Cat",
            "venue_name": "Black Cat",
        }
    ]


def test_fetch_scoreable_events_queries_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fetch helper executes a select and returns scalar rows."""
    session = MagicMock()
    scalars_result = MagicMock()
    scalars_result.all.return_value = ["event-1", "event-2"]
    session.execute.return_value.scalars.return_value = scalars_result
    events = engine_module._fetch_scoreable_events(session, limit=5)
    assert events == ["event-1", "event-2"]
    session.execute.assert_called_once()

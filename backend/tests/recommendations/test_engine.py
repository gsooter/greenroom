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
    city_id: uuid.UUID | None = None
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
    venue: Any = None
    status: Any = None


@pytest.fixture
def patched_engine(monkeypatch: pytest.MonkeyPatch) -> dict[str, MagicMock]:
    """Wire MagicMocks around every IO-touching engine collaborator.

    The DMV-aware overlays are also stubbed to neutral 1.0 by default
    so existing scorer-focused tests don't have to thread through
    venue/region wiring. Tests that exercise the overlay flow mutate
    these mocks (or use the dedicated integration tests below).

    Returns:
        Dict with the engine collaborator mocks: scorer-side
        (``delete``, ``create``, ``fetch``, ``canonical_genres``,
        ``affinity``, ``followed_artists``, ``followed_venues``,
        ``user_region``) and overlay-side (``actionability``,
        ``time_window``, ``availability``).
    """
    delete_mock = MagicMock(name="delete_recommendations_for_user")
    create_mock = MagicMock(name="create_recommendation")
    fetch_mock = MagicMock(name="_fetch_scoreable_events", return_value=[])
    canonical_genres_mock = MagicMock(
        name="_fetch_artist_canonical_genres", return_value={}
    )
    affinity_mock = MagicMock(name="list_saved_venue_affinity", return_value={})
    followed_artists_mock = MagicMock(
        name="list_followed_artist_signals",
        return_value={"spotify_ids": {}, "names": {}, "labels": {}},
    )
    followed_venues_mock = MagicMock(name="list_followed_venue_labels", return_value={})
    user_region_mock = MagicMock(name="_resolve_user_region_id", return_value=None)
    actionability_mock = MagicMock(
        name="compute_actionability_multiplier", return_value=1.0
    )
    time_window_mock = MagicMock(
        name="compute_time_window_multiplier", return_value=1.0
    )
    availability_mock = MagicMock(
        name="compute_availability_multiplier", return_value=1.0
    )
    monkeypatch.setattr(
        engine_module.users_repo,
        "delete_recommendations_for_user",
        delete_mock,
    )
    monkeypatch.setattr(engine_module.users_repo, "create_recommendation", create_mock)
    monkeypatch.setattr(
        engine_module.users_repo, "list_saved_venue_affinity", affinity_mock
    )
    monkeypatch.setattr(
        engine_module.follows_repo,
        "list_followed_artist_signals",
        followed_artists_mock,
    )
    monkeypatch.setattr(
        engine_module.follows_repo,
        "list_followed_venue_labels",
        followed_venues_mock,
    )
    monkeypatch.setattr(engine_module, "_fetch_scoreable_events", fetch_mock)
    monkeypatch.setattr(
        engine_module, "_fetch_artist_canonical_genres", canonical_genres_mock
    )
    monkeypatch.setattr(engine_module, "_resolve_user_region_id", user_region_mock)
    monkeypatch.setattr(
        engine_module, "compute_actionability_multiplier", actionability_mock
    )
    monkeypatch.setattr(
        engine_module, "compute_time_window_multiplier", time_window_mock
    )
    monkeypatch.setattr(
        engine_module, "compute_availability_multiplier", availability_mock
    )
    return {
        "delete": delete_mock,
        "create": create_mock,
        "fetch": fetch_mock,
        "canonical_genres": canonical_genres_mock,
        "affinity": affinity_mock,
        "followed_artists": followed_artists_mock,
        "followed_venues": followed_venues_mock,
        "user_region": user_region_mock,
        "actionability": actionability_mock,
        "time_window": time_window_mock,
        "availability": availability_mock,
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
    patched_engine["canonical_genres"].return_value = {"unknown": ["Indie Rock"]}

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

    monkeypatch_scorers: list[Any] = [
        engine_module.ArtistMatchScorer(user),  # type: ignore[arg-type]
        InflatingScorer(),
    ]
    # Replace _build_scorers so both scorers run for this case.
    original = engine_module._build_scorers
    engine_module._build_scorers = (  # type: ignore[assignment]
        lambda _user, _affinity, **_kwargs: monkeypatch_scorers
    )
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


def test_generate_runs_for_user_with_only_followed_artists(
    patched_engine: dict[str, MagicMock],
) -> None:
    """A user who only followed artists during onboarding still gets recs.

    This is the common shape for new users who skip Spotify connect: a
    handful of followed acts and nothing else. If the engine
    short-circuited on the empty music cache, For-You would be empty
    and the follow Step would feel performative.
    """
    session = MagicMock()
    user = _FakeUser()
    patched_engine["followed_artists"].return_value = {
        "spotify_ids": {"spot-a": "Phoebe Bridgers"},
        "names": {"phoebe bridgers": "Phoebe Bridgers"},
        "labels": {},
    }
    matched = _FakeEvent(spotify_artist_ids=["spot-a"], artists=["Opener"])
    other = _FakeEvent(artists=["Some Other Band"])
    patched_engine["fetch"].return_value = [matched, other]

    result = generate_for_user(session, user)  # type: ignore[arg-type]

    assert result == 1
    call = patched_engine["create"].call_args_list[0]
    assert call.kwargs["event_id"] == matched.id
    assert call.kwargs["score"] == pytest.approx(0.9)
    breakdown = call.kwargs["score_breakdown"]
    assert breakdown["followed_artist"]["matched_artists"] == [
        {"name": "Phoebe Bridgers", "match": "spotify_id"}
    ]
    assert breakdown["_match_reasons"] == [
        {
            "scorer": "followed_artist",
            "kind": "followed_artist",
            "label": "You follow Phoebe Bridgers",
            "artist_name": "Phoebe Bridgers",
        }
    ]


def test_generate_runs_for_user_with_only_followed_venues(
    patched_engine: dict[str, MagicMock],
) -> None:
    """A user with only followed venues gets venue-driven recs."""
    session = MagicMock()
    user = _FakeUser()
    venue_id = uuid.uuid4()
    patched_engine["followed_venues"].return_value = {venue_id: "9:30 Club"}
    matched = _FakeEvent(venue_id=venue_id, artists=["Unknown"])
    other = _FakeEvent(artists=["Unknown"])
    patched_engine["fetch"].return_value = [matched, other]

    result = generate_for_user(session, user)  # type: ignore[arg-type]

    assert result == 1
    call = patched_engine["create"].call_args_list[0]
    assert call.kwargs["event_id"] == matched.id
    assert call.kwargs["score"] == pytest.approx(0.45)
    reasons = call.kwargs["score_breakdown"]["_match_reasons"]
    assert reasons == [
        {
            "scorer": "followed_venue",
            "kind": "followed_venue",
            "label": "You follow 9:30 Club",
            "venue_name": "9:30 Club",
        }
    ]


def test_followed_venue_chip_dedupes_against_saved_venue_chip(
    patched_engine: dict[str, MagicMock],
) -> None:
    """Following AND saving the same venue produces one chip, not two.

    Both scorers fire (and both contribute score), but the reason list
    shows the stronger "You follow X" label and suppresses the weaker
    "You've saved shows at X" duplicate.
    """
    session = MagicMock()
    user = _FakeUser()
    venue_id = uuid.uuid4()
    patched_engine["followed_venues"].return_value = {venue_id: "Black Cat"}
    patched_engine["affinity"].return_value = {
        venue_id: {"count": 2, "name": "Black Cat"}
    }
    matched = _FakeEvent(venue_id=venue_id, artists=["Unknown"])
    patched_engine["fetch"].return_value = [matched]

    result = generate_for_user(session, user)  # type: ignore[arg-type]

    assert result == 1
    breakdown = patched_engine["create"].call_args_list[0].kwargs["score_breakdown"]
    # Both scorer blocks present.
    assert "followed_venue" in breakdown
    assert "venue_affinity" in breakdown
    # But only one venue chip surfaces in reasons.
    venue_reasons = [
        r
        for r in breakdown["_match_reasons"]
        if r["scorer"] in {"followed_venue", "venue_affinity"}
    ]
    assert venue_reasons == [
        {
            "scorer": "followed_venue",
            "kind": "followed_venue",
            "label": "You follow Black Cat",
            "venue_name": "Black Cat",
        }
    ]


def test_followed_artist_chip_dedupes_against_artist_match_chip(
    patched_engine: dict[str, MagicMock],
) -> None:
    """A user who both follows and streams an artist gets one chip."""
    session = MagicMock()
    user = _FakeUser(spotify_top_artists=[{"id": "spot-a", "name": "Phoebe Bridgers"}])
    patched_engine["followed_artists"].return_value = {
        "spotify_ids": {"spot-a": "Phoebe Bridgers"},
        "names": {"phoebe bridgers": "Phoebe Bridgers"},
        "labels": {},
    }
    matched = _FakeEvent(spotify_artist_ids=["spot-a"], artists=["Phoebe Bridgers"])
    patched_engine["fetch"].return_value = [matched]

    result = generate_for_user(session, user)  # type: ignore[arg-type]

    assert result == 1
    breakdown = patched_engine["create"].call_args_list[0].kwargs["score_breakdown"]
    artist_reasons = [
        r
        for r in breakdown["_match_reasons"]
        if r["scorer"] in {"artist_match", "followed_artist"}
    ]
    # The artist_match chip wins; followed_artist is deduped out.
    assert artist_reasons == [
        {
            "scorer": "artist_match",
            "kind": "spotify_id",
            "label": "You listen to Phoebe Bridgers",
            "artist_name": "Phoebe Bridgers",
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


# ---------------------------------------------------------------------------
# DMV-aware overlay integration (Decision 062)
# ---------------------------------------------------------------------------


def test_overlays_applied_once_per_event_after_combining_scorers(
    patched_engine: dict[str, MagicMock],
) -> None:
    """Overlays multiply the combined base, not each scorer's contribution.

    Computing overlays per-scorer would compound the multipliers
    (0.4^N -> near-zero) and is the bug the spec calls out as the
    "subtle but important" failure mode. Lock the order here.
    """
    session = MagicMock()
    user = _FakeUser(spotify_top_artists=[{"id": "id-a", "name": "Solo"}])
    event = _FakeEvent(spotify_artist_ids=["id-a"])
    patched_engine["fetch"].return_value = [event]
    patched_engine["actionability"].return_value = 0.5
    patched_engine["time_window"].return_value = 0.5
    patched_engine["availability"].return_value = 0.5

    result = generate_for_user(session, user)  # type: ignore[arg-type]

    assert result == 1
    # Each overlay should have been called exactly once for the one event,
    # not per scorer.
    assert patched_engine["actionability"].call_count == 1
    assert patched_engine["time_window"].call_count == 1
    assert patched_engine["availability"].call_count == 1
    score = patched_engine["create"].call_args_list[0].kwargs["score"]
    breakdown = patched_engine["create"].call_args_list[0].kwargs["score_breakdown"]
    # 1.0 (artist_match) x 0.5 x 0.5 x 0.5 = 0.125
    assert score == pytest.approx(0.125)
    assert breakdown["base"] == pytest.approx(1.0)
    assert breakdown["actionability"] == 0.5
    assert breakdown["time_window"] == 0.5
    assert breakdown["availability"] == 0.5


def test_final_score_equals_base_times_three_overlays(
    patched_engine: dict[str, MagicMock],
) -> None:
    """Persisted score equals base x actionability x time x availability.

    Property test against floating-point arithmetic — not a single
    arrangement but the documented one. If a future refactor inlines
    a different shape (additive bonus, partial overlay, etc.), this
    fails immediately.
    """
    session = MagicMock()
    user = _FakeUser(spotify_top_artists=[{"id": "id-a", "name": "Solo"}])
    event = _FakeEvent(spotify_artist_ids=["id-a"])
    patched_engine["fetch"].return_value = [event]
    patched_engine["actionability"].return_value = 0.85
    patched_engine["time_window"].return_value = 0.65
    patched_engine["availability"].return_value = 0.45

    generate_for_user(session, user)  # type: ignore[arg-type]
    create_call = patched_engine["create"].call_args_list[0]
    score = create_call.kwargs["score"]
    breakdown = create_call.kwargs["score_breakdown"]
    expected = (
        breakdown["base"]
        * breakdown["actionability"]
        * breakdown["time_window"]
        * breakdown["availability"]
    )
    assert score == pytest.approx(expected)


def test_breakdown_contains_all_four_score_components(
    patched_engine: dict[str, MagicMock],
) -> None:
    """Breakdown carries base / actionability / time_window / availability."""
    session = MagicMock()
    user = _FakeUser(spotify_top_artists=[{"id": "id-a", "name": "Solo"}])
    patched_engine["fetch"].return_value = [_FakeEvent(spotify_artist_ids=["id-a"])]
    patched_engine["actionability"].return_value = 0.85
    patched_engine["time_window"].return_value = 1.0
    patched_engine["availability"].return_value = 1.0

    generate_for_user(session, user)  # type: ignore[arg-type]
    breakdown = patched_engine["create"].call_args_list[0].kwargs["score_breakdown"]
    for key in ("base", "actionability", "time_window", "availability"):
        assert key in breakdown


def test_zero_availability_filters_event_from_recommendations(
    patched_engine: dict[str, MagicMock],
) -> None:
    """A score of 0 (cancelled) drops the event before persisting."""
    session = MagicMock()
    user = _FakeUser(spotify_top_artists=[{"id": "id-a", "name": "Solo"}])
    patched_engine["fetch"].return_value = [_FakeEvent(spotify_artist_ids=["id-a"])]
    patched_engine["availability"].return_value = 0.0

    result = generate_for_user(session, user)  # type: ignore[arg-type]
    assert result == 0
    patched_engine["create"].assert_not_called()


def test_zero_time_window_filters_event_from_recommendations(
    patched_engine: dict[str, MagicMock],
) -> None:
    """A score of 0 from the time-window overlay (past event) drops it."""
    session = MagicMock()
    user = _FakeUser(spotify_top_artists=[{"id": "id-a", "name": "Solo"}])
    patched_engine["fetch"].return_value = [_FakeEvent(spotify_artist_ids=["id-a"])]
    patched_engine["time_window"].return_value = 0.0

    result = generate_for_user(session, user)  # type: ignore[arg-type]
    assert result == 0


def test_user_region_resolved_once_per_run(
    patched_engine: dict[str, MagicMock],
) -> None:
    """The engine resolves the user's region exactly once per scoring run.

    Per the spec — the per-event overlay is O(1), so the region
    lookup must not happen per event. Mocking ``_resolve_user_region_id``
    and asserting one call lock that contract.
    """
    session = MagicMock()
    user_city = uuid.uuid4()
    user = _FakeUser(
        city_id=user_city,
        spotify_top_artists=[{"id": "id-a", "name": "Solo"}],
    )
    patched_engine["fetch"].return_value = [
        _FakeEvent(spotify_artist_ids=["id-a"]) for _ in range(5)
    ]

    generate_for_user(session, user)  # type: ignore[arg-type]
    assert patched_engine["user_region"].call_count == 1
    patched_engine["user_region"].assert_called_with(session, user_city)


def test_overlay_sort_respects_combined_score(
    patched_engine: dict[str, MagicMock],
) -> None:
    """A weak match in DC outranks a strong match downweighted by overlays.

    Two events tied on artist_match (1.0 each); one gets a 1.0
    overlay product, the other a 0.2 overlay product. The 1.0 product
    must persist first.
    """
    session = MagicMock()
    user = _FakeUser(spotify_top_artists=[{"id": "id-a", "name": "Solo"}])
    local = _FakeEvent(spotify_artist_ids=["id-a"], title="Local")
    far = _FakeEvent(spotify_artist_ids=["id-a"], title="Far")
    patched_engine["fetch"].return_value = [far, local]

    def actionability_side_effect(event, *_args, **_kwargs):
        return 1.0 if event.title == "Local" else 0.4

    def time_window_side_effect(event, *_args, **_kwargs):
        return 1.0 if event.title == "Local" else 0.5

    patched_engine["actionability"].side_effect = actionability_side_effect
    patched_engine["time_window"].side_effect = time_window_side_effect

    generate_for_user(session, user)  # type: ignore[arg-type]
    persisted_titles = [
        c.kwargs["event_id"] for c in patched_engine["create"].call_args_list
    ]
    assert persisted_titles == [local.id, far.id]


def test_resolve_user_region_id_returns_none_when_user_has_no_city() -> None:
    """The helper short-circuits when the user has no preferred city."""
    session = MagicMock()
    assert engine_module._resolve_user_region_id(session, None) is None
    session.execute.assert_not_called()


def test_resolve_user_region_id_uses_regions_repo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The helper delegates to ``regions_repo.get_region_for_city``."""
    session = MagicMock()
    region = MagicMock()
    region.id = uuid.uuid4()
    get_region_mock = MagicMock(return_value=region)
    monkeypatch.setattr(
        engine_module.regions_repo, "get_region_for_city", get_region_mock
    )
    city_id = uuid.uuid4()
    result = engine_module._resolve_user_region_id(session, city_id)
    assert result == region.id
    get_region_mock.assert_called_once_with(session, city_id)


def test_resolve_user_region_id_returns_none_when_city_has_no_region(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the city resolves to no region, the helper returns ``None``."""
    monkeypatch.setattr(
        engine_module.regions_repo,
        "get_region_for_city",
        MagicMock(return_value=None),
    )
    assert engine_module._resolve_user_region_id(MagicMock(), uuid.uuid4()) is None

"""Unit tests for :mod:`backend.scraper.runner`.

The runner orchestrates scrapers, commits to DB, logs ScraperRun rows,
and dispatches alerts on failure. Tests replace the scraper class,
repository calls, and the notifier at the module boundary so no DB
or network is touched.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.data.models.scraper import ScraperRunStatus
from backend.scraper import runner
from backend.scraper.base.models import RawEvent
from backend.scraper.base.scraper import BaseScraper
from backend.scraper.config.venues import VenueScraperConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _raw(
    *,
    title: str = "Show",
    starts_at: datetime | None = None,
    raw_data: dict[str, Any] | None = None,
    source_url: str = "https://src.test/e/1",
    artists: list[str] | None = None,
    genres: list[str] | None = None,
) -> RawEvent:
    """Build a RawEvent with sensible defaults for ingestion tests."""
    return RawEvent(
        title=title,
        venue_external_id="v1",
        starts_at=starts_at or datetime(2026, 5, 1, 20, tzinfo=UTC),
        source_url=source_url,
        raw_data=raw_data if raw_data is not None else {"id": "ext-1"},
        artists=artists or ["A"],
        genres=genres or [],
    )


class _FakeScraper(BaseScraper):
    """BaseScraper subclass that yields a caller-supplied list of RawEvents."""

    source_platform = "fake"

    def __init__(self, events: list[RawEvent] | None = None, **_k: Any) -> None:
        self._events = events or []

    def scrape(self) -> Iterator[RawEvent]:
        yield from self._events


class _ExplodingScraper(BaseScraper):
    """BaseScraper subclass whose ``scrape`` raises."""

    source_platform = "fake"

    def __init__(self, **_k: Any) -> None:
        pass

    def scrape(self) -> Iterator[RawEvent]:
        raise RuntimeError("scrape blew up")
        yield  # pragma: no cover - unreachable


@dataclass
class _FakeCity:
    timezone: str = "America/New_York"


@dataclass
class _FakeVenue:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    slug: str = "fake-venue"
    city: _FakeCity = field(default_factory=_FakeCity)


@dataclass
class _FakeEvent:
    title: str = "old"
    description: str | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    on_sale_at: datetime | None = None
    artists: list[str] = field(default_factory=list)
    genres: list[str] = field(default_factory=list)
    image_url: str | None = None
    ticket_url: str | None = None
    min_price: float | None = None
    max_price: float | None = None
    source_url: str | None = None
    raw_data: dict[str, Any] | None = None


def _cfg(
    *,
    scraper_class: str = "backend.scraper.runner._FakeScraperStub",
    venue_slug: str = "fake-venue",
    enabled: bool = True,
) -> VenueScraperConfig:
    return VenueScraperConfig(
        venue_slug=venue_slug,
        display_name="Fake",
        scraper_class=scraper_class,
        platform_config={},
        enabled=enabled,
    )


# ---------------------------------------------------------------------------
# _extract_external_id
# ---------------------------------------------------------------------------


def test_extract_external_id_prefers_id_field() -> None:
    raw = _raw(raw_data={"id": "tm-123"})
    assert runner._extract_external_id(raw) == "tm-123"


def test_extract_external_id_trims_whitespace() -> None:
    raw = _raw(raw_data={"id": "  tm-123  "})
    assert runner._extract_external_id(raw) == "tm-123"


def test_extract_external_id_falls_back_to_at_id() -> None:
    raw = _raw(raw_data={"@id": "https://ld.test/event/42"})
    assert runner._extract_external_id(raw) == "https://ld.test/event/42"


def test_extract_external_id_handles_int_id() -> None:
    """Numeric ids are coerced to string (TM sometimes returns ints)."""
    raw = _raw(raw_data={"id": 12345})
    assert runner._extract_external_id(raw) == "12345"


def test_extract_external_id_hashes_when_no_id() -> None:
    """Absent id: deterministic 32-char hash of url|title|starts_at."""
    raw = _raw(raw_data={})
    eid = runner._extract_external_id(raw)
    assert len(eid) == 32
    # Deterministic: same inputs → same hash.
    assert runner._extract_external_id(raw) == eid


def test_extract_external_id_ignores_empty_string_id() -> None:
    """Empty or whitespace id is skipped; falls through to hash path."""
    raw = _raw(raw_data={"id": "   "})
    eid = runner._extract_external_id(raw)
    assert len(eid) == 32


# ---------------------------------------------------------------------------
# _generate_slug
# ---------------------------------------------------------------------------


def test_generate_slug_is_deterministic() -> None:
    starts = datetime(2026, 5, 1, 20, tzinfo=UTC)
    s1 = runner._generate_slug("Phoebe Bridgers", "930-club", starts, "ext-1")
    s2 = runner._generate_slug("Phoebe Bridgers", "930-club", starts, "ext-1")
    assert s1 == s2
    assert "phoebe-bridgers" in s1
    assert "930-club" in s1
    assert "2026-05-01" in s1


def test_generate_slug_strips_punctuation_and_normalizes_whitespace() -> None:
    starts = datetime(2026, 5, 1, tzinfo=UTC)
    slug = runner._generate_slug("Foo!! / Bar  Baz", "x", starts, "id")
    assert "!" not in slug
    assert "/" not in slug
    assert "--" not in slug


def test_generate_slug_differentiates_by_external_id() -> None:
    """Colliding title+date still produce different slugs via hash suffix."""
    starts = datetime(2026, 5, 1, tzinfo=UTC)
    a = runner._generate_slug("Same", "v", starts, "id-a")
    b = runner._generate_slug("Same", "v", starts, "id-b")
    assert a != b


# ---------------------------------------------------------------------------
# _localize_venue_datetime
# ---------------------------------------------------------------------------


def test_localize_venue_datetime_attaches_zone_to_naive() -> None:
    """A naive 8 pm ET becomes an aware UTC datetime during EDT (UTC-4)."""
    naive = datetime(2026, 5, 1, 20, 0)
    result = runner._localize_venue_datetime(naive, "America/New_York")
    assert result is not None
    assert result.tzinfo is UTC
    assert result == datetime(2026, 5, 2, 0, 0, tzinfo=UTC)


def test_localize_venue_datetime_preserves_aware_input() -> None:
    """An already-aware datetime is normalized to UTC, not re-localized."""
    aware = datetime(2026, 5, 2, 0, 0, tzinfo=UTC)
    result = runner._localize_venue_datetime(aware, "America/New_York")
    assert result == aware


def test_localize_venue_datetime_passes_through_none() -> None:
    assert runner._localize_venue_datetime(None, "America/New_York") is None


# ---------------------------------------------------------------------------
# _update_event_from_raw
# ---------------------------------------------------------------------------


def test_update_event_from_raw_reports_changes() -> None:
    event = _FakeEvent(title="old", starts_at=datetime(2026, 1, 1))
    raw = _raw(
        title="new",
        starts_at=datetime(2026, 5, 1, 20, tzinfo=UTC),
    )
    changed = runner._update_event_from_raw(event, raw, "America/New_York")  # type: ignore[arg-type]
    assert changed is True
    assert event.title == "new"


def test_update_event_from_raw_no_change_returns_false() -> None:
    starts = datetime(2026, 5, 1, 20, tzinfo=UTC)
    event = _FakeEvent(
        title="Show",
        starts_at=starts,
        artists=["A"],
        raw_data={"id": "ext-1"},
        source_url="https://src.test/e/1",
    )
    raw = _raw(title="Show", starts_at=starts)
    changed = runner._update_event_from_raw(event, raw, "America/New_York")  # type: ignore[arg-type]
    assert changed is False


def test_update_event_from_raw_ignores_none_fields() -> None:
    """A ``None`` in raw never clobbers a populated field on the event."""
    event = _FakeEvent(title="keep", description="existing")
    raw = _raw(title="keep")
    raw.description = None
    runner._update_event_from_raw(event, raw, "America/New_York")  # type: ignore[arg-type]
    assert event.description == "existing"


def test_update_event_from_raw_propagates_genres() -> None:
    """Fresh genres from a re-scrape land on the existing event row."""
    event = _FakeEvent(title="Show", genres=[])
    raw = _raw(title="Show", genres=["indie", "rock"])
    changed = runner._update_event_from_raw(event, raw, "America/New_York")  # type: ignore[arg-type]
    assert changed is True
    assert event.genres == ["indie", "rock"]


def test_update_event_from_raw_empty_genres_do_not_clobber() -> None:
    """An empty genres list from a scraper keeps existing genres intact."""
    event = _FakeEvent(title="Show", genres=["indie"])
    raw = _raw(title="Show", genres=[])
    runner._update_event_from_raw(event, raw, "America/New_York")  # type: ignore[arg-type]
    assert event.genres == ["indie"]


# ---------------------------------------------------------------------------
# _upsert_artists
# ---------------------------------------------------------------------------


def test_upsert_artists_calls_repo_once_per_non_blank_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Valid names are forwarded; blanks and non-strings are skipped."""
    upsert_mock = MagicMock()
    monkeypatch.setattr(runner.artists_repo, "upsert_artist_by_name", upsert_mock)

    session = MagicMock()
    runner._upsert_artists(
        session,
        ["Phoebe Bridgers", "", "   ", "Boygenius"],  # type: ignore[list-item]
    )

    assert upsert_mock.call_count == 2
    forwarded = [call.args[1] for call in upsert_mock.call_args_list]
    assert forwarded == ["Phoebe Bridgers", "Boygenius"]


def test_upsert_artists_noop_on_empty_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upsert_mock = MagicMock()
    monkeypatch.setattr(runner.artists_repo, "upsert_artist_by_name", upsert_mock)
    runner._upsert_artists(MagicMock(), [])
    upsert_mock.assert_not_called()


# ---------------------------------------------------------------------------
# _instantiate_scraper
# ---------------------------------------------------------------------------


def test_instantiate_scraper_returns_basescraper() -> None:
    cfg = _cfg(scraper_class="backend.tests.scraper.test_runner._FakeScraper")
    instance = runner._instantiate_scraper(cfg)
    assert isinstance(instance, BaseScraper)


def test_instantiate_scraper_rejects_non_basescraper() -> None:
    cfg = _cfg(scraper_class="backend.tests.scraper.test_runner._NotAScraper")
    with pytest.raises(TypeError):
        runner._instantiate_scraper(cfg)


class _NotAScraper:  # - test fixture class name
    """Stand-in used to prove _instantiate_scraper rejects non-subclasses."""


# ---------------------------------------------------------------------------
# _ingest_events
# ---------------------------------------------------------------------------


def test_ingest_events_skips_everything_when_venue_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the venue isn't in the DB, all events are skipped with a log."""
    monkeypatch.setattr(runner.venues_repo, "get_venue_by_slug", lambda _s, _slug: None)
    monkeypatch.setattr(runner.artists_repo, "upsert_artist_by_name", MagicMock())
    created, updated, skipped = runner._ingest_events(
        MagicMock(), "ghost", [_raw(), _raw()], source_platform="fake"
    )
    assert (created, updated, skipped) == (0, 0, 2)


def test_ingest_events_creates_new_and_updates_existing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mixed path: one id has no row (create), another matches (update)."""
    venue = _FakeVenue()
    monkeypatch.setattr(
        runner.venues_repo, "get_venue_by_slug", lambda _s, _slug: venue
    )

    existing = _FakeEvent(title="old")

    lookup = {"ext-new": None, "ext-old": existing}

    def fake_get_by_ext(_s: Any, external_id: str, _plat: str) -> _FakeEvent | None:
        return lookup.get(external_id)

    monkeypatch.setattr(runner.events_repo, "get_event_by_external_id", fake_get_by_ext)

    create_mock = MagicMock()
    monkeypatch.setattr(runner.events_repo, "create_event", create_mock)
    upsert_mock = MagicMock()
    monkeypatch.setattr(runner.artists_repo, "upsert_artist_by_name", upsert_mock)

    session = MagicMock()
    raws = [
        _raw(title="New", raw_data={"id": "ext-new"}),
        _raw(title="Fresh", raw_data={"id": "ext-old"}),
    ]
    created, updated, skipped = runner._ingest_events(
        session, venue.slug, raws, source_platform="fake"
    )

    assert created == 1
    assert updated == 1
    assert skipped == 0
    create_mock.assert_called_once()
    session.flush.assert_called_once()
    # Every RawEvent's artists are funneled through the artists repo so
    # the enrichment task has rows to work on.
    assert upsert_mock.call_count == 2


def test_ingest_events_localizes_naive_starts_at_to_utc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Naive venue-local time from a scraper lands as UTC with ET offset applied."""
    venue = _FakeVenue(city=_FakeCity(timezone="America/New_York"))
    monkeypatch.setattr(
        runner.venues_repo, "get_venue_by_slug", lambda _s, _slug: venue
    )
    monkeypatch.setattr(
        runner.events_repo,
        "get_event_by_external_id",
        lambda _s, _eid, _plat: None,
    )
    create_mock = MagicMock()
    monkeypatch.setattr(runner.events_repo, "create_event", create_mock)
    monkeypatch.setattr(runner.artists_repo, "upsert_artist_by_name", MagicMock())

    # A 7 pm local show at a DMV venue in May (EDT, UTC-4).
    naive_local = datetime(2026, 5, 1, 19, 0)
    runner._ingest_events(
        MagicMock(), venue.slug, [_raw(starts_at=naive_local)], source_platform="fake"
    )

    stored = create_mock.call_args.kwargs["starts_at"]
    assert stored == datetime(2026, 5, 1, 23, 0, tzinfo=UTC)


def test_ingest_events_passes_genres_to_create_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RawEvent.genres flows into events_repo.create_event on insert."""
    venue = _FakeVenue()
    monkeypatch.setattr(
        runner.venues_repo, "get_venue_by_slug", lambda _s, _slug: venue
    )
    monkeypatch.setattr(
        runner.events_repo,
        "get_event_by_external_id",
        lambda _s, _eid, _plat: None,
    )
    create_mock = MagicMock()
    monkeypatch.setattr(runner.events_repo, "create_event", create_mock)
    monkeypatch.setattr(runner.artists_repo, "upsert_artist_by_name", MagicMock())

    raws = [_raw(title="Show", genres=["indie", "rock"])]

    runner._ingest_events(MagicMock(), venue.slug, raws, source_platform="fake")

    create_mock.assert_called_once()
    assert create_mock.call_args.kwargs["genres"] == ["indie", "rock"]


def test_ingest_events_passes_none_when_no_genres(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty genres list is normalized to None so the column stays default."""
    venue = _FakeVenue()
    monkeypatch.setattr(
        runner.venues_repo, "get_venue_by_slug", lambda _s, _slug: venue
    )
    monkeypatch.setattr(
        runner.events_repo,
        "get_event_by_external_id",
        lambda _s, _eid, _plat: None,
    )
    create_mock = MagicMock()
    monkeypatch.setattr(runner.events_repo, "create_event", create_mock)
    monkeypatch.setattr(runner.artists_repo, "upsert_artist_by_name", MagicMock())

    runner._ingest_events(MagicMock(), venue.slug, [_raw()], source_platform="fake")

    assert create_mock.call_args.kwargs["genres"] is None


def test_ingest_events_skips_unchanged_existing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Existing row with identical fields is skipped (no flush)."""
    venue = _FakeVenue()
    starts = datetime(2026, 5, 1, 20, tzinfo=UTC)
    existing = _FakeEvent(
        title="Same",
        starts_at=starts,
        artists=["A"],
        raw_data={"id": "ext-1"},
        source_url="https://src.test/e/1",
    )

    monkeypatch.setattr(
        runner.venues_repo, "get_venue_by_slug", lambda _s, _slug: venue
    )
    monkeypatch.setattr(
        runner.events_repo,
        "get_event_by_external_id",
        lambda _s, _eid, _plat: existing,
    )
    create_mock = MagicMock()
    monkeypatch.setattr(runner.events_repo, "create_event", create_mock)
    monkeypatch.setattr(runner.artists_repo, "upsert_artist_by_name", MagicMock())

    raws = [_raw(title="Same", starts_at=starts)]
    session = MagicMock()

    created, updated, skipped = runner._ingest_events(
        session, venue.slug, raws, source_platform="fake"
    )
    assert (created, updated, skipped) == (0, 0, 1)
    create_mock.assert_not_called()
    session.flush.assert_not_called()


# ---------------------------------------------------------------------------
# run_scraper_for_venue
# ---------------------------------------------------------------------------


def test_run_scraper_for_venue_happy_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end happy path: scrape → ingest → log → validate → return."""
    raws = [_raw(raw_data={"id": "ext-1"})]

    def fake_instantiate(_cfg: VenueScraperConfig) -> BaseScraper:
        return _FakeScraper(raws)

    monkeypatch.setattr(runner, "_instantiate_scraper", fake_instantiate)
    monkeypatch.setattr(
        runner,
        "_ingest_events",
        lambda _s, _slug, events, source_platform: (len(events), 0, 0),
    )
    create_run_mock = MagicMock()
    monkeypatch.setattr(runner.runs_repo, "create_scraper_run", create_run_mock)
    validate_mock = MagicMock(return_value=True)
    monkeypatch.setattr(runner, "validate_scraper_result", validate_mock)

    result = runner.run_scraper_for_venue(MagicMock(), _cfg())

    assert result["status"] == "success"
    assert result["event_count"] == 1
    assert result["created"] == 1
    create_run_mock.assert_called_once()
    assert create_run_mock.call_args.kwargs["status"] is ScraperRunStatus.SUCCESS
    validate_mock.assert_called_once()


def test_run_scraper_for_venue_logs_failure_and_alerts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scraper exception → FAILED run row + send_alert; result has 'failed'."""
    monkeypatch.setattr(
        runner,
        "_instantiate_scraper",
        lambda _c: _ExplodingScraper(),
    )
    create_run_mock = MagicMock()
    monkeypatch.setattr(runner.runs_repo, "create_scraper_run", create_run_mock)
    alert_mock = MagicMock()
    monkeypatch.setattr(runner, "send_alert", alert_mock)
    validate_mock = MagicMock()
    monkeypatch.setattr(runner, "validate_scraper_result", validate_mock)

    result = runner.run_scraper_for_venue(MagicMock(), _cfg())

    assert result["status"] == "failed"
    assert "scrape blew up" in result["error"]
    create_run_mock.assert_called_once()
    assert create_run_mock.call_args.kwargs["status"] is ScraperRunStatus.FAILED
    alert_mock.assert_called_once()
    alert_kwargs = alert_mock.call_args.kwargs
    assert alert_kwargs["severity"] == "error"
    assert alert_kwargs["alert_key"].startswith("scraper_failed:")
    assert alert_kwargs["cooldown_hours"] > 0
    validate_mock.assert_not_called()


def test_run_scraper_for_venue_fires_escalation_after_consecutive_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """3 consecutive failures fires both the per-venue and escalation alerts."""
    monkeypatch.setattr(runner, "_instantiate_scraper", lambda _c: _ExplodingScraper())
    monkeypatch.setattr(runner.runs_repo, "create_scraper_run", MagicMock())
    monkeypatch.setattr(
        runner.runs_repo,
        "count_consecutive_failed_runs",
        lambda *_a, **_k: 3,
    )
    alert_mock = MagicMock()
    monkeypatch.setattr(runner, "send_alert", alert_mock)
    monkeypatch.setattr(runner, "validate_scraper_result", MagicMock())

    result = runner.run_scraper_for_venue(MagicMock(), _cfg(venue_slug="dc9"))

    assert result["status"] == "failed"
    assert alert_mock.call_count == 2
    keys = [call.kwargs["alert_key"] for call in alert_mock.call_args_list]
    assert "scraper_failed:dc9" in keys
    assert "escalation:dc9" in keys

    escalation_call = next(
        c
        for c in alert_mock.call_args_list
        if c.kwargs["alert_key"] == "escalation:dc9"
    )
    assert escalation_call.kwargs["details"]["consecutive_failures"] == 3
    assert escalation_call.kwargs["cooldown_hours"] == 24.0


def test_run_scraper_for_venue_skips_escalation_below_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single failure in isolation only fires the per-venue alert."""
    monkeypatch.setattr(runner, "_instantiate_scraper", lambda _c: _ExplodingScraper())
    monkeypatch.setattr(runner.runs_repo, "create_scraper_run", MagicMock())
    monkeypatch.setattr(
        runner.runs_repo,
        "count_consecutive_failed_runs",
        lambda *_a, **_k: 1,
    )
    alert_mock = MagicMock()
    monkeypatch.setattr(runner, "send_alert", alert_mock)
    monkeypatch.setattr(runner, "validate_scraper_result", MagicMock())

    runner.run_scraper_for_venue(MagicMock(), _cfg(venue_slug="dc9"))

    assert alert_mock.call_count == 1
    assert alert_mock.call_args.kwargs["alert_key"] == "scraper_failed:dc9"


# ---------------------------------------------------------------------------
# run_all_scrapers
# ---------------------------------------------------------------------------


def test_run_all_scrapers_iterates_enabled_configs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every enabled config goes through run_scraper_for_venue."""
    configs = [_cfg(venue_slug="a"), _cfg(venue_slug="b")]
    monkeypatch.setattr(runner, "get_enabled_configs", lambda: configs)

    seen: list[str] = []

    def fake_run(_session: Any, config: VenueScraperConfig) -> dict[str, Any]:
        seen.append(config.venue_slug)
        return {"status": "success", "event_count": 0}

    monkeypatch.setattr(runner, "run_scraper_for_venue", fake_run)

    results = runner.run_all_scrapers(MagicMock())

    assert seen == ["a", "b"]
    assert set(results.keys()) == {"a", "b"}


def test_run_all_scrapers_fires_fleet_alert_when_failure_rate_exceeds_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """4/5 failures → one fleet alert with the failed venues listed."""
    configs = [_cfg(venue_slug=name) for name in ("a", "b", "c", "d", "e")]
    monkeypatch.setattr(runner, "get_enabled_configs", lambda: configs)

    def fake_run(_session: Any, config: VenueScraperConfig) -> dict[str, Any]:
        if config.venue_slug == "a":
            return {"status": "success"}
        return {"status": "failed", "error": "boom"}

    monkeypatch.setattr(runner, "run_scraper_for_venue", fake_run)
    alert_mock = MagicMock()
    monkeypatch.setattr(runner, "send_alert", alert_mock)

    runner.run_all_scrapers(MagicMock())

    alert_mock.assert_called_once()
    kwargs = alert_mock.call_args.kwargs
    assert kwargs["alert_key"] == "fleet_failure"
    assert kwargs["severity"] == "error"
    assert kwargs["details"]["failed"] == 4
    assert kwargs["details"]["total"] == 5
    # Failed venues are surfaced in the message.
    for slug in ("b", "c", "d", "e"):
        assert slug in kwargs["details"]["venues"]


def test_run_all_scrapers_does_not_fire_fleet_alert_below_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """1/5 failures stays below 40% — no fleet alert."""
    configs = [_cfg(venue_slug=name) for name in ("a", "b", "c", "d", "e")]
    monkeypatch.setattr(runner, "get_enabled_configs", lambda: configs)

    def fake_run(_session: Any, config: VenueScraperConfig) -> dict[str, Any]:
        if config.venue_slug == "a":
            return {"status": "failed", "error": "boom"}
        return {"status": "success"}

    monkeypatch.setattr(runner, "run_scraper_for_venue", fake_run)
    alert_mock = MagicMock()
    monkeypatch.setattr(runner, "send_alert", alert_mock)

    runner.run_all_scrapers(MagicMock())

    alert_mock.assert_not_called()


def test_run_all_scrapers_no_alert_on_zero_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clean run never fires the fleet alert, even on a small fleet."""
    configs = [_cfg(venue_slug="solo")]
    monkeypatch.setattr(runner, "get_enabled_configs", lambda: configs)
    monkeypatch.setattr(
        runner,
        "run_scraper_for_venue",
        lambda *_a, **_k: {"status": "success"},
    )
    alert_mock = MagicMock()
    monkeypatch.setattr(runner, "send_alert", alert_mock)

    runner.run_all_scrapers(MagicMock())

    alert_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Celery task wrappers
# ---------------------------------------------------------------------------


class _CtxSession:
    """Minimal session supporting the ``with`` protocol + commit/rollback."""

    def __init__(self) -> None:
        self.commit = MagicMock()
        self.rollback = MagicMock()

    def __enter__(self) -> _CtxSession:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


def test_scrape_all_venues_commits_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _CtxSession()
    monkeypatch.setattr(runner, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(runner, "run_all_scrapers", lambda _s: {"ok": True})
    result = runner.scrape_all_venues()
    assert result == {"ok": True}
    session.commit.assert_called_once()
    session.rollback.assert_not_called()


def test_scrape_all_venues_rolls_back_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _CtxSession()
    monkeypatch.setattr(runner, "get_session_factory", lambda: lambda: session)

    def boom(_s: Any) -> None:
        raise RuntimeError("ingest failed")

    monkeypatch.setattr(runner, "run_all_scrapers", boom)
    with pytest.raises(RuntimeError):
        runner.scrape_all_venues()
    session.rollback.assert_called_once()
    session.commit.assert_not_called()


def test_scrape_venue_raises_for_unknown_slug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner, "get_venue_config", lambda _s: None)
    with pytest.raises(ValueError):
        runner.scrape_venue("ghost")


def test_scrape_venue_raises_for_disabled_slug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runner,
        "get_venue_config",
        lambda _s: _cfg(venue_slug="off", enabled=False),
    )
    with pytest.raises(ValueError):
        runner.scrape_venue("off")


def test_scrape_venue_commits_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _cfg(venue_slug="ok")
    monkeypatch.setattr(runner, "get_venue_config", lambda _s: cfg)
    session = _CtxSession()
    monkeypatch.setattr(runner, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(
        runner,
        "run_scraper_for_venue",
        lambda _s, _c: {"status": "success"},
    )
    result = runner.scrape_venue("ok")
    assert result == {"status": "success"}
    session.commit.assert_called_once()


def test_scrape_venue_rolls_back_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _cfg(venue_slug="ok")
    monkeypatch.setattr(runner, "get_venue_config", lambda _s: cfg)
    session = _CtxSession()
    monkeypatch.setattr(runner, "get_session_factory", lambda: lambda: session)

    def boom(_s: Any, _c: VenueScraperConfig) -> None:
        raise RuntimeError("bad")

    monkeypatch.setattr(runner, "run_scraper_for_venue", boom)
    with pytest.raises(RuntimeError):
        runner.scrape_venue("ok")
    session.rollback.assert_called_once()

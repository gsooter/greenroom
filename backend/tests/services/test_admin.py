"""Unit tests for :mod:`backend.services.admin`."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.core.exceptions import NotFoundError, ValidationError
from backend.data.models.scraper import ScraperRunStatus
from backend.services import admin as admin_service


@dataclass
class _FakeRun:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    venue_slug: str = "black-cat"
    scraper_class: str = "BlackCatScraper"
    status: ScraperRunStatus = ScraperRunStatus.SUCCESS
    event_count: int = 42
    started_at: datetime = field(
        default_factory=lambda: datetime(2026, 4, 18, tzinfo=UTC)
    )
    finished_at: datetime | None = field(
        default_factory=lambda: datetime(2026, 4, 18, 0, 5, tzinfo=UTC)
    )
    duration_seconds: float = 5.0
    error_message: str | None = None
    metadata_json: dict[str, Any] | None = field(default_factory=lambda: {"k": "v"})


@dataclass
class _FakeConfig:
    venue_slug: str = "black-cat"
    display_name: str = "Black Cat"
    region: str = "DMV"
    city_slug: str = "washington-dc"
    scraper_class: str = "BlackCatScraper"
    enabled: bool = True


def test_list_scraper_runs_rejects_oversized_per_page() -> None:
    with pytest.raises(ValidationError):
        admin_service.list_scraper_runs(MagicMock(), per_page=101)


def test_list_scraper_runs_rejects_unknown_status() -> None:
    with pytest.raises(ValidationError):
        admin_service.list_scraper_runs(MagicMock(), status="weird")


def test_list_scraper_runs_forwards_enum_and_filters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_list(_session: Any, **kwargs: Any) -> tuple[list[Any], int]:
        captured.update(kwargs)
        return [], 0

    monkeypatch.setattr(admin_service.runs_repo, "list_scraper_runs", fake_list)
    admin_service.list_scraper_runs(
        MagicMock(), venue_slug="x", status="success", page=2, per_page=10
    )
    assert captured["status"] is ScraperRunStatus.SUCCESS
    assert captured["venue_slug"] == "x"


def test_trigger_scraper_run_raises_when_venue_config_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(admin_service, "get_venue_config", lambda _slug: None)
    with pytest.raises(NotFoundError):
        admin_service.trigger_scraper_run(MagicMock(), "ghost-venue")


def test_trigger_scraper_run_raises_when_venue_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        admin_service,
        "get_venue_config",
        lambda _slug: _FakeConfig(enabled=False),
    )
    with pytest.raises(NotFoundError):
        admin_service.trigger_scraper_run(MagicMock(), "black-cat")


def test_trigger_scraper_run_invokes_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _FakeConfig()
    monkeypatch.setattr(admin_service, "get_venue_config", lambda _slug: cfg)
    monkeypatch.setattr(
        admin_service,
        "run_scraper_for_venue",
        lambda _session, config: {"status": "success", "slug": config.venue_slug},
    )
    result = admin_service.trigger_scraper_run(MagicMock(), "black-cat")
    assert result["status"] == "success"
    assert result["slug"] == "black-cat"


def test_summarize_fleet_groups_by_region(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configs = [
        _FakeConfig(venue_slug="a", region="DMV"),
        _FakeConfig(venue_slug="b", region="DMV"),
        _FakeConfig(venue_slug="c", region="NYC"),
    ]
    monkeypatch.setattr(admin_service, "get_enabled_configs", lambda: configs)
    summary = admin_service.summarize_fleet()
    assert summary["enabled"] == 3
    assert summary["by_region"] == {"DMV": 2, "NYC": 1}
    assert [v["slug"] for v in summary["venues"]] == ["a", "b", "c"]


def test_serialize_scraper_run_renders_all_fields() -> None:
    run = _FakeRun()
    payload = admin_service.serialize_scraper_run(run)  # type: ignore[arg-type]
    assert payload["id"] == str(run.id)
    assert payload["status"] == "success"
    assert payload["event_count"] == 42
    assert payload["metadata"] == {"k": "v"}


def test_serialize_scraper_run_handles_null_metadata_and_finish() -> None:
    run = _FakeRun(finished_at=None, metadata_json=None)
    payload = admin_service.serialize_scraper_run(run)  # type: ignore[arg-type]
    assert payload["finished_at"] is None
    assert payload["metadata"] == {}

"""Unit tests for :mod:`backend.services.scraper_digest`.

The digest is a pure decision over a fleet snapshot and recent-alert
counts. Tests stub the repository layer and ``get_enabled_configs`` so
the rendered payload — title, severity, message body, structured
details — can be asserted directly without standing up a database.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from backend.data.models.scraper import ScraperRunStatus
from backend.scraper.config.venues import VenueScraperConfig
from backend.services import scraper_digest as digest_mod


def _config(slug: str, name: str | None = None) -> VenueScraperConfig:
    """Build a VenueScraperConfig for digest tests.

    Args:
        slug: Venue slug.
        name: Display name; falls back to a title-cased slug.

    Returns:
        A minimal :class:`VenueScraperConfig`.
    """
    return VenueScraperConfig(
        venue_slug=slug,
        display_name=name or slug.replace("-", " ").title(),
        scraper_class="test.Scraper",
    )


def _stub_fleet(
    monkeypatch: pytest.MonkeyPatch,
    *,
    snapshots: dict[str, dict[str, object]],
    alert_count: int = 0,
) -> list[VenueScraperConfig]:
    """Replace fleet/repo lookups with deterministic fixtures.

    Args:
        monkeypatch: pytest's monkeypatch fixture.
        snapshots: Mapping of venue slug → snapshot fields. Recognized
            keys: ``last_status`` (ScraperRunStatus or None),
            ``last_run_age_hours`` (int), ``last_success_age_hours``
            (int or None), ``consecutive_failures`` (int),
            ``display_name`` (str, optional).
        alert_count: Value returned by ``count_active_alerts``.

    Returns:
        The configs the digest will iterate over, ordered to match
        ``snapshots`` insertion order.
    """
    configs = [
        _config(slug, name=str(data.get("display_name") or slug))
        for slug, data in snapshots.items()
    ]
    monkeypatch.setattr(digest_mod, "get_enabled_configs", lambda: configs)

    now = datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(
        digest_mod.alerts_repo,
        "count_active_alerts",
        lambda _s, *, since: alert_count,
    )

    def _recent(_session: object, slug: str, *, limit: int = 1) -> list[object]:
        data = snapshots[slug]
        if data.get("last_status") is None:
            return []
        run = MagicMock()
        run.status = data["last_status"]
        run.started_at = now - timedelta(hours=int(data["last_run_age_hours"]))
        return [run]

    def _last_success(_session: object, slug: str) -> object | None:
        age = snapshots[slug].get("last_success_age_hours")
        if age is None:
            return None
        run = MagicMock()
        run.status = ScraperRunStatus.SUCCESS
        run.started_at = now - timedelta(hours=int(age))
        return run

    def _consecutive(_session: object, slug: str, *, limit: int = 10) -> int:
        return int(snapshots[slug].get("consecutive_failures", 0))

    monkeypatch.setattr(digest_mod.runs_repo, "get_recent_runs", _recent)
    monkeypatch.setattr(digest_mod.runs_repo, "get_last_successful_run", _last_success)
    monkeypatch.setattr(
        digest_mod.runs_repo,
        "count_consecutive_failed_runs",
        _consecutive,
    )
    return configs


def _now() -> datetime:
    """Return the canonical "now" used by digest fixtures.

    Returns:
        Frozen UTC timestamp matching ``_stub_fleet``.
    """
    return datetime(2026, 4, 25, 12, 0, tzinfo=UTC)


def test_all_healthy_fleet_emits_info_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clean fleet renders an info-severity digest with the success line."""
    _stub_fleet(
        monkeypatch,
        snapshots={
            "black-cat": {
                "last_status": ScraperRunStatus.SUCCESS,
                "last_run_age_hours": 6,
                "last_success_age_hours": 6,
                "consecutive_failures": 0,
            },
            "dc9": {
                "last_status": ScraperRunStatus.SUCCESS,
                "last_run_age_hours": 5,
                "last_success_age_hours": 5,
                "consecutive_failures": 0,
            },
        },
    )

    payload = digest_mod.build_digest_payload(MagicMock(), now=_now())

    assert payload.severity == "info"
    assert "2/2 healthy" in payload.title
    assert "All enabled scrapers ran cleanly" in payload.message
    assert payload.details == {
        "total": 2,
        "healthy": 2,
        "failing": 0,
        "stale": 0,
        "alerts_24h": 0,
    }


def test_failing_venue_drives_error_severity_and_section(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A FAILED head run flips the digest to error severity."""
    _stub_fleet(
        monkeypatch,
        snapshots={
            "black-cat": {
                "last_status": ScraperRunStatus.SUCCESS,
                "last_run_age_hours": 6,
                "last_success_age_hours": 6,
                "consecutive_failures": 0,
            },
            "dc9": {
                "last_status": ScraperRunStatus.FAILED,
                "last_run_age_hours": 5,
                "last_success_age_hours": 30,
                "consecutive_failures": 3,
                "display_name": "DC9 Nightclub",
            },
        },
        alert_count=4,
    )

    payload = digest_mod.build_digest_payload(MagicMock(), now=_now())

    assert payload.severity == "error"
    assert "1 failing" in payload.title
    assert "1/2 healthy" in payload.title
    assert "DC9 Nightclub" in payload.message
    assert "3 consecutive failures" in payload.message
    assert "30h ago" in payload.message
    assert payload.details["alerts_24h"] == 4
    assert payload.details["failing"] == 1


def test_stale_venue_yields_warning_when_no_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No failures but a stale success → warning severity, stale section."""
    _stub_fleet(
        monkeypatch,
        snapshots={
            "black-cat": {
                "last_status": ScraperRunStatus.SUCCESS,
                "last_run_age_hours": 6,
                "last_success_age_hours": 6,
                "consecutive_failures": 0,
            },
            "ottobar": {
                # Last run still succeeded but 60h ago — outside the
                # 36h success window so it should land in the stale set.
                "last_status": ScraperRunStatus.SUCCESS,
                "last_run_age_hours": 60,
                "last_success_age_hours": 60,
                "consecutive_failures": 0,
            },
        },
    )

    payload = digest_mod.build_digest_payload(MagicMock(), now=_now())

    assert payload.severity == "warning"
    assert "1 stale" in payload.title
    assert "Stale venues" in payload.message
    assert "ottobar" in payload.message
    assert payload.details["stale"] == 1


def test_failure_takes_precedence_over_stale_in_severity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When both failing and stale venues exist, severity is error."""
    _stub_fleet(
        monkeypatch,
        snapshots={
            "ottobar": {
                "last_status": ScraperRunStatus.SUCCESS,
                "last_run_age_hours": 60,
                "last_success_age_hours": 60,
                "consecutive_failures": 0,
            },
            "dc9": {
                "last_status": ScraperRunStatus.FAILED,
                "last_run_age_hours": 5,
                "last_success_age_hours": 30,
                "consecutive_failures": 2,
            },
        },
    )

    payload = digest_mod.build_digest_payload(MagicMock(), now=_now())

    assert payload.severity == "error"
    assert "1 failing" in payload.title
    assert "1 stale" in payload.title


def test_send_daily_digest_dispatches_with_alert_key_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dispatcher must skip cooldown dedup so the daily ping always lands."""
    _stub_fleet(
        monkeypatch,
        snapshots={
            "black-cat": {
                "last_status": ScraperRunStatus.SUCCESS,
                "last_run_age_hours": 6,
                "last_success_age_hours": 6,
                "consecutive_failures": 0,
            },
        },
    )
    alert_mock = MagicMock()
    monkeypatch.setattr(digest_mod, "send_alert", alert_mock)

    payload = digest_mod.send_daily_digest_for_session(MagicMock(), now=_now())

    alert_mock.assert_called_once()
    kwargs = alert_mock.call_args.kwargs
    assert kwargs["alert_key"] is None
    assert kwargs["severity"] == payload.severity
    assert kwargs["title"] == payload.title
    assert kwargs["message"] == payload.message
    assert kwargs["category"] == "digest"


def test_format_relative_handles_known_buckets() -> None:
    """``_format_relative`` covers never, sub-hour, hours, and day windows."""
    now = _now()
    assert digest_mod._format_relative(None, now) == "never"
    assert digest_mod._format_relative(now - timedelta(minutes=20), now) == "<1h ago"
    assert digest_mod._format_relative(now - timedelta(hours=5), now) == "5h ago"
    assert digest_mod._format_relative(now - timedelta(hours=72), now) == "3d ago"


def test_to_utc_attaches_tz_for_naive_input() -> None:
    """``_to_utc`` defensively re-attaches UTC for naive timestamps."""
    naive = datetime(2026, 4, 25, 10, 0)
    result = digest_mod._to_utc(naive)
    assert result.tzinfo is UTC
    assert result.replace(tzinfo=None) == naive

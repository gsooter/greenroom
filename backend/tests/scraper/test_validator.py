"""Unit tests for :mod:`backend.scraper.validator`.

The validator is a pure decision function over the repository's
30-run average and recent-run history. Tests stub the repo calls and
capture whether ``send_alert`` is invoked with the right severity.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.data.models.scraper import ScraperRunStatus
from backend.scraper import validator as validator_mod


def _stub_recent_runs(
    monkeypatch: pytest.MonkeyPatch,
    runs: list[Any] | None = None,
) -> None:
    """Stub ``runs_repo.get_recent_runs`` to return a fixed list.

    Used by tests that don't exercise the stale-data path themselves
    so the real DB query does not fire on a MagicMock session.
    """
    monkeypatch.setattr(
        validator_mod.runs_repo,
        "get_recent_runs",
        lambda _s, _slug, *, limit=30: runs or [],
    )


def _fake_run(
    *,
    status: ScraperRunStatus = ScraperRunStatus.SUCCESS,
    created: int = 0,
) -> MagicMock:
    """Build a stand-in ScraperRun with ``status`` and metadata payload.

    Args:
        status: The scraper run status.
        created: Number of events created — drives the stale-data check.

    Returns:
        A MagicMock that quacks like a ScraperRun for the validator's
        purposes.
    """
    run = MagicMock()
    run.status = status
    run.metadata_json = {"created": created}
    return run


def test_zero_events_fires_error_alert_and_returns_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Zero scraped events is always treated as a hard failure."""
    alert_mock = MagicMock()
    monkeypatch.setattr(validator_mod, "send_alert", alert_mock)
    _stub_recent_runs(monkeypatch)

    ok = validator_mod.validate_scraper_result(
        MagicMock(), venue_slug="black-cat", event_count=0
    )

    assert ok is False
    alert_mock.assert_called_once()
    kwargs = alert_mock.call_args.kwargs
    assert kwargs["severity"] == "error"
    assert kwargs["alert_key"] == "zero_results:black-cat"
    assert kwargs["cooldown_hours"] > 0


def test_no_history_short_circuits_to_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First-ever run (no avg) passes without alerting."""
    monkeypatch.setattr(
        validator_mod.runs_repo,
        "get_average_event_count",
        lambda _s, _slug: None,
    )
    _stub_recent_runs(monkeypatch)
    alert_mock = MagicMock()
    monkeypatch.setattr(validator_mod, "send_alert", alert_mock)

    ok = validator_mod.validate_scraper_result(
        MagicMock(), venue_slug="pie-shop", event_count=3
    )

    assert ok is True
    alert_mock.assert_not_called()


def test_zero_historical_average_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Edge case: avg is 0 (every prior run zeroed) — can't divide, we pass."""
    monkeypatch.setattr(
        validator_mod.runs_repo,
        "get_average_event_count",
        lambda _s, _slug: 0,
    )
    _stub_recent_runs(monkeypatch)
    alert_mock = MagicMock()
    monkeypatch.setattr(validator_mod, "send_alert", alert_mock)

    ok = validator_mod.validate_scraper_result(
        MagicMock(), venue_slug="dc9", event_count=5
    )

    assert ok is True
    alert_mock.assert_not_called()


def test_drop_above_threshold_fires_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A drop of >60% triggers a warning-level alert and returns False."""
    monkeypatch.setattr(
        validator_mod.runs_repo,
        "get_average_event_count",
        lambda _s, _slug: 20.0,
    )
    _stub_recent_runs(monkeypatch)
    alert_mock = MagicMock()
    monkeypatch.setattr(validator_mod, "send_alert", alert_mock)

    ok = validator_mod.validate_scraper_result(
        MagicMock(), venue_slug="black-cat", event_count=5
    )

    assert ok is False
    alert_mock.assert_called_once()
    kwargs = alert_mock.call_args.kwargs
    assert kwargs["severity"] == "warning"
    assert kwargs["alert_key"] == "event_drop:black-cat"
    assert kwargs["cooldown_hours"] > 0


def test_small_drop_below_threshold_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 20% drop from avg is within tolerance — no alert."""
    monkeypatch.setattr(
        validator_mod.runs_repo,
        "get_average_event_count",
        lambda _s, _slug: 20.0,
    )
    _stub_recent_runs(monkeypatch)
    alert_mock = MagicMock()
    monkeypatch.setattr(validator_mod, "send_alert", alert_mock)

    ok = validator_mod.validate_scraper_result(
        MagicMock(), venue_slug="black-cat", event_count=16
    )

    assert ok is True
    alert_mock.assert_not_called()


def test_count_equal_to_average_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exactly matching the historical avg is healthy."""
    monkeypatch.setattr(
        validator_mod.runs_repo,
        "get_average_event_count",
        lambda _s, _slug: 12.0,
    )
    _stub_recent_runs(monkeypatch)
    alert_mock = MagicMock()
    monkeypatch.setattr(validator_mod, "send_alert", alert_mock)

    ok = validator_mod.validate_scraper_result(
        MagicMock(), venue_slug="930-club", event_count=12
    )

    assert ok is True
    alert_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Stale-data check
# ---------------------------------------------------------------------------


def test_stale_data_alert_fires_after_threshold_zero_creates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Threshold consecutive successful runs with created=0 → warning."""
    monkeypatch.setattr(
        validator_mod.runs_repo,
        "get_average_event_count",
        lambda _s, _slug: 12.0,
    )
    _stub_recent_runs(
        monkeypatch,
        runs=[
            _fake_run(created=0) for _ in range(validator_mod.STALE_DATA_RUN_THRESHOLD)
        ],
    )
    alert_mock = MagicMock()
    monkeypatch.setattr(validator_mod, "send_alert", alert_mock)

    ok = validator_mod.validate_scraper_result(
        MagicMock(), venue_slug="dc9", event_count=12
    )

    # Validation still returns True — stale data is a non-blocking warning.
    assert ok is True
    alert_mock.assert_called_once()
    kwargs = alert_mock.call_args.kwargs
    assert kwargs["severity"] == "warning"
    assert kwargs["alert_key"] == "stale_data:dc9"


def test_stale_data_silent_when_not_enough_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Below the threshold of successful runs we don't alert."""
    monkeypatch.setattr(
        validator_mod.runs_repo,
        "get_average_event_count",
        lambda _s, _slug: 12.0,
    )
    _stub_recent_runs(
        monkeypatch,
        runs=[
            _fake_run(created=0)
            for _ in range(validator_mod.STALE_DATA_RUN_THRESHOLD - 1)
        ],
    )
    alert_mock = MagicMock()
    monkeypatch.setattr(validator_mod, "send_alert", alert_mock)

    validator_mod.validate_scraper_result(MagicMock(), venue_slug="dc9", event_count=12)

    alert_mock.assert_not_called()


def test_stale_data_silent_when_any_run_created_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even one successful run with created>0 resets the stale signal."""
    monkeypatch.setattr(
        validator_mod.runs_repo,
        "get_average_event_count",
        lambda _s, _slug: 12.0,
    )
    runs = [
        _fake_run(created=0) for _ in range(validator_mod.STALE_DATA_RUN_THRESHOLD - 1)
    ]
    runs.append(_fake_run(created=2))
    _stub_recent_runs(monkeypatch, runs=runs)
    alert_mock = MagicMock()
    monkeypatch.setattr(validator_mod, "send_alert", alert_mock)

    validator_mod.validate_scraper_result(MagicMock(), venue_slug="dc9", event_count=12)

    alert_mock.assert_not_called()


def test_stale_data_skips_failed_runs_in_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failed runs in the window don't count — only success rows do.

    A one-off failure shouldn't either trigger or reset the stale check.
    The validator filters to SUCCESS rows and looks at the most recent
    threshold of those.
    """
    monkeypatch.setattr(
        validator_mod.runs_repo,
        "get_average_event_count",
        lambda _s, _slug: 12.0,
    )
    runs = [_fake_run(status=ScraperRunStatus.FAILED)]
    runs.extend(
        _fake_run(created=0) for _ in range(validator_mod.STALE_DATA_RUN_THRESHOLD)
    )
    _stub_recent_runs(monkeypatch, runs=runs)
    alert_mock = MagicMock()
    monkeypatch.setattr(validator_mod, "send_alert", alert_mock)

    validator_mod.validate_scraper_result(MagicMock(), venue_slug="dc9", event_count=12)

    alert_mock.assert_called_once()
    assert alert_mock.call_args.kwargs["alert_key"] == "stale_data:dc9"


def test_stale_data_check_runs_alongside_drop_alert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A drop alert does not suppress the stale-data check.

    Both can be true at once — and stale data is often the *cause* of
    a sudden drop, so the operator wants to see both signals.
    """
    monkeypatch.setattr(
        validator_mod.runs_repo,
        "get_average_event_count",
        lambda _s, _slug: 20.0,
    )
    _stub_recent_runs(
        monkeypatch,
        runs=[
            _fake_run(created=0) for _ in range(validator_mod.STALE_DATA_RUN_THRESHOLD)
        ],
    )
    alert_mock = MagicMock()
    monkeypatch.setattr(validator_mod, "send_alert", alert_mock)

    ok = validator_mod.validate_scraper_result(
        MagicMock(), venue_slug="dc9", event_count=5
    )

    assert ok is False
    keys = [c.kwargs["alert_key"] for c in alert_mock.call_args_list]
    assert "event_drop:dc9" in keys
    assert "stale_data:dc9" in keys

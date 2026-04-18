"""Unit tests for :mod:`backend.scraper.validator`.

The validator is a pure decision function over the repository's
30-run average. Tests stub the repo call and capture whether
``send_alert`` is invoked with the right severity.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backend.scraper import validator as validator_mod


def test_zero_events_fires_error_alert_and_returns_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Zero scraped events is always treated as a hard failure."""
    alert_mock = MagicMock()
    monkeypatch.setattr(validator_mod, "send_alert", alert_mock)

    ok = validator_mod.validate_scraper_result(
        MagicMock(), venue_slug="black-cat", event_count=0
    )

    assert ok is False
    alert_mock.assert_called_once()
    assert alert_mock.call_args.kwargs["severity"] == "error"


def test_no_history_short_circuits_to_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First-ever run (no avg) passes without alerting."""
    monkeypatch.setattr(
        validator_mod.runs_repo,
        "get_average_event_count",
        lambda _s, _slug: None,
    )
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
    alert_mock = MagicMock()
    monkeypatch.setattr(validator_mod, "send_alert", alert_mock)

    ok = validator_mod.validate_scraper_result(
        MagicMock(), venue_slug="black-cat", event_count=5
    )

    assert ok is False
    alert_mock.assert_called_once()
    assert alert_mock.call_args.kwargs["severity"] == "warning"


def test_small_drop_below_threshold_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 20% drop from avg is within tolerance — no alert."""
    monkeypatch.setattr(
        validator_mod.runs_repo,
        "get_average_event_count",
        lambda _s, _slug: 20.0,
    )
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
    alert_mock = MagicMock()
    monkeypatch.setattr(validator_mod, "send_alert", alert_mock)

    ok = validator_mod.validate_scraper_result(
        MagicMock(), venue_slug="930-club", event_count=12
    )

    assert ok is True
    alert_mock.assert_not_called()

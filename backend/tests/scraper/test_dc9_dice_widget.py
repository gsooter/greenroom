"""Unit tests for :mod:`backend.scraper.watchdogs.dc9_dice_widget`.

The watchdog is a weekly Celery probe — no DB, no ORM. Tests exercise
the HTML-comment stripping helper directly and patch ``fetch_html`` /
``send_alert`` at the module boundary for the task function.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backend.scraper.base.http import HttpFetchError
from backend.scraper.watchdogs import dc9_dice_widget

# ---------------------------------------------------------------------------
# is_widget_live
# ---------------------------------------------------------------------------


def test_is_widget_live_detects_uncommented_widget() -> None:
    """A bare widget div with the magic id counts as live."""
    html = '<html><body><div id="dice-event-list-widget"></div></body></html>'
    assert dc9_dice_widget.is_widget_live(html) is True


def test_is_widget_live_returns_false_when_commented() -> None:
    """A widget div inside an HTML comment does NOT count as live."""
    html = '<html><body><!-- <div id="dice-event-list-widget"></div> --></body></html>'
    assert dc9_dice_widget.is_widget_live(html) is False


def test_is_widget_live_handles_multiline_comment() -> None:
    """A multi-line comment wrapping the widget still strips it out."""
    html = (
        '<html><body><!--\n<div id="dice-event-list-widget"></div>\n-->\n</body></html>'
    )
    assert dc9_dice_widget.is_widget_live(html) is False


def test_is_widget_live_false_for_unrelated_page() -> None:
    """A page with no widget markup at all returns False."""
    assert dc9_dice_widget.is_widget_live("<html><body>nope</body></html>") is False


# ---------------------------------------------------------------------------
# check_dc9_dice_widget (Celery task)
# ---------------------------------------------------------------------------


def test_check_dispatches_alert_when_widget_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live widget triggers a single ``send_alert`` call with widget id."""
    monkeypatch.setattr(
        dc9_dice_widget,
        "fetch_html",
        lambda _url: '<div id="dice-event-list-widget"></div>',
    )
    alert_mock = MagicMock()
    monkeypatch.setattr(dc9_dice_widget, "send_alert", alert_mock)

    result = dc9_dice_widget.check_dc9_dice_widget()

    assert result["live"] is True
    assert result["error"] is None
    alert_mock.assert_called_once()
    kwargs = alert_mock.call_args.kwargs
    assert kwargs["severity"] == "warning"
    assert kwargs["details"]["widget_id"] == dc9_dice_widget.WIDGET_ID


def test_check_skips_alert_when_widget_commented_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Commented-out widget → no alert, ``live`` is False."""
    monkeypatch.setattr(
        dc9_dice_widget,
        "fetch_html",
        lambda _url: '<!-- <div id="dice-event-list-widget"></div> -->',
    )
    alert_mock = MagicMock()
    monkeypatch.setattr(dc9_dice_widget, "send_alert", alert_mock)

    result = dc9_dice_widget.check_dc9_dice_widget()

    assert result["live"] is False
    assert result["error"] is None
    alert_mock.assert_not_called()


def test_check_returns_error_when_fetch_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HttpFetchError is swallowed; no alert; ``live`` is None."""

    def boom(_url: str) -> str:
        raise HttpFetchError("dc9 down")

    monkeypatch.setattr(dc9_dice_widget, "fetch_html", boom)
    alert_mock = MagicMock()
    monkeypatch.setattr(dc9_dice_widget, "send_alert", alert_mock)

    result = dc9_dice_widget.check_dc9_dice_widget()

    assert result["live"] is None
    assert "dc9 down" in result["error"]  # type: ignore[operator]
    alert_mock.assert_not_called()

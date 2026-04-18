"""Unit tests for :mod:`backend.scraper.notifier`.

Notifier dispatches to Slack via ``requests.post`` and falls back to
SendGrid. Tests patch both seams and exercise the success/failure
branches plus the env-gated no-op branches.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests

from backend.scraper import notifier


class _FakeSlackResponse:
    """Minimal Slack webhook response stand-in — only ``raise_for_status`` is called."""

    def __init__(self, *, status_code: int = 200) -> None:
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class _FakeSendGridResponse:
    """Stand-in for the sendgrid library's response object."""

    def __init__(self, *, status_code: int = 202) -> None:
        self.status_code = status_code


# ---------------------------------------------------------------------------
# send_alert — top-level flow
# ---------------------------------------------------------------------------


def test_send_alert_uses_slack_when_it_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 200 from the Slack webhook short-circuits — email is not touched."""
    settings = MagicMock()
    settings.slack_webhook_url = "https://hooks.slack.test/abc"
    monkeypatch.setattr(notifier, "get_settings", lambda: settings)

    post_mock = MagicMock(return_value=_FakeSlackResponse(status_code=200))
    monkeypatch.setattr(notifier.requests, "post", post_mock)

    email_mock = MagicMock()
    monkeypatch.setattr(notifier, "_send_email_alert", email_mock)

    notifier.send_alert(
        title="hello",
        message="world",
        severity="info",
        details={"venue": "black-cat"},
    )

    post_mock.assert_called_once()
    email_mock.assert_not_called()


def test_send_alert_falls_back_to_email_when_slack_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If Slack returns False, email is attempted as a fallback."""
    monkeypatch.setattr(notifier, "_send_slack_alert", lambda **_kw: False)
    email_mock = MagicMock()
    monkeypatch.setattr(notifier, "_send_email_alert", email_mock)

    notifier.send_alert(title="t", message="m", severity="warning")

    email_mock.assert_called_once()


# ---------------------------------------------------------------------------
# _send_slack_alert branches
# ---------------------------------------------------------------------------


def test_slack_alert_noop_when_webhook_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Placeholder ``x`` webhook is treated as unconfigured and returns False."""
    settings = MagicMock()
    settings.slack_webhook_url = "x"
    monkeypatch.setattr(notifier, "get_settings", lambda: settings)

    post_mock = MagicMock()
    monkeypatch.setattr(notifier.requests, "post", post_mock)

    result = notifier._send_slack_alert(
        title="t", message="m", severity="warning"
    )

    assert result is False
    post_mock.assert_not_called()


def test_slack_alert_returns_false_on_request_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RequestException during post is caught and reported as failure."""
    settings = MagicMock()
    settings.slack_webhook_url = "https://hooks.slack.test/abc"
    monkeypatch.setattr(notifier, "get_settings", lambda: settings)

    def boom(*_a: object, **_k: object) -> None:
        raise requests.ConnectionError("no dns")

    monkeypatch.setattr(notifier.requests, "post", boom)

    result = notifier._send_slack_alert(
        title="t", message="m", severity="error", details={"k": "v"}
    )
    assert result is False


def test_slack_alert_defaults_color_for_unknown_severity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unknown severity string still posts; we don't raise."""
    settings = MagicMock()
    settings.slack_webhook_url = "https://hooks.slack.test/abc"
    monkeypatch.setattr(notifier, "get_settings", lambda: settings)

    post_mock = MagicMock(return_value=_FakeSlackResponse(status_code=200))
    monkeypatch.setattr(notifier.requests, "post", post_mock)

    result = notifier._send_slack_alert(
        title="t", message="m", severity="catastrophic"
    )

    assert result is True
    post_mock.assert_called_once()


# ---------------------------------------------------------------------------
# _send_email_alert branches
# ---------------------------------------------------------------------------


def test_email_alert_noop_when_alert_email_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Placeholder ``x@x.com`` recipient short-circuits to False."""
    settings = MagicMock()
    settings.alert_email = "x@x.com"
    settings.sendgrid_api_key = "real-key"
    monkeypatch.setattr(notifier, "get_settings", lambda: settings)

    assert (
        notifier._send_email_alert(title="t", message="m", severity="warning")
        is False
    )


def test_email_alert_noop_when_sendgrid_key_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Placeholder ``x`` sendgrid key short-circuits to False."""
    settings = MagicMock()
    settings.alert_email = "alerts@example.test"
    settings.sendgrid_api_key = "x"
    monkeypatch.setattr(notifier, "get_settings", lambda: settings)

    assert (
        notifier._send_email_alert(title="t", message="m", severity="warning")
        is False
    )


def test_email_alert_returns_false_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Any exception during SendGrid send is swallowed and reported False."""
    settings = MagicMock()
    settings.alert_email = "alerts@example.test"
    settings.sendgrid_api_key = "SG.real"
    settings.sendgrid_from_email = "from@example.test"
    monkeypatch.setattr(notifier, "get_settings", lambda: settings)

    # Patch the sendgrid module imported inside the function so .send raises.
    import sendgrid

    class _BoomClient:
        def __init__(self, *_a: object, **_k: object) -> None: ...

        def send(self, _mail: object) -> None:
            raise RuntimeError("sendgrid down")

    monkeypatch.setattr(sendgrid, "SendGridAPIClient", _BoomClient)

    assert (
        notifier._send_email_alert(
            title="t", message="m", severity="warning", details={"k": "v"}
        )
        is False
    )


def test_email_alert_returns_true_on_2xx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 202 from SendGrid maps to True."""
    settings = MagicMock()
    settings.alert_email = "alerts@example.test"
    settings.sendgrid_api_key = "SG.real"
    settings.sendgrid_from_email = "from@example.test"
    monkeypatch.setattr(notifier, "get_settings", lambda: settings)

    import sendgrid

    class _OkClient:
        def __init__(self, *_a: object, **_k: object) -> None: ...

        def send(self, _mail: object) -> _FakeSendGridResponse:
            return _FakeSendGridResponse(status_code=202)

    monkeypatch.setattr(sendgrid, "SendGridAPIClient", _OkClient)

    assert (
        notifier._send_email_alert(
            title="t", message="m", severity="warning", details={"k": "v"}
        )
        is True
    )

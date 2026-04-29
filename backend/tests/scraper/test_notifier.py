"""Unit tests for :mod:`backend.scraper.notifier`.

Notifier dispatches to Slack via ``requests.post`` and falls back to the
``services.email.send_email`` seam for Resend delivery. Tests patch both
seams and exercise the success/failure branches plus the env-gated no-op
branches.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests

from backend.core.exceptions import EMAIL_DELIVERY_FAILED, AppError
from backend.scraper import notifier


class _FakeSlackResponse:
    """Minimal Slack webhook response stand-in — only ``raise_for_status`` is called."""

    def __init__(self, *, status_code: int = 200) -> None:
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


# ---------------------------------------------------------------------------
# send_alert — top-level flow
# ---------------------------------------------------------------------------


def test_send_alert_uses_slack_when_it_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 200 from the Slack webhook short-circuits — email is not touched."""
    settings = MagicMock()
    settings.slack_webhook_ops_url = "https://hooks.slack.test/abc"
    settings.slack_webhook_digest_url = ""
    settings.slack_webhook_feedback_url = ""
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
    settings.slack_webhook_ops_url = "x"
    settings.slack_webhook_digest_url = ""
    settings.slack_webhook_feedback_url = ""
    monkeypatch.setattr(notifier, "get_settings", lambda: settings)

    post_mock = MagicMock()
    monkeypatch.setattr(notifier.requests, "post", post_mock)

    result = notifier._send_slack_alert(title="t", message="m", severity="warning")

    assert result is False
    post_mock.assert_not_called()


def test_slack_alert_returns_false_on_request_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RequestException during post is caught and reported as failure."""
    settings = MagicMock()
    settings.slack_webhook_ops_url = "https://hooks.slack.test/abc"
    settings.slack_webhook_digest_url = ""
    settings.slack_webhook_feedback_url = ""
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
    settings.slack_webhook_ops_url = "https://hooks.slack.test/abc"
    settings.slack_webhook_digest_url = ""
    settings.slack_webhook_feedback_url = ""
    monkeypatch.setattr(notifier, "get_settings", lambda: settings)

    post_mock = MagicMock(return_value=_FakeSlackResponse(status_code=200))
    monkeypatch.setattr(notifier.requests, "post", post_mock)

    result = notifier._send_slack_alert(title="t", message="m", severity="catastrophic")

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
    settings.resend_api_key = "real-key"
    monkeypatch.setattr(notifier, "get_settings", lambda: settings)

    assert (
        notifier._send_email_alert(title="t", message="m", severity="warning") is False
    )


def test_email_alert_noop_when_resend_key_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Placeholder ``x`` resend key short-circuits to False."""
    settings = MagicMock()
    settings.alert_email = "alerts@example.test"
    settings.resend_api_key = "x"
    monkeypatch.setattr(notifier, "get_settings", lambda: settings)

    assert (
        notifier._send_email_alert(title="t", message="m", severity="warning") is False
    )


def test_email_alert_returns_false_when_send_email_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AppError from ``send_email`` is swallowed and reported as False."""
    settings = MagicMock()
    settings.alert_email = "alerts@example.test"
    settings.resend_api_key = "re_real"
    settings.resend_from_email = "from@example.test"
    monkeypatch.setattr(notifier, "get_settings", lambda: settings)

    def boom(**_kw: object) -> None:
        raise AppError(
            code=EMAIL_DELIVERY_FAILED,
            message="resend down",
            status_code=502,
        )

    monkeypatch.setattr(notifier, "send_email", boom)

    assert (
        notifier._send_email_alert(
            title="t", message="m", severity="warning", details={"k": "v"}
        )
        is False
    )


def test_email_alert_returns_true_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful ``send_email`` call maps to True and carries the full body."""
    settings = MagicMock()
    settings.alert_email = "alerts@example.test"
    settings.resend_api_key = "re_real"
    settings.resend_from_email = "from@example.test"
    monkeypatch.setattr(notifier, "get_settings", lambda: settings)

    send_mock = MagicMock(return_value=None)
    monkeypatch.setattr(notifier, "send_email", send_mock)

    result = notifier._send_email_alert(
        title="scraper down",
        message="zero events",
        severity="error",
        details={"venue": "black-cat"},
    )

    assert result is True
    send_mock.assert_called_once()
    kwargs = send_mock.call_args.kwargs
    assert kwargs["to"] == "alerts@example.test"
    assert kwargs["subject"] == "[Greenroom Scraper] scraper down"
    assert "zero events" in kwargs["text_body"]
    assert "venue: black-cat" in kwargs["text_body"]


# ---------------------------------------------------------------------------
# Cooldown / dedup behaviour
# ---------------------------------------------------------------------------


def test_send_alert_no_dedup_when_alert_key_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without alert_key, the dedup repo is never consulted."""
    monkeypatch.setattr(notifier, "_send_slack_alert", lambda **_kw: True)

    suppress_mock = MagicMock()
    record_mock = MagicMock()
    monkeypatch.setattr(notifier.alerts_repo, "should_suppress", suppress_mock)
    monkeypatch.setattr(notifier.alerts_repo, "record_alert", record_mock)

    sent = notifier.send_alert(title="t", message="m", severity="warning")

    assert sent is True
    suppress_mock.assert_not_called()
    record_mock.assert_not_called()


def test_send_alert_suppressed_when_within_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A suppressing prior send short-circuits Slack/email and returns False."""
    slack_mock = MagicMock(return_value=True)
    email_mock = MagicMock(return_value=True)
    record_mock = MagicMock()
    monkeypatch.setattr(notifier, "_send_slack_alert", slack_mock)
    monkeypatch.setattr(notifier, "_send_email_alert", email_mock)
    monkeypatch.setattr(notifier.alerts_repo, "should_suppress", lambda *_a, **_k: True)
    monkeypatch.setattr(notifier.alerts_repo, "record_alert", record_mock)

    session = MagicMock()
    sent = notifier.send_alert(
        title="t",
        message="m",
        severity="error",
        alert_key="zero_results:bc",
        cooldown_hours=12.0,
        session=session,
    )

    assert sent is False
    slack_mock.assert_not_called()
    email_mock.assert_not_called()
    record_mock.assert_not_called()


def test_send_alert_records_attempt_when_not_suppressed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-suppressed send records the dedup row with the same payload."""
    slack_mock = MagicMock(return_value=True)
    record_mock = MagicMock()
    monkeypatch.setattr(notifier, "_send_slack_alert", slack_mock)
    monkeypatch.setattr(
        notifier.alerts_repo, "should_suppress", lambda *_a, **_k: False
    )
    monkeypatch.setattr(notifier.alerts_repo, "record_alert", record_mock)

    session = MagicMock()
    sent = notifier.send_alert(
        title="Zero results",
        message="m",
        severity="error",
        details={"venue": "bc"},
        alert_key="zero_results:bc",
        cooldown_hours=12.0,
        session=session,
    )

    assert sent is True
    slack_mock.assert_called_once()
    record_mock.assert_called_once()
    kwargs = record_mock.call_args.kwargs
    assert kwargs["alert_key"] == "zero_results:bc"
    assert kwargs["severity"] == "error"
    assert kwargs["title"] == "Zero results"
    assert kwargs["details"] == {"venue": "bc"}


def test_send_alert_records_attempt_even_when_delivery_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed Slack+email delivery still consumes a cooldown slot.

    Otherwise a broken Slack integration would cause the notifier to
    retry on every nightly run, hammering the disabled webhook.
    """
    monkeypatch.setattr(notifier, "_send_slack_alert", lambda **_kw: False)
    monkeypatch.setattr(notifier, "_send_email_alert", lambda **_kw: False)
    monkeypatch.setattr(
        notifier.alerts_repo, "should_suppress", lambda *_a, **_k: False
    )

    record_mock = MagicMock()
    monkeypatch.setattr(notifier.alerts_repo, "record_alert", record_mock)

    sent = notifier.send_alert(
        title="t",
        message="m",
        severity="error",
        alert_key="scraper_failed:bc",
        cooldown_hours=6.0,
        session=MagicMock(),
    )

    assert sent is True
    record_mock.assert_called_once()


def test_send_alert_fails_open_when_dedup_read_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broken dedup table never blocks an alert.

    If ``should_suppress`` raises, we err on the side of delivery — a
    silent monitoring stack is the worse failure mode.
    """
    slack_mock = MagicMock(return_value=True)
    monkeypatch.setattr(notifier, "_send_slack_alert", slack_mock)
    monkeypatch.setattr(notifier.alerts_repo, "record_alert", lambda *_a, **_k: None)

    def boom(*_a: object, **_k: object) -> bool:
        raise RuntimeError("DB down")

    monkeypatch.setattr(notifier.alerts_repo, "should_suppress", boom)

    sent = notifier.send_alert(
        title="t",
        message="m",
        severity="error",
        alert_key="zero_results:bc",
        cooldown_hours=12.0,
        session=MagicMock(),
    )

    assert sent is True
    slack_mock.assert_called_once()


def test_send_alert_uses_scoped_session_when_none_supplied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the caller omits ``session``, notifier opens its own scoped one."""
    monkeypatch.setattr(notifier, "_send_slack_alert", lambda **_kw: True)
    suppress_mock = MagicMock(return_value=False)
    record_mock = MagicMock()
    monkeypatch.setattr(notifier.alerts_repo, "should_suppress", suppress_mock)
    monkeypatch.setattr(notifier.alerts_repo, "record_alert", record_mock)

    fake_session = MagicMock()

    class _FakeFactory:
        def __call__(self) -> MagicMock:
            return fake_session

    monkeypatch.setattr(notifier, "get_session_factory", lambda: _FakeFactory())

    sent = notifier.send_alert(
        title="t",
        message="m",
        severity="error",
        alert_key="zero_results:bc",
        cooldown_hours=12.0,
    )

    assert sent is True
    # The scoped session is committed independently and closed.
    fake_session.commit.assert_called_once()
    fake_session.close.assert_called()
    suppress_mock.assert_called_once()
    record_mock.assert_called_once()


# ---------------------------------------------------------------------------
# Category → webhook routing
# ---------------------------------------------------------------------------


def _settings_with(
    *,
    ops: str = "",
    digest: str = "",
    feedback: str = "",
) -> MagicMock:
    """Build a mock Settings populated with the three webhook URLs."""
    settings = MagicMock()
    settings.slack_webhook_ops_url = ops
    settings.slack_webhook_digest_url = digest
    settings.slack_webhook_feedback_url = feedback
    return settings


def test_resolve_webhook_url_routes_each_category(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each category resolves to its own URL when configured."""
    monkeypatch.setattr(
        notifier,
        "get_settings",
        lambda: _settings_with(
            ops="https://ops.example",
            digest="https://digest.example",
            feedback="https://feedback.example",
        ),
    )
    assert notifier._resolve_webhook_url("ops") == "https://ops.example"
    assert notifier._resolve_webhook_url("digest") == "https://digest.example"
    assert notifier._resolve_webhook_url("feedback") == "https://feedback.example"


def test_resolve_webhook_url_falls_back_to_ops_when_category_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unset category URL falls back to the ops URL."""
    monkeypatch.setattr(
        notifier,
        "get_settings",
        lambda: _settings_with(ops="https://ops.example"),
    )
    assert notifier._resolve_webhook_url("digest") == "https://ops.example"
    assert notifier._resolve_webhook_url("feedback") == "https://ops.example"


def test_slack_alert_posts_to_category_webhook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The webhook URL passed to requests.post matches the category."""
    monkeypatch.setattr(
        notifier,
        "get_settings",
        lambda: _settings_with(
            ops="https://ops.example",
            feedback="https://feedback.example",
        ),
    )
    post_mock = MagicMock(return_value=_FakeSlackResponse(status_code=200))
    monkeypatch.setattr(notifier.requests, "post", post_mock)

    notifier._send_slack_alert(
        title="t",
        message="m",
        severity="info",
        category="feedback",
    )

    assert post_mock.call_args.args[0] == "https://feedback.example"


def test_send_alert_forwards_category_to_slack_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The top-level helper forwards ``category`` to ``_send_slack_alert``."""
    captured: dict[str, object] = {}

    def fake_slack(**kwargs: object) -> bool:
        captured.update(kwargs)
        return True

    monkeypatch.setattr(notifier, "_send_slack_alert", fake_slack)
    monkeypatch.setattr(notifier, "_send_email_alert", lambda **_kw: True)

    notifier.send_alert(
        title="hi",
        message="m",
        severity="info",
        category="digest",
    )
    assert captured["category"] == "digest"


def test_send_alert_defaults_category_to_ops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An omitted ``category`` argument routes to ops."""
    captured: dict[str, object] = {}

    def fake_slack(**kwargs: object) -> bool:
        captured.update(kwargs)
        return True

    monkeypatch.setattr(notifier, "_send_slack_alert", fake_slack)
    notifier.send_alert(title="t", message="m", severity="warning")
    assert captured["category"] == "ops"

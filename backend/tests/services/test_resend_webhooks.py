"""Unit tests for the Resend webhook handler service."""

from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Any

import pytest

from backend.core.exceptions import ValidationError
from backend.services import resend_webhooks


def _build_signature(secret_b64: str, svix_id: str, ts: int, payload: bytes) -> str:
    secret_bytes = base64.b64decode(secret_b64)
    signed = f"{svix_id}.{ts}.".encode() + payload
    sig = base64.b64encode(
        hmac.new(secret_bytes, signed, hashlib.sha256).digest()
    ).decode("ascii")
    return f"v1,{sig}"


@pytest.fixture
def webhook_secret(monkeypatch: pytest.MonkeyPatch) -> str:
    """Configure a deterministic Resend webhook secret for the test."""
    raw = b"a" * 32
    secret = "whsec_" + base64.b64encode(raw).decode("ascii")
    monkeypatch.setenv("RESEND_WEBHOOK_SECRET", secret)
    return secret


def test_verify_signature_accepts_valid_payload(
    webhook_secret: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b'{"type":"email.bounced","data":{}}'
    ts = 1_700_000_000
    monkeypatch.setattr(resend_webhooks, "_now", lambda: ts)
    sig_header = _build_signature(webhook_secret[len("whsec_") :], "msg_1", ts, payload)
    resend_webhooks.verify_signature(
        payload,
        svix_id="msg_1",
        svix_timestamp=str(ts),
        svix_signature=sig_header,
    )


def test_verify_signature_rejects_bad_signature(
    webhook_secret: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b'{"type":"email.bounced","data":{}}'
    ts = 1_700_000_000
    monkeypatch.setattr(resend_webhooks, "_now", lambda: ts)
    with pytest.raises(ValidationError):
        resend_webhooks.verify_signature(
            payload,
            svix_id="msg_1",
            svix_timestamp=str(ts),
            svix_signature="v1,not-a-valid-signature",
        )


def test_verify_signature_rejects_old_timestamp(
    webhook_secret: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"{}"
    ts = 1_700_000_000
    monkeypatch.setattr(resend_webhooks, "_now", lambda: ts + 10_000)
    sig = _build_signature(webhook_secret[len("whsec_") :], "msg_1", ts, payload)
    with pytest.raises(ValidationError):
        resend_webhooks.verify_signature(
            payload,
            svix_id="msg_1",
            svix_timestamp=str(ts),
            svix_signature=sig,
        )


def test_verify_signature_rejects_unconfigured_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RESEND_WEBHOOK_SECRET", "")
    with pytest.raises(ValidationError):
        resend_webhooks.verify_signature(
            b"{}",
            svix_id="msg_1",
            svix_timestamp="1700000000",
            svix_signature="v1,xyz",
        )


def test_handle_event_ignores_non_failure_types() -> None:
    result = resend_webhooks.handle_event(
        session=None,  # type: ignore[arg-type]
        event={"type": "email.opened", "data": {"to": ["x@example.com"]}},
    )
    assert result == {"action": "ignored", "reason": "not_a_hard_fail_event"}


def test_handle_event_ignores_event_without_recipient() -> None:
    result = resend_webhooks.handle_event(
        session=None,  # type: ignore[arg-type]
        event={"type": "email.bounced", "data": {}},
    )
    assert result == {"action": "ignored", "reason": "no_recipient"}


def test_handle_event_marks_user_when_recipient_known(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the user lookup resolves, the event marks them bounced."""
    fake_user: Any = type(
        "FakeUser",
        (),
        {
            "id": "fake-id",
            "email_bounced_at": None,
            "email_bounce_reason": None,
        },
    )()

    def fake_get_user_by_email(_session: Any, email: str) -> Any:
        assert email == "user@example.com"
        return fake_user

    import backend.services.resend_webhooks as mod

    monkeypatch.setattr(
        "backend.data.repositories.users.get_user_by_email",
        fake_get_user_by_email,
    )
    result = mod.handle_event(
        session=None,  # type: ignore[arg-type]
        event={
            "type": "email.bounced",
            "data": {"to": ["User@Example.com"]},
        },
    )
    assert result["action"] == "marked_hard_bounce"
    assert fake_user.email_bounced_at is not None
    assert fake_user.email_bounce_reason == "hard_bounce"

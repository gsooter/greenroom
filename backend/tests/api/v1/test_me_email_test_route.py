"""Tests for the user-facing ``POST /api/v1/me/email/test`` endpoint.

Pin the auth gate, the bounce-suppression branch, the no-email branch,
the delivery-failed branch, and the happy path. ``compose_email`` is
mocked so the suite never hits Resend.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from flask.testing import FlaskClient

from backend.core.exceptions import EMAIL_DELIVERY_FAILED, AppError
from backend.data.models.users import User


def _stub_compose_email(monkeypatch: Any) -> list[dict[str, Any]]:
    """Replace compose_email with a recorder. Returns the call list."""
    calls: list[dict[str, Any]] = []

    def fake_compose(**kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(
        "backend.api.v1.email_test.email_service.compose_email",
        fake_compose,
    )
    return calls


def test_send_test_email_to_self_requires_auth(client: FlaskClient) -> None:
    response = client.post("/api/v1/me/email/test")
    assert response.status_code == 401


def test_send_test_email_to_self_happy_path_invokes_compose_and_masks_email(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: Any,
) -> None:
    """Happy path: compose_email is called with the right scope/template
    and the response masks the recipient address."""
    client, user, headers = authed_client
    calls = _stub_compose_email(monkeypatch)

    response = client.post("/api/v1/me/email/test", headers=headers())

    assert response.status_code == 200
    body = response.get_json()
    assert body["data"]["sent"] is True
    assert body["data"]["reason"] == "sent"
    # Mask preserves the first letter and the full domain.
    assert body["data"]["to"] == "p***@example.test"
    assert len(calls) == 1
    call = calls[0]
    assert call["to"] == user.email
    assert call["template"] == "show_announcement"
    assert call["scope"] == "weekly_digest"
    assert call["subject"] == "Greenroom test email"
    # Context wires the placeholder show + a manage_url link back to settings.
    assert call["context"]["heading"].startswith("This is a test")
    assert call["context"]["shows"]
    assert "/settings/notifications" in call["context"]["manage_url"]


def test_send_test_email_to_self_refuses_when_address_has_bounced(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: Any,
) -> None:
    """Bounced/complained address: refuse with reason='bounced'."""
    client, user, headers = authed_client
    user.email_bounced_at = datetime.now(UTC)
    calls = _stub_compose_email(monkeypatch)

    response = client.post("/api/v1/me/email/test", headers=headers())

    body = response.get_json()
    assert body["data"]["sent"] is False
    assert body["data"]["reason"] == "bounced"
    # Recipient mask still computed so the UI can echo the address back.
    assert body["data"]["to"] == "p***@example.test"
    assert calls == []


def test_send_test_email_to_self_handles_no_email_on_account(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: Any,
) -> None:
    """Empty-email branch: refuse with reason='no_email'."""
    client, user, headers = authed_client
    user.email = ""
    calls = _stub_compose_email(monkeypatch)

    response = client.post("/api/v1/me/email/test", headers=headers())

    body = response.get_json()
    assert body["data"] == {"sent": False, "to": "", "reason": "no_email"}
    assert calls == []


def test_send_test_email_to_self_handles_resend_failure(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: Any,
) -> None:
    """Resend rejection becomes reason='delivery_failed', never a 500."""
    client, _user, headers = authed_client

    def fake_compose(**_: Any) -> None:
        raise AppError(
            code=EMAIL_DELIVERY_FAILED,
            message="resend down",
            status_code=502,
        )

    monkeypatch.setattr(
        "backend.api.v1.email_test.email_service.compose_email",
        fake_compose,
    )

    response = client.post("/api/v1/me/email/test", headers=headers())

    assert response.status_code == 200
    body = response.get_json()
    assert body["data"]["sent"] is False
    assert body["data"]["reason"] == "delivery_failed"

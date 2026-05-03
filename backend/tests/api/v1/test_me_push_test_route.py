"""Tests for the user-facing ``POST /api/v1/me/push/test`` endpoint.

The endpoint surfaces the same SendResult the dispatcher returns, so
the frontend can render specific guidance ("no devices subscribed,"
"push isn't configured here," etc.). These tests pin the auth gate,
the rate-limit subject, and the four interesting result shapes.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from flask.testing import FlaskClient

from backend.data.models.users import User
from backend.services import push as push_service


def _send_to_user_stub(monkeypatch: Any, result: push_service.SendResult) -> list[Any]:
    """Replace push_service.send_to_user with a recorder.

    Returns the recorder list so the test can assert call args.
    """
    calls: list[Any] = []

    def fake_send(
        session: Any, user_id: Any, payload: Any, **_: Any
    ) -> push_service.SendResult:
        calls.append({"user_id": user_id, "payload": payload})
        return result

    monkeypatch.setattr(
        "backend.api.v1.push.push_service.send_to_user",
        fake_send,
    )
    return calls


def test_send_test_push_to_self_requires_auth(client: FlaskClient) -> None:
    """No bearer token means a 401, not a 500."""
    response = client.post("/api/v1/me/push/test")
    assert response.status_code == 401


def test_send_test_push_to_self_returns_sent_count_for_caller(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: Any,
) -> None:
    """Happy path: dispatcher reports one device, response mirrors it."""
    client, user, headers = authed_client
    calls = _send_to_user_stub(
        monkeypatch,
        push_service.SendResult(
            attempted=1, succeeded=1, disabled=0, skipped_no_vapid=False
        ),
    )

    response = client.post("/api/v1/me/push/test", headers=headers())

    assert response.status_code == 200
    assert response.get_json() == {
        "data": {
            "attempted": 1,
            "succeeded": 1,
            "disabled": 0,
            "skipped_no_vapid": False,
        }
    }
    # The endpoint forwards the *current* user's id, never trusts a
    # body-supplied one.
    assert len(calls) == 1
    assert calls[0]["user_id"] == user.id


def test_send_test_push_to_self_surfaces_skipped_no_vapid(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: Any,
) -> None:
    """When VAPID is unconfigured the result body says so verbatim."""
    client, _, headers = authed_client
    _send_to_user_stub(
        monkeypatch,
        push_service.SendResult(
            attempted=0, succeeded=0, disabled=0, skipped_no_vapid=True
        ),
    )

    response = client.post("/api/v1/me/push/test", headers=headers())

    body = response.get_json()
    assert body["data"]["attempted"] == 0
    assert body["data"]["skipped_no_vapid"] is True


def test_send_test_push_to_self_surfaces_zero_subscriptions(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: Any,
) -> None:
    """No subscriptions: ``attempted == 0`` but ``skipped_no_vapid == False``."""
    client, _, headers = authed_client
    _send_to_user_stub(
        monkeypatch,
        push_service.SendResult(
            attempted=0, succeeded=0, disabled=0, skipped_no_vapid=False
        ),
    )

    response = client.post("/api/v1/me/push/test", headers=headers())

    body = response.get_json()
    assert body["data"] == {
        "attempted": 0,
        "succeeded": 0,
        "disabled": 0,
        "skipped_no_vapid": False,
    }


def test_send_test_push_to_self_surfaces_disabled_count(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: Any,
) -> None:
    """Permanent push-service failure must show up so the UI can prompt re-enable."""
    client, _, headers = authed_client
    _send_to_user_stub(
        monkeypatch,
        push_service.SendResult(
            attempted=1, succeeded=0, disabled=1, skipped_no_vapid=False
        ),
    )

    response = client.post("/api/v1/me/push/test", headers=headers())

    body = response.get_json()
    assert body["data"]["disabled"] == 1
    assert body["data"]["succeeded"] == 0

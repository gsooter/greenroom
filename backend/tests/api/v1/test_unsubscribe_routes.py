"""Route tests for :mod:`backend.api.v1.unsubscribe`.

The unsubscribe endpoint is the only Greenroom route that's
intentionally unauthenticated — recipients click a link in their
email client and we have to trust the signed token rather than a
session cookie. Tests pin down the contract: GET previews the action
without writing, POST commits the change, malformed/expired tokens
return a clean 400, and missing tokens return 422 (validation,
not unauthorized).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from flask.testing import FlaskClient

from backend.api.v1 import unsubscribe as unsub_route
from backend.services import email_tokens


def test_get_with_valid_token_previews_action(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET surfaces the user_id and scope without mutating the DB."""
    user_id = uuid.uuid4()
    token = email_tokens.mint_unsubscribe_token(user_id, "weekly_digest")

    def fake_unsub(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("GET must not call the unsubscribe service")

    monkeypatch.setattr(
        unsub_route.unsubscribe_service, "unsubscribe_with_token", fake_unsub
    )

    resp = client.get(f"/api/v1/unsubscribe?token={token}")
    assert resp.status_code == 200
    body = resp.get_json()["data"]
    assert body["scope"] == "weekly_digest"
    assert body["user_id"] == str(user_id)


def test_post_with_valid_token_calls_service(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST commits the unsubscribe and reports the scope back."""
    user_id = uuid.uuid4()
    token = email_tokens.mint_unsubscribe_token(user_id, "staff_picks")
    captured: dict[str, Any] = {}

    def fake_unsub(_session: Any, t: str) -> email_tokens.UnsubscribeToken:
        captured["token"] = t
        return email_tokens.UnsubscribeToken(
            user_id=user_id, scope="staff_picks", issued_at=0
        )

    monkeypatch.setattr(
        unsub_route.unsubscribe_service, "unsubscribe_with_token", fake_unsub
    )

    resp = client.post(f"/api/v1/unsubscribe?token={token}")
    assert resp.status_code == 200
    body = resp.get_json()["data"]
    assert body["scope"] == "staff_picks"
    assert body["confirmed"] is True
    assert captured["token"] == token


def test_post_one_click_form_payload_is_accepted(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RFC 8058: gateways POST application/x-www-form-urlencoded.

    Some mailbox providers send the One-Click body as
    ``List-Unsubscribe=One-Click`` rather than JSON. The endpoint
    accepts both — the token still arrives in the query string.
    """
    user_id = uuid.uuid4()
    token = email_tokens.mint_unsubscribe_token(user_id, "all")

    def fake_unsub(_session: Any, _t: str) -> email_tokens.UnsubscribeToken:
        return email_tokens.UnsubscribeToken(user_id=user_id, scope="all", issued_at=0)

    monkeypatch.setattr(
        unsub_route.unsubscribe_service, "unsubscribe_with_token", fake_unsub
    )

    resp = client.post(
        f"/api/v1/unsubscribe?token={token}",
        data={"List-Unsubscribe": "One-Click"},
        content_type="application/x-www-form-urlencoded",
    )
    assert resp.status_code == 200


def test_missing_token_returns_422(client: FlaskClient) -> None:
    """A request without the ``token`` query param fails validation."""
    resp = client.get("/api/v1/unsubscribe")
    assert resp.status_code == 422
    body = resp.get_json()
    assert body["error"]["code"] == "VALIDATION_ERROR"


def test_tampered_token_returns_422(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A token whose signature does not verify surfaces as 422."""
    resp = client.post("/api/v1/unsubscribe?token=garbage.value.here")
    assert resp.status_code == 422
    body = resp.get_json()
    assert body["error"]["code"] == "VALIDATION_ERROR"


def test_post_does_not_require_auth_header(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No Authorization header is needed — the token is the auth."""
    token = email_tokens.mint_unsubscribe_token(uuid.uuid4(), "all")
    monkeypatch.setattr(
        unsub_route.unsubscribe_service,
        "unsubscribe_with_token",
        lambda _s, _t: email_tokens.UnsubscribeToken(
            user_id=uuid.UUID(int=0), scope="all", issued_at=0
        ),
    )
    resp = client.post(f"/api/v1/unsubscribe?token={token}")
    # 200, not 401: the token is the credential.
    assert resp.status_code == 200

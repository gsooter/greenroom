"""Route tests for :mod:`backend.api.v1.auth_identity`.

These endpoints are server-side proxies that forward identity
ceremonies (magic link, Google, Apple, passkey) to Knuckles so the
app-client secret never ships to the browser. The tests mock
``knuckles_client.post`` directly rather than standing up an HTTP
stub — the proxy's job is plumbing, not network behavior.

Covered per endpoint:
- forwarding of the expected payload (including the server-built
  ``redirect_url``),
- passthrough of Knuckles' ``data`` envelope for the challenge halves,
- lazy user provisioning on the session-completing halves,
- validation errors for missing body fields,
- ``@require_auth`` enforcement on the passkey register proxies.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from flask.testing import FlaskClient

from backend.api.v1 import auth_identity as route
from backend.data.models.users import User
from backend.tests.conftest import mint_knuckles_token

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stub_session_exchange(
    monkeypatch: pytest.MonkeyPatch,
    *,
    user: User,
    access_token: str,
) -> MagicMock:
    """Replace ``_exchange_session`` so sign-in proxies don't need JWKS.

    Args:
        monkeypatch: pytest's monkeypatch fixture.
        user: User whose serialized form is returned in the envelope.
        access_token: Token to echo back in the envelope.

    Returns:
        The mock so callers can assert it was invoked.
    """
    exchange = MagicMock(
        return_value={
            "token": access_token,
            "user": {"id": str(user.id), "email": user.email},
        }
    )
    monkeypatch.setattr(route, "_exchange_session", exchange)
    return exchange


# ---------------------------------------------------------------------------
# Magic link
# ---------------------------------------------------------------------------


def test_magic_link_request_forwards_email_and_redirect(
    client: FlaskClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Greenroom fills in the frontend ``redirect_url`` from settings."""
    captured: dict[str, Any] = {}

    def fake_post(
        path: str, *, json: dict[str, Any] | None = None, **_: Any
    ) -> dict[str, Any]:
        """Capture the Knuckles call args for assertion.

        Args:
            path: The Knuckles path being POSTed.
            json: The body, if any.

        Returns:
            An empty Knuckles response body.
        """
        captured["path"] = path
        captured["json"] = json
        return {}

    monkeypatch.setattr(route, "knuckles_post", fake_post)

    resp = client.post(
        "/api/v1/auth/magic-link/request",
        json={"email": "pat@example.test"},
    )
    assert resp.status_code == 200
    assert resp.get_json()["data"] == {"email_sent": True}
    assert captured["path"] == "/v1/auth/magic-link/start"
    assert captured["json"]["email"] == "pat@example.test"
    assert captured["json"]["redirect_url"].endswith("/auth/verify")


def test_magic_link_request_rejects_missing_email(client: FlaskClient) -> None:
    """No email field → 422 ValidationError without contacting Knuckles."""
    resp = client.post("/api/v1/auth/magic-link/request", json={})
    assert resp.status_code == 422


def test_magic_link_verify_returns_token_and_user(
    client: FlaskClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path returns the exchange envelope verbatim."""
    user = User(id=uuid.uuid4(), email="pat@example.test", is_active=True)
    exchange = _stub_session_exchange(monkeypatch, user=user, access_token="a.b.c")
    monkeypatch.setattr(
        route,
        "knuckles_post",
        lambda *_a, **_k: {"data": {"access_token": "a.b.c"}},
    )

    resp = client.post("/api/v1/auth/magic-link/verify", json={"token": "link-tok"})
    assert resp.status_code == 200
    body = resp.get_json()["data"]
    assert body["token"] == "a.b.c"
    assert body["user"]["id"] == str(user.id)
    exchange.assert_called_once()


def test_magic_link_verify_rejects_missing_token(client: FlaskClient) -> None:
    """No token → 422 without contacting Knuckles."""
    resp = client.post("/api/v1/auth/magic-link/verify", json={})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Google
# ---------------------------------------------------------------------------


def test_google_start_forwards_redirect_and_returns_data(
    client: FlaskClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Start passes the callback URL and returns Knuckles' data block."""
    captured: dict[str, Any] = {}

    def fake_post(
        path: str, *, json: dict[str, Any] | None = None, **_: Any
    ) -> dict[str, Any]:
        """Record the Knuckles call and return a canned challenge.

        Args:
            path: Knuckles path being called.
            json: The forwarded body.

        Returns:
            A stand-in Knuckles response body.
        """
        captured["path"] = path
        captured["json"] = json
        return {
            "data": {"authorize_url": "https://accounts.google.com/x", "state": "s"}
        }

    monkeypatch.setattr(route, "knuckles_post", fake_post)

    resp = client.get("/api/v1/auth/google/start")
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["authorize_url"].startswith("https://accounts.google.com/")
    assert data["state"] == "s"
    assert captured["json"]["redirect_url"].endswith("/auth/google/callback")


def test_google_complete_exchanges_and_returns_token(
    client: FlaskClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Complete round-trips code/state and returns a session envelope."""
    user = User(id=uuid.uuid4(), email="pat@example.test", is_active=True)
    _stub_session_exchange(monkeypatch, user=user, access_token="g.t")
    monkeypatch.setattr(
        route,
        "knuckles_post",
        lambda *_a, **_k: {"data": {"access_token": "g.t"}},
    )

    resp = client.post("/api/v1/auth/google/complete", json={"code": "c", "state": "s"})
    assert resp.status_code == 200
    assert resp.get_json()["data"]["token"] == "g.t"


def test_google_complete_rejects_missing_fields(client: FlaskClient) -> None:
    """code and state are both required."""
    resp = client.post("/api/v1/auth/google/complete", json={"state": "s"})
    assert resp.status_code == 422
    resp = client.post("/api/v1/auth/google/complete", json={"code": "c"})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Apple
# ---------------------------------------------------------------------------


def test_apple_start_returns_data(
    client: FlaskClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Apple start proxies through to Knuckles and returns the challenge."""
    monkeypatch.setattr(
        route,
        "knuckles_post",
        lambda *_a, **_k: {
            "data": {"authorize_url": "https://appleid.apple.com/x", "state": "s"}
        },
    )
    resp = client.get("/api/v1/auth/apple/start")
    assert resp.status_code == 200
    assert resp.get_json()["data"]["state"] == "s"


def test_apple_complete_forwards_user_payload(
    client: FlaskClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First-login ``user`` blob is forwarded verbatim."""
    captured: dict[str, Any] = {}

    def fake_post(
        path: str, *, json: dict[str, Any] | None = None, **_: Any
    ) -> dict[str, Any]:
        """Record args and return a stub session response.

        Args:
            path: Knuckles path.
            json: Forwarded body.

        Returns:
            Canned Knuckles response.
        """
        captured["json"] = json
        return {"data": {"access_token": "a.t"}}

    monkeypatch.setattr(route, "knuckles_post", fake_post)
    user = User(id=uuid.uuid4(), email="pat@example.test", is_active=True)
    _stub_session_exchange(monkeypatch, user=user, access_token="a.t")

    user_blob = {"name": {"firstName": "Pat", "lastName": "Q"}}
    resp = client.post(
        "/api/v1/auth/apple/complete",
        json={"code": "c", "state": "s", "user": user_blob},
    )
    assert resp.status_code == 200
    assert captured["json"]["user"] == user_blob


def test_apple_complete_rejects_non_object_user(client: FlaskClient) -> None:
    """A non-null ``user`` field that isn't an object is a validation error."""
    resp = client.post(
        "/api/v1/auth/apple/complete",
        json={"code": "c", "state": "s", "user": "bad"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Passkey — registration (require_auth)
# ---------------------------------------------------------------------------


def test_passkey_register_start_requires_auth(client: FlaskClient) -> None:
    """No bearer → 401 before any Knuckles call."""
    resp = client.post("/api/v1/auth/passkey/register/start")
    assert resp.status_code == 401


def test_passkey_register_start_forwards_bearer(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The caller's token is forwarded to Knuckles as a bearer."""
    client, _user, headers = authed_client
    captured: dict[str, Any] = {}

    def fake_post(
        path: str,
        *,
        json: dict[str, Any] | None = None,
        bearer_token: str | None = None,
    ) -> dict[str, Any]:
        """Capture args for assertion.

        Args:
            path: Knuckles path.
            json: Forwarded body.
            bearer_token: Forwarded access token.

        Returns:
            Canned challenge payload.
        """
        captured["path"] = path
        captured["bearer"] = bearer_token
        return {"data": {"options": {"challenge": "x"}, "state": "s"}}

    monkeypatch.setattr(route, "knuckles_post", fake_post)

    resp = client.post(
        "/api/v1/auth/passkey/register/start",
        headers=headers(),
    )
    assert resp.status_code == 200
    assert captured["path"] == "/v1/auth/passkey/register/begin"
    assert captured["bearer"] == headers()["Authorization"].split(" ", 1)[1]


def test_passkey_register_complete_returns_registered_flag(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Success collapses Knuckles' credential_id into ``{registered: True}``."""
    client, _user, headers = authed_client
    monkeypatch.setattr(
        route,
        "knuckles_post",
        lambda *_a, **_k: {"data": {"credential_id": str(uuid.uuid4())}},
    )

    resp = client.post(
        "/api/v1/auth/passkey/register/complete",
        json={"credential": {"id": "x"}, "state": "s", "name": "Laptop"},
        headers=headers(),
    )
    assert resp.status_code == 200
    assert resp.get_json()["data"] == {"registered": True}


def test_passkey_register_complete_rejects_missing_fields(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
) -> None:
    """credential and state are required."""
    client, _user, headers = authed_client
    resp = client.post(
        "/api/v1/auth/passkey/register/complete",
        json={"state": "s"},
        headers=headers(),
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Passkey — sign-in (anonymous)
# ---------------------------------------------------------------------------


def test_passkey_authenticate_start_is_public(
    client: FlaskClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sign-in begin does not require a bearer token."""
    monkeypatch.setattr(
        route,
        "knuckles_post",
        lambda *_a, **_k: {"data": {"options": {"challenge": "x"}, "state": "s"}},
    )
    resp = client.post("/api/v1/auth/passkey/authenticate/start")
    assert resp.status_code == 200
    assert resp.get_json()["data"]["state"] == "s"


def test_passkey_authenticate_complete_returns_session(
    client: FlaskClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Completing the assertion returns the normalized session envelope."""
    user = User(id=uuid.uuid4(), email="pat@example.test", is_active=True)
    _stub_session_exchange(monkeypatch, user=user, access_token="p.t")
    monkeypatch.setattr(
        route,
        "knuckles_post",
        lambda *_a, **_k: {"data": {"access_token": "p.t"}},
    )

    resp = client.post(
        "/api/v1/auth/passkey/authenticate/complete",
        json={"credential": {"id": "x"}, "state": "s"},
    )
    assert resp.status_code == 200
    body = resp.get_json()["data"]
    assert body["token"] == "p.t"
    assert body["user"]["id"] == str(user.id)


# ---------------------------------------------------------------------------
# _exchange_session — branches
# ---------------------------------------------------------------------------


def test_exchange_session_auto_provisions_missing_user(
    monkeypatch: pytest.MonkeyPatch,
    knuckles_test_key: rsa.RSAPrivateKey,
    stub_knuckles_jwks: str,
) -> None:
    """An unknown ``sub`` triggers lazy user creation from claims."""
    user_id = uuid.uuid4()
    created = User(id=user_id, email="new@example.test", is_active=True)
    create = MagicMock(return_value=created)
    monkeypatch.setattr(route, "get_db", lambda: MagicMock())
    monkeypatch.setattr(route.users_repo, "get_user_by_id", lambda _s, _uid: None)
    monkeypatch.setattr(route.users_repo, "create_user", create)
    monkeypatch.setattr(route.users_repo, "update_last_login", MagicMock())
    monkeypatch.setattr(
        route.users_service, "serialize_user", lambda u: {"id": str(u.id)}
    )

    token = mint_knuckles_token(
        signing_key=knuckles_test_key,
        kid=stub_knuckles_jwks,
        user_id=user_id,
        email="new@example.test",
    )
    result = route._exchange_session({"data": {"access_token": token}})
    assert result["token"] == token
    assert result["user"]["id"] == str(user_id)
    create.assert_called_once()


def test_exchange_session_rejects_deactivated_user(
    monkeypatch: pytest.MonkeyPatch,
    knuckles_test_key: rsa.RSAPrivateKey,
    stub_knuckles_jwks: str,
) -> None:
    """A token for a deactivated Greenroom user is refused."""
    user_id = uuid.uuid4()
    deactivated = User(id=user_id, email="p@example.test", is_active=False)
    monkeypatch.setattr(route, "get_db", lambda: MagicMock())
    monkeypatch.setattr(
        route.users_repo, "get_user_by_id", lambda _s, _uid: deactivated
    )

    token = mint_knuckles_token(
        signing_key=knuckles_test_key,
        kid=stub_knuckles_jwks,
        user_id=user_id,
        email="p@example.test",
    )
    from backend.core.exceptions import UnauthorizedError

    with pytest.raises(UnauthorizedError):
        route._exchange_session({"data": {"access_token": token}})


def test_exchange_session_rejects_missing_access_token() -> None:
    """Knuckles returning no token bubbles up as a 502."""
    from backend.core.exceptions import AppError

    with pytest.raises(AppError) as excinfo:
        route._exchange_session({"data": {}})
    assert excinfo.value.status_code == 502

"""Route tests for :mod:`backend.api.v1.auth_tidal`.

Tidal is a *connect* flow — both endpoints sit behind ``@require_auth``
and the caller's existing Knuckles bearer is the session credential.
Covers the same invariants as the Spotify route tests:

- both endpoints reject unauthenticated calls,
- state token round-trips and tamper/purpose checks,
- the happy path links a MusicServiceConnection to the caller without
  issuing a Greenroom JWT,
- inline top-artists sync failure is swallowed,
- the linker rejects a Tidal profile already bound to another user.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import jwt
import pytest
from flask.testing import FlaskClient

from backend.api.v1 import auth_tidal as auth_route
from backend.core.config import get_settings
from backend.core.exceptions import TIDAL_AUTH_FAILED, AppError
from backend.data.models.users import User
from backend.services.tidal import TidalProfile, TidalTokens


def _valid_state() -> str:
    """Mint a valid state token via the module's own helper.

    Returns:
        An encoded Tidal OAuth state JWT valid for the default TTL.
    """
    return auth_route._issue_state_token(verifier="test-verifier")


# ---------------------------------------------------------------------------
# /auth/tidal/start
# ---------------------------------------------------------------------------


def test_tidal_start_rejects_unauthenticated(client: FlaskClient) -> None:
    """No Authorization header → 401 before any Tidal call."""
    resp = client.get("/api/v1/auth/tidal/start")
    assert resp.status_code == 401


def test_tidal_start_returns_authorize_url_and_state(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """State round-trips through the signed JWT helper."""
    client, _user, headers = authed_client
    monkeypatch.setattr(
        auth_route.tidal_service,
        "build_authorize_url",
        lambda state, code_challenge: (
            f"https://login.tidal.com/authorize?state={state}"
            f"&code_challenge={code_challenge}"
        ),
    )
    resp = client.get("/api/v1/auth/tidal/start", headers=headers())
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["authorize_url"].startswith("https://login.tidal.com/")
    assert "code_challenge=" in data["authorize_url"]
    assert "state" in data
    claims = jwt.decode(
        data["state"],
        get_settings().jwt_secret_key,
        algorithms=["HS256"],
    )
    assert claims["purpose"] == "tidal_oauth_state"
    assert isinstance(claims.get("pkce"), str) and claims["pkce"]


# ---------------------------------------------------------------------------
# /auth/tidal/complete
# ---------------------------------------------------------------------------


def test_complete_rejects_unauthenticated(client: FlaskClient) -> None:
    """An unauthenticated POST is rejected before any validation runs."""
    resp = client.post(
        "/api/v1/auth/tidal/complete",
        json={"code": "abc", "state": _valid_state()},
    )
    assert resp.status_code == 401


def test_complete_rejects_non_json_body(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
) -> None:
    """An empty POST body fails validation before any service runs."""
    client, _user, headers = authed_client
    resp = client.post(
        "/api/v1/auth/tidal/complete",
        data="",
        content_type="text/plain",
        headers=headers(),
    )
    assert resp.status_code == 422


def test_complete_rejects_missing_code(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
) -> None:
    client, _user, headers = authed_client
    resp = client.post(
        "/api/v1/auth/tidal/complete",
        json={"state": _valid_state()},
        headers=headers(),
    )
    assert resp.status_code == 422


def test_complete_rejects_missing_state(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
) -> None:
    client, _user, headers = authed_client
    resp = client.post(
        "/api/v1/auth/tidal/complete",
        json={"code": "abc"},
        headers=headers(),
    )
    assert resp.status_code == 422


def test_complete_rejects_tampered_state(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
) -> None:
    """A state token signed with the wrong secret → TIDAL_AUTH_FAILED."""
    client, _user, headers = authed_client
    bad_state = jwt.encode(
        {"purpose": "tidal_oauth_state"}, "wrong-secret", algorithm="HS256"
    )
    resp = client.post(
        "/api/v1/auth/tidal/complete",
        json={"code": "abc", "state": bad_state},
        headers=headers(),
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == TIDAL_AUTH_FAILED


def test_complete_rejects_wrong_purpose_state(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
) -> None:
    """A well-signed token with the wrong purpose is also rejected."""
    client, _user, headers = authed_client
    now = datetime.now(UTC)
    wrong_state = jwt.encode(
        {
            "purpose": "something_else",
            "exp": int((now + timedelta(minutes=5)).timestamp()),
        },
        get_settings().jwt_secret_key,
        algorithm="HS256",
    )
    resp = client.post(
        "/api/v1/auth/tidal/complete",
        json={"code": "abc", "state": wrong_state},
        headers=headers(),
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == TIDAL_AUTH_FAILED


def test_complete_happy_path_links_connection_without_issuing_jwt(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path links the connection, runs sync, and returns no token."""
    client, user, headers = authed_client
    tokens = TidalTokens(
        access_token="a",
        refresh_token="r",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        scope="",
        user_id="tidal-1",
    )
    profile = TidalProfile(
        id="tidal-1",
        email="p@example.test",
        display_name="Pat",
        avatar_url=None,
    )

    monkeypatch.setattr(
        auth_route.tidal_service,
        "exchange_code",
        lambda _c, code_verifier: tokens,
    )
    monkeypatch.setattr(auth_route.tidal_service, "get_profile", lambda _t, _u: profile)
    link_mock = MagicMock()
    monkeypatch.setattr(auth_route, "_link_tidal_connection", link_mock)
    sync_mock = MagicMock(return_value=3)
    monkeypatch.setattr(auth_route.tidal_service, "sync_top_artists", sync_mock)
    monkeypatch.setattr(
        auth_route.users_service,
        "serialize_user",
        lambda u: {"id": str(u.id)},
    )

    resp = client.post(
        "/api/v1/auth/tidal/complete",
        json={"code": "abc", "state": _valid_state()},
        headers=headers(),
    )
    assert resp.status_code == 200
    body = resp.get_json()["data"]
    assert body["user"]["id"] == str(user.id)
    assert "token" not in body
    link_mock.assert_called_once()
    sync_mock.assert_called_once()


def test_complete_swallows_sync_failure(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inline top-artists sync failure must not block the connect flow."""
    client, _user, headers = authed_client
    tokens = TidalTokens(
        access_token="a",
        refresh_token="r",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        scope="",
        user_id="t1",
    )
    profile = TidalProfile(
        id="t1", email="p@example.test", display_name="Pat", avatar_url=None
    )

    monkeypatch.setattr(
        auth_route.tidal_service,
        "exchange_code",
        lambda _c, code_verifier: tokens,
    )
    monkeypatch.setattr(auth_route.tidal_service, "get_profile", lambda _t, _u: profile)
    monkeypatch.setattr(auth_route, "_link_tidal_connection", MagicMock())

    def boom(*_a: Any, **_k: Any) -> None:
        raise AppError(code=TIDAL_AUTH_FAILED, message="down", status_code=502)

    monkeypatch.setattr(auth_route.tidal_service, "sync_top_artists", boom)
    monkeypatch.setattr(
        auth_route.users_service, "serialize_user", lambda u: {"id": str(u.id)}
    )

    resp = client.post(
        "/api/v1/auth/tidal/complete",
        json={"code": "abc", "state": _valid_state()},
        headers=headers(),
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# _link_tidal_connection branches
# ---------------------------------------------------------------------------


def _tokens() -> TidalTokens:
    """Build a minimal TidalTokens payload for link-flow tests."""
    return TidalTokens(
        access_token="a",
        refresh_token="r",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        scope="",
        user_id="tidal-1",
    )


def _profile() -> TidalProfile:
    """Build a minimal TidalProfile for link-flow tests."""
    return TidalProfile(
        id="tidal-1", email="p@example.test", display_name="Pat", avatar_url=None
    )


def test_link_updates_tokens_when_connection_belongs_to_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Existing connection on the same user → refresh tokens in place."""
    user = User(id=uuid.uuid4(), email="p@example.test", is_active=True)
    connection = MagicMock(user_id=user.id)

    monkeypatch.setattr(
        auth_route.users_repo, "get_music_connection", lambda *_a, **_k: connection
    )
    update_tokens = MagicMock()
    monkeypatch.setattr(
        auth_route.users_repo, "update_music_connection_tokens", update_tokens
    )
    create_conn = MagicMock()
    monkeypatch.setattr(auth_route.users_repo, "create_music_connection", create_conn)
    monkeypatch.setattr(auth_route.users_repo, "update_user", MagicMock())

    auth_route._link_tidal_connection(MagicMock(), user, _profile(), _tokens())

    update_tokens.assert_called_once()
    create_conn.assert_not_called()


def test_link_creates_connection_when_none_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No connection row → create one for the caller."""
    user = User(id=uuid.uuid4(), email="p@example.test", is_active=True)

    monkeypatch.setattr(
        auth_route.users_repo, "get_music_connection", lambda *_a, **_k: None
    )
    create_conn = MagicMock()
    monkeypatch.setattr(auth_route.users_repo, "create_music_connection", create_conn)
    monkeypatch.setattr(auth_route.users_repo, "update_user", MagicMock())

    auth_route._link_tidal_connection(MagicMock(), user, _profile(), _tokens())

    create_conn.assert_called_once()


def test_link_rejects_when_connection_belongs_to_different_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Existing connection on a different user → 409 TIDAL_AUTH_FAILED."""
    user = User(id=uuid.uuid4(), email="p@example.test", is_active=True)
    connection = MagicMock(user_id=uuid.uuid4())

    monkeypatch.setattr(
        auth_route.users_repo, "get_music_connection", lambda *_a, **_k: connection
    )

    with pytest.raises(AppError) as excinfo:
        auth_route._link_tidal_connection(MagicMock(), user, _profile(), _tokens())
    assert excinfo.value.code == TIDAL_AUTH_FAILED
    assert excinfo.value.status_code == 409

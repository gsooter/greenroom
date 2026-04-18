"""Route tests for :mod:`backend.api.v1.auth` (Spotify OAuth flow)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock

import jwt
import pytest
from flask.testing import FlaskClient

from backend.api.v1 import auth as auth_route
from backend.core.config import get_settings
from backend.core.exceptions import AppError, SPOTIFY_AUTH_FAILED
from backend.services.spotify import SpotifyProfile, SpotifyTokens


@dataclass
class _FakeUser:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    display_name: str = "Pat"
    avatar_url: str | None = None
    email: str = "p@example.test"


# ---------------------------------------------------------------------------
# /auth/spotify/start
# ---------------------------------------------------------------------------


def test_spotify_start_returns_authorize_url_and_state(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """State round-trips through the signed JWT helper."""
    monkeypatch.setattr(
        auth_route.spotify_service,
        "build_authorize_url",
        lambda state: f"https://accounts.spotify.com/authorize?state={state}",
    )
    resp = client.get("/api/v1/auth/spotify/start")
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["authorize_url"].startswith("https://accounts.spotify.com/")
    assert "state" in data
    # State must decode with the configured secret.
    claims = jwt.decode(
        data["state"],
        get_settings().jwt_secret_key,
        algorithms=["HS256"],
    )
    assert claims["purpose"] == "spotify_oauth_state"


# ---------------------------------------------------------------------------
# /auth/spotify/complete
# ---------------------------------------------------------------------------


def _valid_state() -> str:
    """Mint a valid state token via the module's own helper."""
    return auth_route._issue_state_token()


def test_complete_rejects_non_json_body(client: FlaskClient) -> None:
    """An empty POST body fails validation before any service runs."""
    resp = client.post(
        "/api/v1/auth/spotify/complete",
        data="",
        content_type="text/plain",
    )
    assert resp.status_code == 422


def test_complete_rejects_missing_code(client: FlaskClient) -> None:
    resp = client.post(
        "/api/v1/auth/spotify/complete",
        json={"state": _valid_state()},
    )
    assert resp.status_code == 422


def test_complete_rejects_missing_state(client: FlaskClient) -> None:
    resp = client.post(
        "/api/v1/auth/spotify/complete", json={"code": "abc"}
    )
    assert resp.status_code == 422


def test_complete_rejects_tampered_state(client: FlaskClient) -> None:
    """A state token signed with the wrong secret → SPOTIFY_AUTH_FAILED."""
    bad_state = jwt.encode(
        {"purpose": "spotify_oauth_state"}, "wrong-secret", algorithm="HS256"
    )
    resp = client.post(
        "/api/v1/auth/spotify/complete",
        json={"code": "abc", "state": bad_state},
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == SPOTIFY_AUTH_FAILED


def test_complete_rejects_wrong_purpose_state(client: FlaskClient) -> None:
    """A well-signed token with the wrong purpose is also rejected."""
    now = datetime.now(timezone.utc)
    wrong_state = jwt.encode(
        {
            "purpose": "something_else",
            "exp": int((now + timedelta(minutes=5)).timestamp()),
        },
        get_settings().jwt_secret_key,
        algorithm="HS256",
    )
    resp = client.post(
        "/api/v1/auth/spotify/complete",
        json={"code": "abc", "state": wrong_state},
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == SPOTIFY_AUTH_FAILED


def test_complete_happy_path(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Happy path returns a JWT + serialized user and runs inline sync."""
    tokens = SpotifyTokens(
        access_token="a",
        refresh_token="r",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        scope="",
    )
    profile = SpotifyProfile(
        id="spotify-1", email="p@example.test", display_name="Pat", avatar_url=None
    )
    user = _FakeUser()

    monkeypatch.setattr(
        auth_route.spotify_service, "exchange_code", lambda _c: tokens
    )
    monkeypatch.setattr(
        auth_route.spotify_service, "get_profile", lambda _t: profile
    )
    monkeypatch.setattr(
        auth_route, "_upsert_spotify_user", lambda _s, _p, _t: user
    )
    sync_mock = MagicMock(return_value=3)
    monkeypatch.setattr(
        auth_route.spotify_service, "sync_top_artists", sync_mock
    )
    monkeypatch.setattr(
        auth_route.users_service,
        "serialize_user",
        lambda u: {"id": str(u.id)},
    )

    resp = client.post(
        "/api/v1/auth/spotify/complete",
        json={"code": "abc", "state": _valid_state()},
    )
    assert resp.status_code == 200
    body = resp.get_json()["data"]
    assert body["user"]["id"] == str(user.id)
    assert isinstance(body["token"], str) and body["token"]
    sync_mock.assert_called_once()


def test_complete_swallows_sync_failure(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inline top-artists sync failure must not block login."""
    tokens = SpotifyTokens(
        access_token="a",
        refresh_token="r",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        scope="",
    )
    profile = SpotifyProfile(
        id="s1", email="p@example.test", display_name="Pat", avatar_url=None
    )
    user = _FakeUser()

    monkeypatch.setattr(
        auth_route.spotify_service, "exchange_code", lambda _c: tokens
    )
    monkeypatch.setattr(
        auth_route.spotify_service, "get_profile", lambda _t: profile
    )
    monkeypatch.setattr(
        auth_route, "_upsert_spotify_user", lambda _s, _p, _t: user
    )

    def boom(*_a: Any, **_k: Any) -> None:
        raise AppError(code=SPOTIFY_AUTH_FAILED, message="down", status_code=502)

    monkeypatch.setattr(auth_route.spotify_service, "sync_top_artists", boom)
    monkeypatch.setattr(
        auth_route.users_service, "serialize_user", lambda u: {"id": str(u.id)}
    )

    resp = client.post(
        "/api/v1/auth/spotify/complete",
        json={"code": "abc", "state": _valid_state()},
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# _upsert_spotify_user branches
# ---------------------------------------------------------------------------


def test_upsert_returns_existing_oauth_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Existing oauth row → update tokens and return the linked user."""
    tokens = SpotifyTokens(
        access_token="a",
        refresh_token="r",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        scope="",
    )
    profile = SpotifyProfile(
        id="s1", email="p@example.test", display_name=None, avatar_url=None
    )
    linked_user = _FakeUser()
    oauth = MagicMock(user=linked_user)

    monkeypatch.setattr(
        auth_route.users_repo,
        "get_oauth_provider",
        lambda *_a, **_k: oauth,
    )
    update_tokens = MagicMock()
    monkeypatch.setattr(
        auth_route.users_repo, "update_oauth_tokens", update_tokens
    )
    monkeypatch.setattr(auth_route.users_repo, "update_user", MagicMock())
    monkeypatch.setattr(auth_route.users_repo, "update_last_login", MagicMock())

    result = auth_route._upsert_spotify_user(MagicMock(), profile, tokens)

    assert result is linked_user
    update_tokens.assert_called_once()


def test_upsert_matches_by_email_and_creates_oauth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No oauth row but existing email → create oauth on the found user."""
    tokens = SpotifyTokens(
        access_token="a",
        refresh_token="r",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        scope="full",
    )
    profile = SpotifyProfile(
        id="s1", email="p@example.test", display_name="New", avatar_url=None
    )
    email_user = _FakeUser()

    monkeypatch.setattr(
        auth_route.users_repo, "get_oauth_provider", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        auth_route.users_repo,
        "get_user_by_email",
        lambda _s, _email: email_user,
    )
    monkeypatch.setattr(auth_route.users_repo, "update_user", MagicMock())
    create_oauth = MagicMock()
    monkeypatch.setattr(
        auth_route.users_repo, "create_oauth_provider", create_oauth
    )
    monkeypatch.setattr(auth_route.users_repo, "update_last_login", MagicMock())

    result = auth_route._upsert_spotify_user(MagicMock(), profile, tokens)

    assert result is email_user
    create_oauth.assert_called_once()


def test_upsert_creates_brand_new_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No oauth, no email match → create a User and an OAuth row."""
    tokens = SpotifyTokens(
        access_token="a",
        refresh_token="r",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        scope="",
    )
    profile = SpotifyProfile(
        id="s1", email="p@example.test", display_name="New", avatar_url=None
    )
    created = _FakeUser()

    monkeypatch.setattr(
        auth_route.users_repo, "get_oauth_provider", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        auth_route.users_repo, "get_user_by_email", lambda *_a, **_k: None
    )
    create_user = MagicMock(return_value=created)
    monkeypatch.setattr(auth_route.users_repo, "create_user", create_user)
    create_oauth = MagicMock()
    monkeypatch.setattr(
        auth_route.users_repo, "create_oauth_provider", create_oauth
    )
    monkeypatch.setattr(auth_route.users_repo, "update_last_login", MagicMock())

    result = auth_route._upsert_spotify_user(MagicMock(), profile, tokens)

    assert result is created
    create_user.assert_called_once()
    create_oauth.assert_called_once()

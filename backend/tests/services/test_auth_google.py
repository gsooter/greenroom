"""Unit tests for the Google OAuth sign-in flow.

The service talks to Google's OAuth token + userinfo endpoints via the
module-level ``httpx`` client; tests replace the client with a
MagicMock so nothing goes over the wire.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.core.exceptions import GOOGLE_AUTH_FAILED, AppError
from backend.data.models.users import OAuthProvider, User
from backend.services import auth as auth_service

# ---------------------------------------------------------------------------
# build_authorize_url
# ---------------------------------------------------------------------------


def test_google_build_authorize_url_includes_state_and_scopes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The consent URL includes the caller-supplied state and openid scope."""
    fake = MagicMock()
    fake.google_oauth_client_id = "client-id"
    fake.google_oauth_redirect_uri = "https://example.test/cb"
    monkeypatch.setattr(auth_service, "get_settings", lambda: fake)

    url = auth_service.google_build_authorize_url(state="s1")
    assert "client_id=client-id" in url
    assert "state=s1" in url
    assert "scope=" in url
    # The URL must contain the openid/email/profile scopes so the
    # downstream profile fetch has what it needs.
    assert "openid" in url
    assert "email" in url
    assert "response_type=code" in url


# ---------------------------------------------------------------------------
# google_complete — high-level happy path + failure branches
# ---------------------------------------------------------------------------


def _patch_http(
    monkeypatch: pytest.MonkeyPatch,
    *,
    token_json: dict[str, Any] | None = None,
    token_status: int = 200,
    userinfo_json: dict[str, Any] | None = None,
    userinfo_status: int = 200,
) -> None:
    """Install fake ``requests.post`` and ``requests.get`` on the auth module.

    Args:
        monkeypatch: pytest fixture for attribute rebinding.
        token_json: JSON body Google's token endpoint should return.
        token_status: HTTP status for the token exchange.
        userinfo_json: JSON body the userinfo endpoint should return.
        userinfo_status: HTTP status for the userinfo fetch.
    """

    def fake_post(url: str, **_: Any) -> Any:
        resp = MagicMock()
        resp.status_code = token_status
        resp.json.return_value = token_json or {}
        return resp

    def fake_get(url: str, **_: Any) -> Any:
        resp = MagicMock()
        resp.status_code = userinfo_status
        resp.json.return_value = userinfo_json or {}
        return resp

    monkeypatch.setattr(auth_service.requests, "post", fake_post)
    monkeypatch.setattr(auth_service.requests, "get", fake_get)


def test_google_complete_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """A valid code → upserts a user and returns a JWT."""
    _patch_http(
        monkeypatch,
        token_json={"access_token": "at", "refresh_token": "rt", "expires_in": 3600},
        userinfo_json={
            "sub": "google-123",
            "email": "pat@example.test",
            "email_verified": True,
            "name": "Pat",
            "picture": "https://img/pat.png",
        },
    )

    upsert = MagicMock(return_value=User(id=uuid.uuid4(), email="pat@example.test"))
    monkeypatch.setattr(auth_service, "_upsert_oauth_user", upsert)

    result = auth_service.google_complete(MagicMock(), code="abc")

    assert isinstance(result.jwt, str) and result.jwt
    assert upsert.call_args.kwargs["provider"] is OAuthProvider.GOOGLE
    assert upsert.call_args.kwargs["provider_user_id"] == "google-123"
    assert upsert.call_args.kwargs["email"] == "pat@example.test"


def test_google_complete_rejects_unverified_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Google can return ``email_verified=False`` for consumer accounts.

    We never mint a session for an unverified email since an attacker
    could register with someone else's address.
    """
    _patch_http(
        monkeypatch,
        token_json={"access_token": "at", "expires_in": 3600},
        userinfo_json={
            "sub": "g1",
            "email": "pat@example.test",
            "email_verified": False,
        },
    )

    with pytest.raises(AppError) as exc:
        auth_service.google_complete(MagicMock(), code="abc")
    assert exc.value.code == GOOGLE_AUTH_FAILED


def test_google_complete_token_endpoint_error_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-200 from Google's token endpoint raises GOOGLE_AUTH_FAILED."""
    _patch_http(monkeypatch, token_status=400, token_json={"error": "invalid_grant"})

    with pytest.raises(AppError) as exc:
        auth_service.google_complete(MagicMock(), code="bad")
    assert exc.value.code == GOOGLE_AUTH_FAILED


def test_google_complete_userinfo_error_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-200 from Google's userinfo endpoint raises GOOGLE_AUTH_FAILED."""
    _patch_http(
        monkeypatch,
        token_json={"access_token": "at", "expires_in": 3600},
        userinfo_status=401,
    )
    with pytest.raises(AppError) as exc:
        auth_service.google_complete(MagicMock(), code="abc")
    assert exc.value.code == GOOGLE_AUTH_FAILED


# ---------------------------------------------------------------------------
# _upsert_oauth_user — the provider-agnostic upsert used by Google, Apple,
# and (later) any other OAuth provider.
# ---------------------------------------------------------------------------


def test_upsert_oauth_user_matches_existing_oauth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An existing ``(provider, provider_user_id)`` → update tokens, reuse user."""
    existing_user = User(id=uuid.uuid4(), email="pat@example.test", is_active=True)
    oauth = MagicMock(user=existing_user)

    monkeypatch.setattr(
        auth_service.users_repo, "get_oauth_provider", lambda *_a, **_k: oauth
    )
    update_tokens = MagicMock()
    monkeypatch.setattr(auth_service.users_repo, "update_oauth_tokens", update_tokens)
    monkeypatch.setattr(auth_service.users_repo, "update_user", MagicMock())
    monkeypatch.setattr(auth_service.users_repo, "update_last_login", MagicMock())

    result = auth_service._upsert_oauth_user(
        MagicMock(),
        provider=OAuthProvider.GOOGLE,
        provider_user_id="g1",
        email="pat@example.test",
        display_name="Pat",
        avatar_url=None,
        access_token="at",
        refresh_token=None,
        token_expires_at=None,
    )

    assert result is existing_user
    update_tokens.assert_called_once()


def test_upsert_oauth_user_matches_email_creates_oauth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No oauth row but existing email → link provider to existing user."""
    found = User(id=uuid.uuid4(), email="pat@example.test", is_active=True)

    monkeypatch.setattr(
        auth_service.users_repo, "get_oauth_provider", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        auth_service.users_repo, "get_user_by_email", lambda *_a, **_k: found
    )
    monkeypatch.setattr(auth_service.users_repo, "update_user", MagicMock())
    monkeypatch.setattr(auth_service.users_repo, "update_last_login", MagicMock())
    create_oauth = MagicMock()
    monkeypatch.setattr(auth_service.users_repo, "create_oauth_provider", create_oauth)

    result = auth_service._upsert_oauth_user(
        MagicMock(),
        provider=OAuthProvider.GOOGLE,
        provider_user_id="g1",
        email="pat@example.test",
        display_name=None,
        avatar_url=None,
        access_token="at",
        refresh_token=None,
        token_expires_at=None,
    )

    assert result is found
    create_oauth.assert_called_once()


def test_upsert_oauth_user_creates_brand_new(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No oauth, no email match → create User + OAuth row."""
    created = User(id=uuid.uuid4(), email="new@example.test", is_active=True)
    monkeypatch.setattr(
        auth_service.users_repo, "get_oauth_provider", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        auth_service.users_repo, "get_user_by_email", lambda *_a, **_k: None
    )
    create_user = MagicMock(return_value=created)
    monkeypatch.setattr(auth_service.users_repo, "create_user", create_user)
    monkeypatch.setattr(auth_service.users_repo, "create_oauth_provider", MagicMock())
    monkeypatch.setattr(auth_service.users_repo, "update_last_login", MagicMock())

    result = auth_service._upsert_oauth_user(
        MagicMock(),
        provider=OAuthProvider.GOOGLE,
        provider_user_id="g1",
        email="new@example.test",
        display_name="New",
        avatar_url=None,
        access_token="at",
        refresh_token=None,
        token_expires_at=None,
    )

    assert result is created
    create_user.assert_called_once()


def test_upsert_oauth_user_rejects_deactivated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deactivated user must never get a JWT via any OAuth provider."""
    dead = User(id=uuid.uuid4(), email="dead@example.test", is_active=False)
    oauth = MagicMock(user=dead)
    monkeypatch.setattr(
        auth_service.users_repo, "get_oauth_provider", lambda *_a, **_k: oauth
    )
    monkeypatch.setattr(auth_service.users_repo, "update_oauth_tokens", MagicMock())

    with pytest.raises(AppError):
        auth_service._upsert_oauth_user(
            MagicMock(),
            provider=OAuthProvider.GOOGLE,
            provider_user_id="g1",
            email="dead@example.test",
            display_name=None,
            avatar_url=None,
            access_token="at",
            refresh_token=None,
            token_expires_at=None,
        )

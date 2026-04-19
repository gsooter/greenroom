"""Unit tests for the Sign-in-with-Apple flow.

The crypto-heavy pieces — client-secret JWT minting, Apple JWK fetch,
id-token verification — are patched so the orchestrator can be driven
without real Apple credentials or internet access.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.core.exceptions import APPLE_AUTH_FAILED, AppError
from backend.data.models.users import OAuthProvider, User
from backend.services import auth as auth_service

# ---------------------------------------------------------------------------
# build_authorize_url
# ---------------------------------------------------------------------------


def test_apple_build_authorize_url_includes_state_and_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Apple consent URL carries state and the form_post response mode."""
    fake = MagicMock()
    fake.apple_oauth_client_id = "com.greenroom.web"
    fake.apple_oauth_redirect_uri = "https://example.test/cb"
    monkeypatch.setattr(auth_service, "get_settings", lambda: fake)

    url = auth_service.apple_build_authorize_url(state="s1")
    assert "client_id=com.greenroom.web" in url
    assert "state=s1" in url
    assert "response_mode=form_post" in url
    assert "scope=" in url
    assert "name" in url
    assert "email" in url


# ---------------------------------------------------------------------------
# apple_complete
# ---------------------------------------------------------------------------


def _patch_crypto(
    monkeypatch: pytest.MonkeyPatch,
    *,
    profile: dict[str, Any] | None = None,
    raise_on_exchange: bool = False,
    raise_on_verify: bool = False,
) -> None:
    """Replace Apple's network + crypto helpers with in-memory fakes.

    Args:
        monkeypatch: pytest fixture.
        profile: What :func:`_apple_verify_id_token` should return.
        raise_on_exchange: If True, the token exchange helper raises.
        raise_on_verify: If True, the id-token verifier raises.
    """
    monkeypatch.setattr(
        auth_service,
        "_apple_mint_client_secret",
        lambda: "client-secret-jwt",
    )

    def fake_exchange(_code: str, _client_secret: str) -> dict[str, Any]:
        if raise_on_exchange:
            raise AppError(code=APPLE_AUTH_FAILED, message="down", status_code=400)
        return {"id_token": "idt", "refresh_token": "rt"}

    def fake_verify(_id_token: str) -> dict[str, Any]:
        if raise_on_verify:
            raise AppError(code=APPLE_AUTH_FAILED, message="down", status_code=400)
        return profile or {
            "sub": "apple-1",
            "email": "pat@example.test",
            "email_verified": "true",
            "is_private_email": "false",
        }

    monkeypatch.setattr(auth_service, "_apple_exchange_code", fake_exchange)
    monkeypatch.setattr(auth_service, "_apple_verify_id_token", fake_verify)


def test_apple_complete_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """A valid code → verified id_token → upsert + JWT."""
    _patch_crypto(monkeypatch)

    user = User(id=uuid.uuid4(), email="pat@example.test")
    upsert = MagicMock(return_value=user)
    monkeypatch.setattr(auth_service, "_upsert_oauth_user", upsert)

    result = auth_service.apple_complete(MagicMock(), code="abc", user_data=None)
    assert isinstance(result.jwt, str) and result.jwt
    assert upsert.call_args.kwargs["provider"] is OAuthProvider.APPLE
    assert upsert.call_args.kwargs["provider_user_id"] == "apple-1"


def test_apple_complete_uses_user_data_display_name_for_first_signin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Apple only sends ``user`` payload on the first sign-in.

    When present it carries the user's real display name since the id
    token itself doesn't. We pass it into the upsert so the account
    has a friendly name rather than showing the relay email.
    """
    _patch_crypto(monkeypatch)
    upsert = MagicMock(return_value=User(id=uuid.uuid4(), email="a@b.c"))
    monkeypatch.setattr(auth_service, "_upsert_oauth_user", upsert)

    user_data = {"name": {"firstName": "Pat", "lastName": "Doe"}}
    auth_service.apple_complete(MagicMock(), code="abc", user_data=user_data)

    assert upsert.call_args.kwargs["display_name"] == "Pat Doe"


def test_apple_complete_token_exchange_error_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Apple rejecting the code → APPLE_AUTH_FAILED."""
    _patch_crypto(monkeypatch, raise_on_exchange=True)
    with pytest.raises(AppError) as exc:
        auth_service.apple_complete(MagicMock(), code="bad", user_data=None)
    assert exc.value.code == APPLE_AUTH_FAILED


def test_apple_complete_verify_error_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """id_token verification failure → APPLE_AUTH_FAILED."""
    _patch_crypto(monkeypatch, raise_on_verify=True)
    with pytest.raises(AppError) as exc:
        auth_service.apple_complete(MagicMock(), code="abc", user_data=None)
    assert exc.value.code == APPLE_AUTH_FAILED


def test_apple_complete_allows_private_relay_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Apple private-relay addresses are valid login identities.

    ``is_private_email=true`` means Apple generated an address that
    forwards to the user's real one. We must accept it — many Apple
    users choose relay by default — and store it so the user gets the
    same account on every return visit.
    """
    _patch_crypto(
        monkeypatch,
        profile={
            "sub": "apple-2",
            "email": "abc@privaterelay.appleid.com",
            "email_verified": "true",
            "is_private_email": "true",
        },
    )
    upsert = MagicMock(
        return_value=User(id=uuid.uuid4(), email="abc@privaterelay.appleid.com")
    )
    monkeypatch.setattr(auth_service, "_upsert_oauth_user", upsert)

    result = auth_service.apple_complete(MagicMock(), code="abc", user_data=None)
    assert isinstance(result.jwt, str)
    assert upsert.call_args.kwargs["email"] == "abc@privaterelay.appleid.com"

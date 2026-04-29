"""Tests for :mod:`backend.core.knuckles`.

The module is a thin shim around the ``knuckles-client`` SDK — its job
is to keep one :class:`KnucklesClient` per process and translate the
SDK's :class:`KnucklesTokenError` into Greenroom's :class:`AppError`
envelope. These tests exercise that translation plus the singleton's
lifecycle.
"""

from __future__ import annotations

import uuid
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from knuckles_client import KnucklesClient
from knuckles_client.exceptions import (
    KnucklesAuthError,
    KnucklesTokenError,
)

from backend.core import knuckles as knuckles_module
from backend.core.exceptions import (
    INVALID_TOKEN,
    TOKEN_EXPIRED,
    AppError,
)
from backend.tests.conftest import (
    KNUCKLES_TEST_CLIENT_ID,
    KNUCKLES_TEST_URL,
    mint_knuckles_token,
)


def test_get_client_returns_singleton() -> None:
    """Two calls return the same instance — one client per process."""
    first = knuckles_module.get_client()
    second = knuckles_module.get_client()
    assert first is second
    assert isinstance(first, KnucklesClient)


def test_reset_client_drops_cached_instance() -> None:
    """``reset_client`` clears the cache so the next call rebuilds."""
    first = knuckles_module.get_client()
    knuckles_module.reset_client()
    second = knuckles_module.get_client()
    assert first is not second


def test_verify_knuckles_token_returns_claims_for_valid_token(
    knuckles_test_key: rsa.RSAPrivateKey,
    stub_knuckles_jwks: str,
) -> None:
    """A well-formed RS256 token decodes to its claims dict."""
    user_id = uuid.uuid4()
    token = mint_knuckles_token(
        signing_key=knuckles_test_key,
        kid=stub_knuckles_jwks,
        user_id=user_id,
        email="user@example.test",
    )
    claims = knuckles_module.verify_knuckles_token(token)
    assert claims["sub"] == str(user_id)
    assert claims["email"] == "user@example.test"
    assert claims["aud"] == KNUCKLES_TEST_CLIENT_ID
    assert claims["iss"] == KNUCKLES_TEST_URL


def test_verify_knuckles_token_maps_expired_to_token_expired(
    knuckles_test_key: rsa.RSAPrivateKey,
    stub_knuckles_jwks: str,
) -> None:
    """An expired ``exp`` surfaces as ``TOKEN_EXPIRED`` (401)."""
    token = mint_knuckles_token(
        signing_key=knuckles_test_key,
        kid=stub_knuckles_jwks,
        user_id=uuid.uuid4(),
        exp_offset=-30,
    )
    with pytest.raises(AppError) as info:
        knuckles_module.verify_knuckles_token(token)
    assert info.value.code == TOKEN_EXPIRED
    assert info.value.status_code == 401


def test_verify_knuckles_token_maps_other_failures_to_invalid_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-expiry token errors fold to ``INVALID_TOKEN`` (401)."""

    class _StubVerifier:
        """Replace ``KnucklesClient`` with a fixed ``verify_access_token`` raise."""

        def verify_access_token(self, _token: str) -> dict[str, Any]:
            """Always raise to simulate a non-expiry verify failure.

            Returns:
                Never — always raises.

            Raises:
                KnucklesTokenError: A signature failure with no ``__cause__``
                    of :class:`jwt.ExpiredSignatureError`.
            """
            raise KnucklesTokenError("bad signature")

    monkeypatch.setattr(knuckles_module, "get_client", lambda: _StubVerifier())
    with pytest.raises(AppError) as info:
        knuckles_module.verify_knuckles_token("any.token.value")
    assert info.value.code == INVALID_TOKEN
    assert info.value.status_code == 401


def test_verify_knuckles_token_rejects_token_signed_with_unknown_key(
    stub_knuckles_jwks: str,
) -> None:
    """A token signed by a different key surfaces as ``INVALID_TOKEN``."""
    rogue = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = mint_knuckles_token(
        signing_key=rogue,
        kid=stub_knuckles_jwks,
        user_id=uuid.uuid4(),
    )
    with pytest.raises(AppError) as info:
        knuckles_module.verify_knuckles_token(token)
    assert info.value.code == INVALID_TOKEN


def test_verify_knuckles_token_rejects_unsigned_token(
    stub_knuckles_jwks: str,
) -> None:
    """An ``alg=none`` token is rejected with ``INVALID_TOKEN``."""
    token = jwt.encode(
        {
            "iss": KNUCKLES_TEST_URL,
            "sub": str(uuid.uuid4()),
            "aud": KNUCKLES_TEST_CLIENT_ID,
        },
        key="",
        algorithm="none",
    )
    with pytest.raises(AppError) as info:
        knuckles_module.verify_knuckles_token(token)
    assert info.value.code == INVALID_TOKEN


def test_auth_error_to_app_error_preserves_code_and_status() -> None:
    """The SDK's code/message/status flow through verbatim."""
    err = KnucklesAuthError(
        code="REFRESH_TOKEN_REUSED",
        message="reuse-detected",
        status_code=401,
    )
    translated = knuckles_module.auth_error_to_app_error(err)
    assert isinstance(translated, AppError)
    assert translated.code == "REFRESH_TOKEN_REUSED"
    assert translated.message == "reuse-detected"
    assert translated.status_code == 401

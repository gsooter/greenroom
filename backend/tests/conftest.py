"""Shared test fixtures for the backend test suite.

Fixtures here are process-scoped and cheap — the Flask app factory
and stubbed settings in particular are reused across the unit-test
layer, which does not touch the database.

DB-backed integration tests (repositories, end-to-end API flows) live
under ``tests/data`` and ``tests/api`` and pull in their own session
fixtures once a ``greenroom_test`` Postgres database exists. Until
then, the unit tests here provide the coverage.
"""

from __future__ import annotations

import base64
import os
import time
import uuid as _uuid
from collections.abc import Iterator
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa


def _generate_test_es256_pem() -> str:
    """Return a throwaway ES256 private key for Apple Music dev-token tests.

    Generated once at module import so every test uses the same key,
    and so ``jwt.decode`` assertions can reuse the paired public key.

    Returns:
        The unencrypted PKCS8 PEM string.
    """
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return pem.decode("ascii")


APPLE_MUSIC_TEST_PEM = _generate_test_es256_pem()
APPLE_MAPKIT_TEST_PEM = _generate_test_es256_pem()

# ---------------------------------------------------------------------------
# Environment stubs for Pydantic Settings
# ---------------------------------------------------------------------------
#
# ``backend.core.config.Settings`` declares every env var as required. Unit
# tests should never depend on the developer's real ``.env``, so we inject
# safe placeholders before any backend module is imported.

KNUCKLES_TEST_URL = "https://knuckles.test"
KNUCKLES_TEST_CLIENT_ID = "greenroom-app-client"
KNUCKLES_TEST_KID = "test-kid"

_TEST_ENV = {
    "SPOTIFY_CLIENT_ID": "test-spotify-id",
    "SPOTIFY_CLIENT_SECRET": "test-spotify-secret",
    "SPOTIFY_REDIRECT_URI": "http://localhost/callback",
    "DATABASE_URL": "postgresql://localhost/greenroom_test",
    "REDIS_URL": "redis://localhost:6379/1",
    "JWT_SECRET_KEY": "test-jwt-secret-key-with-minimum-32-bytes-for-hs256",
    "JWT_EXPIRY_SECONDS": "3600",
    "RESEND_API_KEY": "x",
    "RESEND_FROM_EMAIL": "alerts@greenroom.test",
    "TICKETMASTER_API_KEY": "test-tm-key",
    "SEATGEEK_CLIENT_ID": "test-sg-id",
    "SEATGEEK_CLIENT_SECRET": "test-sg-secret",
    "ADMIN_SECRET_KEY": "test-admin-secret",
    "SLACK_WEBHOOK_OPS_URL": "x",
    "SLACK_WEBHOOK_DIGEST_URL": "",
    "SLACK_WEBHOOK_FEEDBACK_URL": "",
    "ALERT_EMAIL": "x@x.com",
    "POSTHOG_API_KEY": "x",
    "POSTHOG_HOST": "http://localhost:8000",
    "KNUCKLES_URL": KNUCKLES_TEST_URL,
    "KNUCKLES_CLIENT_ID": KNUCKLES_TEST_CLIENT_ID,
    "KNUCKLES_CLIENT_SECRET": "test-knuckles-secret",
    "TIDAL_CLIENT_ID": "test-tidal-id",
    "TIDAL_CLIENT_SECRET": "test-tidal-secret",
    "TIDAL_REDIRECT_URI": "http://localhost/callback/tidal",
    "APPLE_MUSIC_TEAM_ID": "TESTTEAM01",
    "APPLE_MUSIC_KEY_ID": "TESTKEY001",
    "APPLE_MUSIC_PRIVATE_KEY": APPLE_MUSIC_TEST_PEM,
    "APPLE_MUSIC_BUNDLE_ID": "media.greenroom.test.web",
    "APPLE_MAPKIT_TEAM_ID": "TESTTEAM01",
    "APPLE_MAPKIT_KEY_ID": "TESTMAP001",
    "APPLE_MAPKIT_PRIVATE_KEY": APPLE_MAPKIT_TEST_PEM,
}

for _key, _value in _TEST_ENV.items():
    os.environ.setdefault(_key, _value)


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    """Ensure each test sees freshly loaded settings.

    ``get_settings`` is not memoized today, but fixtures that monkey-patch
    env vars between tests would otherwise risk leakage. This hook is a
    cheap safety net.

    Yields:
        None; teardown is a no-op.
    """
    yield


@pytest.fixture(autouse=True)
def _disable_rate_limiter(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Default the rate limiter to fail-open in tests.

    The limiter is built to degrade gracefully when Redis is
    unreachable, but CI environments often have a real Redis pointing
    at a shared db and per-IP counters would leak between tests.
    Stubbing ``_get_redis`` to ``None`` makes every rate-limited
    route behave as if Redis were down (i.e. it calls through).

    Tests that exercise the limiter explicitly re-enable it by
    monkey-patching ``_get_redis`` back to a fake client.

    Yields:
        None; teardown restores by virtue of monkeypatch scope.
    """
    from backend.core import rate_limit as rate_limit_module

    monkeypatch.setattr(rate_limit_module, "_get_redis", lambda: None)
    yield


@pytest.fixture(autouse=True)
def _reset_knuckles_client() -> Iterator[None]:
    """Drop the SDK singleton between tests.

    Cases that override Knuckles env vars or stub the JWKS endpoint
    need a fresh :class:`KnucklesClient` so the cached transport and
    JWKS verifier rebuild against the new state.

    Yields:
        None; teardown drops the cache again so the next test starts
        clean.
    """
    from backend.core import knuckles as knuckles_module

    knuckles_module.reset_client()
    yield
    knuckles_module.reset_client()


# ---------------------------------------------------------------------------
# Knuckles RS256 token helpers
# ---------------------------------------------------------------------------
#
# After Decision 030, every authenticated request validates against the
# Knuckles JWKS. Tests need to mint valid RS256 tokens cheaply — generating
# a fresh 2048-bit RSA key per test is too slow, so we cache one for the
# whole session and stub the JWKS endpoint to publish its public half.


def _b64url_uint(value: int) -> str:
    """Encode a non-negative int as unpadded base64url (JWK spec).

    Args:
        value: Non-negative integer (typically an RSA modulus or
            exponent).

    Returns:
        Big-endian byte representation, base64url-encoded without
        padding, as required by RFC 7518.
    """
    length = (value.bit_length() + 7) // 8
    raw = value.to_bytes(length, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _build_jwks(public_key: rsa.RSAPublicKey, *, kid: str) -> dict[str, Any]:
    """Build a single-key JWKS document for the given public key.

    Args:
        public_key: The RSA public key to publish.
        kid: Key id to advertise.

    Returns:
        A JWKS dict of shape ``{"keys": [...]}``.
    """
    nums = public_key.public_numbers()
    return {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "alg": "RS256",
                "kid": kid,
                "n": _b64url_uint(nums.n),
                "e": _b64url_uint(nums.e),
            }
        ]
    }


@pytest.fixture(scope="session")
def knuckles_test_key() -> rsa.RSAPrivateKey:
    """A 2048-bit RSA key generated once per test session.

    Generating a fresh key per test would dominate the suite runtime,
    so we share one across every test that needs Knuckles tokens.

    Returns:
        The RSA private key tests sign tokens with.
    """
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def stub_knuckles_jwks(
    monkeypatch: pytest.MonkeyPatch,
    knuckles_test_key: rsa.RSAPrivateKey,
) -> str:
    """Stub the Knuckles JWKS endpoint with the session test key.

    Patches :meth:`jwt.PyJWKClient.fetch_data`, which the SDK's JWKS
    verifier uses under the hood. Each test that takes this fixture
    also benefits from the autouse client reset above so the SDK
    singleton picks up the patched fetcher on first use.

    Args:
        monkeypatch: pytest's monkeypatch fixture.
        knuckles_test_key: The session-scoped RSA key to publish.

    Returns:
        The ``kid`` the test key is published under, so callers can
        sign tokens with a matching header.
    """
    document = _build_jwks(knuckles_test_key.public_key(), kid=KNUCKLES_TEST_KID)
    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", lambda _self: document)
    return KNUCKLES_TEST_KID


def mint_knuckles_token(
    *,
    signing_key: rsa.RSAPrivateKey,
    kid: str,
    user_id: _uuid.UUID | str,
    email: str | None = None,
    audience: str = KNUCKLES_TEST_CLIENT_ID,
    issuer: str = KNUCKLES_TEST_URL,
    exp_offset: int = 600,
) -> str:
    """Mint a Knuckles-shaped RS256 access token for tests.

    Args:
        signing_key: RSA private key to sign with — typically the
            session-scoped ``knuckles_test_key`` fixture.
        kid: Key id header — must match the JWKS published by
            :func:`stub_knuckles_jwks`.
        user_id: The Greenroom/Knuckles user UUID to embed as ``sub``.
        email: Optional email claim mirroring what Knuckles emits.
        audience: ``aud`` claim. Defaults to the test app-client id.
        issuer: ``iss`` claim. Defaults to the test Knuckles URL.
        exp_offset: Seconds in the future for the ``exp`` claim. Use
            a negative value to mint an already-expired token.

    Returns:
        A signed RS256 JWT.
    """
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": issuer,
        "sub": str(user_id),
        "aud": audience,
        "iat": now,
        "exp": now + exp_offset,
    }
    if email is not None:
        claims["email"] = email
    return jwt.encode(claims, signing_key, algorithm="RS256", headers={"kid": kid})

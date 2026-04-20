"""Unit tests for :mod:`backend.core.knuckles_client`.

These tests stub the JWKS endpoint with an in-memory key generated per
fixture so the whole flow — JWKS fetch, signature verification, claim
checks, key rotation — can run without a network or a live Knuckles
instance.
"""

from __future__ import annotations

import base64
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from backend.core import knuckles_client
from backend.core.exceptions import (
    INVALID_TOKEN,
    TOKEN_EXPIRED,
    AppError,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _b64url_uint(value: int) -> str:
    """Encode a non-negative int as unpadded base64url (JWK spec)."""
    length = (value.bit_length() + 7) // 8
    raw = value.to_bytes(length, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _jwks_for(public_key: rsa.RSAPublicKey, *, kid: str) -> dict[str, Any]:
    """Build a single-key JWKS document for the given public key."""
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


def _mint(
    private_key: rsa.RSAPrivateKey,
    *,
    kid: str,
    claims: dict[str, Any],
) -> str:
    """Sign a JWT with the given key + kid header."""
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": kid})


def _default_claims(*, exp_offset: int = 60) -> dict[str, Any]:
    """Return a valid claim set targeting the test config."""
    now = int(time.time())
    return {
        "iss": "https://knuckles.test",
        "sub": "11111111-1111-1111-1111-111111111111",
        "aud": "greenroom-app-client",
        "iat": now,
        "exp": now + exp_offset,
    }


@dataclass
class _StubResponse:
    """Tiny ``requests.Response`` stand-in."""

    status_code: int
    payload: Any
    text: str = ""

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import requests as _requests

            raise _requests.HTTPError(f"{self.status_code}")

    def json(self) -> Any:
        return self.payload


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_cache() -> Iterator[None]:
    """Drop the JWKS cache before and after every test."""
    knuckles_client.reset_jwks_cache()
    yield
    knuckles_client.reset_jwks_cache()


@pytest.fixture(autouse=True)
def _knuckles_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point Settings at a fixed Knuckles URL + client id for assertions."""
    monkeypatch.setenv("KNUCKLES_URL", "https://knuckles.test")
    monkeypatch.setenv("KNUCKLES_CLIENT_ID", "greenroom-app-client")
    monkeypatch.setenv("KNUCKLES_CLIENT_SECRET", "shh")
    monkeypatch.setenv("KNUCKLES_JWKS_CACHE_TTL_SECONDS", "3600")


@pytest.fixture
def signing_key() -> rsa.RSAPrivateKey:
    """A fresh RSA key for the duration of one test."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def stub_jwks(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[rsa.RSAPublicKey, str], list[str]]:
    """Install a stub for ``requests.get`` that returns a JWKS document.

    Returns the list of URLs the client fetched, so tests can assert on
    refresh/no-refresh behavior.
    """

    fetched: list[str] = []
    state: dict[str, Any] = {"document": None}

    def install(public_key: rsa.RSAPublicKey, kid: str) -> list[str]:
        state["document"] = _jwks_for(public_key, kid=kid)
        return fetched

    def fake_get(url: str, timeout: int) -> _StubResponse:
        fetched.append(url)
        return _StubResponse(status_code=200, payload=state["document"])

    monkeypatch.setattr(knuckles_client.requests, "get", fake_get)
    return install


# ---------------------------------------------------------------------------
# verify_knuckles_token
# ---------------------------------------------------------------------------


def test_verify_happy_path_returns_claims(
    signing_key: rsa.RSAPrivateKey,
    stub_jwks: Callable[[rsa.RSAPublicKey, str], list[str]],
) -> None:
    """A token signed by the JWKS key with matching aud/iss decodes cleanly."""
    fetched = stub_jwks(signing_key.public_key(), "kid-1")
    token = _mint(signing_key, kid="kid-1", claims=_default_claims())

    claims = knuckles_client.verify_knuckles_token(token)

    assert claims["sub"] == "11111111-1111-1111-1111-111111111111"
    assert claims["aud"] == "greenroom-app-client"
    assert fetched == ["https://knuckles.test/.well-known/jwks.json"]


def test_verify_caches_jwks_across_calls(
    signing_key: rsa.RSAPrivateKey,
    stub_jwks: Callable[[rsa.RSAPublicKey, str], list[str]],
) -> None:
    """Two consecutive verifications hit the JWKS endpoint exactly once."""
    fetched = stub_jwks(signing_key.public_key(), "kid-1")
    token = _mint(signing_key, kid="kid-1", claims=_default_claims())

    knuckles_client.verify_knuckles_token(token)
    knuckles_client.verify_knuckles_token(token)

    assert len(fetched) == 1


def test_verify_refreshes_jwks_after_ttl(
    monkeypatch: pytest.MonkeyPatch,
    signing_key: rsa.RSAPrivateKey,
    stub_jwks: Callable[[rsa.RSAPublicKey, str], list[str]],
) -> None:
    """A second call past the TTL forces a JWKS refetch."""
    monkeypatch.setenv("KNUCKLES_JWKS_CACHE_TTL_SECONDS", "1")
    fetched = stub_jwks(signing_key.public_key(), "kid-1")
    token = _mint(signing_key, kid="kid-1", claims=_default_claims(exp_offset=600))

    knuckles_client.verify_knuckles_token(token)

    real_time = knuckles_client.time.time
    monkeypatch.setattr(knuckles_client.time, "time", lambda: real_time() + 5)

    knuckles_client.verify_knuckles_token(token)

    assert len(fetched) == 2


def test_verify_unknown_kid_forces_refresh_then_succeeds(
    signing_key: rsa.RSAPrivateKey,
    stub_jwks: Callable[[rsa.RSAPublicKey, str], list[str]],
) -> None:
    """A previously-unseen kid triggers an immediate JWKS refresh (rotation)."""
    fetched = stub_jwks(signing_key.public_key(), "kid-old")
    knuckles_client.verify_knuckles_token(
        _mint(signing_key, kid="kid-old", claims=_default_claims())
    )
    assert len(fetched) == 1

    new_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    stub_jwks(new_key.public_key(), "kid-new")
    rotated = _mint(new_key, kid="kid-new", claims=_default_claims())

    claims = knuckles_client.verify_knuckles_token(rotated)

    assert claims["sub"] == "11111111-1111-1111-1111-111111111111"
    assert len(fetched) == 2


def test_verify_unknown_kid_after_refresh_raises_invalid_token(
    signing_key: rsa.RSAPrivateKey,
    stub_jwks: Callable[[rsa.RSAPublicKey, str], list[str]],
) -> None:
    """A kid that's never present even after refresh fails as INVALID_TOKEN."""
    stub_jwks(signing_key.public_key(), "kid-1")
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = _mint(other_key, kid="kid-unknown", claims=_default_claims())

    with pytest.raises(AppError) as excinfo:
        knuckles_client.verify_knuckles_token(forged)
    assert excinfo.value.code == INVALID_TOKEN


def test_verify_expired_token_raises_token_expired(
    signing_key: rsa.RSAPrivateKey,
    stub_jwks: Callable[[rsa.RSAPublicKey, str], list[str]],
) -> None:
    """An ``exp`` in the past surfaces as TOKEN_EXPIRED, not INVALID_TOKEN."""
    stub_jwks(signing_key.public_key(), "kid-1")
    token = _mint(signing_key, kid="kid-1", claims=_default_claims(exp_offset=-30))

    with pytest.raises(AppError) as excinfo:
        knuckles_client.verify_knuckles_token(token)
    assert excinfo.value.code == TOKEN_EXPIRED


def test_verify_wrong_audience_raises_invalid_token(
    signing_key: rsa.RSAPrivateKey,
    stub_jwks: Callable[[rsa.RSAPublicKey, str], list[str]],
) -> None:
    """A token issued for a different app rejects with INVALID_TOKEN."""
    stub_jwks(signing_key.public_key(), "kid-1")
    claims = _default_claims()
    claims["aud"] = "some-other-app"
    token = _mint(signing_key, kid="kid-1", claims=claims)

    with pytest.raises(AppError) as excinfo:
        knuckles_client.verify_knuckles_token(token)
    assert excinfo.value.code == INVALID_TOKEN


def test_verify_wrong_issuer_raises_invalid_token(
    signing_key: rsa.RSAPrivateKey,
    stub_jwks: Callable[[rsa.RSAPublicKey, str], list[str]],
) -> None:
    """A token with a foreign ``iss`` rejects with INVALID_TOKEN."""
    stub_jwks(signing_key.public_key(), "kid-1")
    claims = _default_claims()
    claims["iss"] = "https://evil.example"
    token = _mint(signing_key, kid="kid-1", claims=claims)

    with pytest.raises(AppError) as excinfo:
        knuckles_client.verify_knuckles_token(token)
    assert excinfo.value.code == INVALID_TOKEN


def test_verify_missing_kid_header_raises_invalid_token(
    signing_key: rsa.RSAPrivateKey,
    stub_jwks: Callable[[rsa.RSAPublicKey, str], list[str]],
) -> None:
    """A token without a ``kid`` header rejects before any JWKS fetch."""
    stub_jwks(signing_key.public_key(), "kid-1")
    token = jwt.encode(_default_claims(), signing_key, algorithm="RS256")

    with pytest.raises(AppError) as excinfo:
        knuckles_client.verify_knuckles_token(token)
    assert excinfo.value.code == INVALID_TOKEN


def test_verify_garbage_token_raises_invalid_token() -> None:
    """A non-JWT string is rejected at header-parse time."""
    with pytest.raises(AppError) as excinfo:
        knuckles_client.verify_knuckles_token("not.a.jwt")
    assert excinfo.value.code == INVALID_TOKEN


def test_verify_jwks_endpoint_failure_raises_invalid_token(
    monkeypatch: pytest.MonkeyPatch,
    signing_key: rsa.RSAPrivateKey,
) -> None:
    """A network or 5xx error fetching the JWKS rejects with INVALID_TOKEN."""

    def boom(url: str, timeout: int) -> _StubResponse:
        return _StubResponse(status_code=503, payload={}, text="boom")

    monkeypatch.setattr(knuckles_client.requests, "get", boom)
    token = _mint(signing_key, kid="kid-1", claims=_default_claims())

    with pytest.raises(AppError) as excinfo:
        knuckles_client.verify_knuckles_token(token)
    assert excinfo.value.code == INVALID_TOKEN


# ---------------------------------------------------------------------------
# JWKS disk cache
# ---------------------------------------------------------------------------


def test_successful_fetch_persists_jwks_to_disk(
    signing_key: rsa.RSAPrivateKey,
    stub_jwks: Callable[[rsa.RSAPublicKey, str], list[str]],
) -> None:
    """A successful JWKS fetch writes the document to the disk cache."""
    stub_jwks(signing_key.public_key(), "kid-1")
    knuckles_client.verify_knuckles_token(
        _mint(signing_key, kid="kid-1", claims=_default_claims())
    )

    import json

    path = knuckles_client._disk_cache_path()
    assert path.exists()
    payload = json.loads(path.read_text())
    assert "fetched_at" in payload
    assert payload["keys"][0]["kid"] == "kid-1"


def test_cold_start_loads_from_disk_without_network(
    monkeypatch: pytest.MonkeyPatch,
    signing_key: rsa.RSAPrivateKey,
) -> None:
    """Populated disk + fresh snapshot → verify without hitting the network."""
    import json

    fetched: list[str] = []

    def fake_get(url: str, timeout: int) -> _StubResponse:
        fetched.append(url)
        return _StubResponse(status_code=503, payload={}, text="should not happen")

    monkeypatch.setattr(knuckles_client.requests, "get", fake_get)

    document = _jwks_for(signing_key.public_key(), kid="kid-1")
    path = knuckles_client._disk_cache_path()
    path.write_text(
        json.dumps(
            {
                "fetched_at": time.time(),
                "keys": document["keys"],
            }
        )
    )

    token = _mint(signing_key, kid="kid-1", claims=_default_claims())
    claims = knuckles_client.verify_knuckles_token(token)

    assert claims["sub"] == "11111111-1111-1111-1111-111111111111"
    assert fetched == []


def test_stale_disk_cache_forces_network_refetch(
    monkeypatch: pytest.MonkeyPatch,
    signing_key: rsa.RSAPrivateKey,
    stub_jwks: Callable[[rsa.RSAPublicKey, str], list[str]],
) -> None:
    """Disk snapshot past TTL → refetch from network on cold start."""
    import json

    monkeypatch.setenv("KNUCKLES_JWKS_CACHE_TTL_SECONDS", "60")
    document = _jwks_for(signing_key.public_key(), kid="kid-1")
    path = knuckles_client._disk_cache_path()
    path.write_text(
        json.dumps(
            {
                "fetched_at": time.time() - 600,
                "keys": document["keys"],
            }
        )
    )

    fetched = stub_jwks(signing_key.public_key(), "kid-1")
    token = _mint(signing_key, kid="kid-1", claims=_default_claims())

    knuckles_client.verify_knuckles_token(token)

    assert len(fetched) == 1


def test_corrupt_disk_cache_falls_back_to_network(
    signing_key: rsa.RSAPrivateKey,
    stub_jwks: Callable[[rsa.RSAPublicKey, str], list[str]],
) -> None:
    """A garbage disk file is ignored rather than crashing verification."""
    path = knuckles_client._disk_cache_path()
    path.write_text("this is not json {{{")

    fetched = stub_jwks(signing_key.public_key(), "kid-1")
    token = _mint(signing_key, kid="kid-1", claims=_default_claims())

    knuckles_client.verify_knuckles_token(token)

    assert len(fetched) == 1


# ---------------------------------------------------------------------------
# post()
# ---------------------------------------------------------------------------


def test_post_sends_app_client_headers_and_returns_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """post() forwards the X-Client-* headers and returns the JSON body."""
    captured: dict[str, Any] = {}

    def fake_post(
        url: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str],
        timeout: int,
    ) -> _StubResponse:
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        captured["timeout"] = timeout
        return _StubResponse(status_code=200, payload={"sent": True})

    monkeypatch.setattr(knuckles_client.requests, "post", fake_post)

    result = knuckles_client.post(
        "/v1/auth/magic-link/start", json={"email": "a@b.test"}
    )

    assert result == {"sent": True}
    assert captured["url"] == "https://knuckles.test/v1/auth/magic-link/start"
    assert captured["json"] == {"email": "a@b.test"}
    assert captured["headers"]["X-Client-Id"] == "greenroom-app-client"
    assert captured["headers"]["X-Client-Secret"] == "shh"


def test_post_raises_knuckles_http_error_with_upstream_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-2xx response surfaces as KnucklesHTTPError with the same status."""

    def fake_post(
        url: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str],
        timeout: int,
    ) -> _StubResponse:
        return _StubResponse(
            status_code=422,
            payload={"error": {"code": "VALIDATION_ERROR", "message": "no good"}},
        )

    monkeypatch.setattr(knuckles_client.requests, "post", fake_post)

    with pytest.raises(knuckles_client.KnucklesHTTPError) as excinfo:
        knuckles_client.post("/v1/auth/magic-link/start", json={})

    assert excinfo.value.status_code == 422
    assert "no good" in excinfo.value.message


def test_post_handles_non_json_error_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An HTML/text error body still produces a KnucklesHTTPError."""

    class _BadJsonResponse(_StubResponse):
        def json(self) -> Any:
            raise ValueError("not json")

    def fake_post(
        url: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str],
        timeout: int,
    ) -> _StubResponse:
        return _BadJsonResponse(status_code=500, payload=None, text="<html>")

    monkeypatch.setattr(knuckles_client.requests, "post", fake_post)

    with pytest.raises(knuckles_client.KnucklesHTTPError) as excinfo:
        knuckles_client.post("/v1/auth/magic-link/start", json={})
    assert excinfo.value.status_code == 500


def test_post_with_no_body_sends_empty_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Omitting ``json`` posts ``{}`` — Knuckles expects a JSON body."""
    captured: dict[str, Any] = {}

    def fake_post(
        url: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str],
        timeout: int,
    ) -> _StubResponse:
        captured["json"] = json
        return _StubResponse(status_code=200, payload={"ok": True})

    monkeypatch.setattr(knuckles_client.requests, "post", fake_post)

    knuckles_client.post("/v1/auth/passkey/sign-in/begin")

    assert captured["json"] == {}

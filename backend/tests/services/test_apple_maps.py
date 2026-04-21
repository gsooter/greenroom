"""Unit tests for :mod:`backend.services.apple_maps`.

Exercises the MapKit JS token minter and the Redis-backed cache
behavior. ES256 signing uses the session-wide test key seeded in
``conftest.py``; ``jwt.decode`` with the paired public half verifies
both the header and claims.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization

from backend.core.exceptions import APPLE_MAPS_UNAVAILABLE, AppError
from backend.services import apple_maps as service
from backend.tests.conftest import APPLE_MAPKIT_TEST_PEM


@pytest.fixture(autouse=True)
def _disable_module_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the module-level Redis client out of the way.

    Tests that want caching pass their own ``_FakeRedis`` explicitly.
    Unit tests that pass ``redis_client=None`` should behave as if Redis
    were unreachable so each mint call produces a fresh token.
    """
    service.reset_redis_client_for_tests()
    monkeypatch.setattr(service, "_get_redis", lambda: None)


def _public_key_pem() -> bytes:
    """Return the PEM of the MapKit test key's public half.

    Returns:
        The SubjectPublicKeyInfo PEM bytes for the ES256 test key.
    """
    private_key = serialization.load_pem_private_key(
        APPLE_MAPKIT_TEST_PEM.encode("ascii"),
        password=None,
    )
    return private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


class _FakeRedis:
    """Minimal in-memory stand-in for the redis client methods we use."""

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.expirations: dict[str, int] = {}

    def get(self, key: str) -> bytes | None:
        return self.store.get(key)

    def set(
        self,
        key: str,
        value: str | bytes,
        *,
        ex: int | None = None,
    ) -> None:
        self.store[key] = value.encode() if isinstance(value, str) else value
        if ex is not None:
            self.expirations[key] = ex


# ---------------------------------------------------------------------------
# is_configured
# ---------------------------------------------------------------------------


def test_is_configured_true_when_env_populated() -> None:
    """conftest seeds every MapKit env var, so this should be True."""
    assert service.is_configured() is True


def test_is_configured_false_without_team_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPLE_MAPKIT_TEAM_ID", "")
    assert service.is_configured() is False


def test_is_configured_false_without_any_private_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPLE_MAPKIT_PRIVATE_KEY", "")
    monkeypatch.setenv("APPLE_MAPKIT_PRIVATE_KEY_PATH", "")
    assert service.is_configured() is False


# ---------------------------------------------------------------------------
# mint_mapkit_token
# ---------------------------------------------------------------------------


def test_mint_mapkit_token_signs_with_es256_and_includes_origin() -> None:
    payload = service.mint_mapkit_token(
        origin="https://example.test",
        redis_client=None,
    )
    assert set(payload) == {"token", "expires_at"}

    token = payload["token"]
    assert isinstance(token, str)
    header = jwt.get_unverified_header(token)
    assert header["alg"] == "ES256"
    assert header["kid"] == "TESTMAP001"
    assert header["typ"] == "JWT"

    decoded = jwt.decode(token, _public_key_pem(), algorithms=["ES256"])
    assert decoded["iss"] == "TESTTEAM01"
    assert decoded["origin"] == "https://example.test"
    assert decoded["exp"] > decoded["iat"]


def test_mint_mapkit_token_omits_origin_claim_when_none() -> None:
    payload = service.mint_mapkit_token(origin=None, redis_client=None)
    decoded = jwt.decode(payload["token"], _public_key_pem(), algorithms=["ES256"])
    assert "origin" not in decoded


def test_mint_mapkit_token_expires_thirty_minutes_from_anchor() -> None:
    anchor = datetime(2026, 4, 1, 12, 0, 0, tzinfo=UTC)
    payload = service.mint_mapkit_token(now=anchor, redis_client=None)
    expected = int((anchor + timedelta(minutes=30)).timestamp())
    assert payload["expires_at"] == expected


def test_mint_mapkit_token_raises_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPLE_MAPKIT_TEAM_ID", "")
    with pytest.raises(AppError) as exc:
        service.mint_mapkit_token(redis_client=None)
    assert exc.value.code == APPLE_MAPS_UNAVAILABLE
    assert exc.value.status_code == 503


def test_mint_mapkit_token_returns_cached_payload_when_fresh() -> None:
    fake = _FakeRedis()
    first = service.mint_mapkit_token(origin="o1", redis_client=fake)
    second = service.mint_mapkit_token(origin="o1", redis_client=fake)
    # Second call must return the exact cached payload (byte-identical
    # token) rather than re-sign with a new iat.
    assert first == second


def test_mint_mapkit_token_skips_cached_entry_when_expired() -> None:
    fake = _FakeRedis()
    expired_payload: dict[str, Any] = {
        "token": "stale.jwt.value",
        "expires_at": int(datetime.now(UTC).timestamp()) - 60,
    }
    fake.store["apple_maps:mapkit_token:v1:o1"] = json.dumps(expired_payload).encode()

    payload = service.mint_mapkit_token(origin="o1", redis_client=fake)
    assert payload["token"] != "stale.jwt.value"
    # New entry was written with the 25-minute TTL.
    assert fake.expirations["apple_maps:mapkit_token:v1:o1"] == 25 * 60


def test_mint_mapkit_token_ignores_cache_entries_missing_fields() -> None:
    fake = _FakeRedis()
    fake.store["apple_maps:mapkit_token:v1:o1"] = b'{"token": null}'
    payload = service.mint_mapkit_token(origin="o1", redis_client=fake)
    assert isinstance(payload["token"], str)
    assert len(payload["token"]) > 0


def test_mint_mapkit_token_tolerates_non_json_cache_blob() -> None:
    fake = _FakeRedis()
    fake.store["apple_maps:mapkit_token:v1:o1"] = b"not-json"
    payload = service.mint_mapkit_token(origin="o1", redis_client=fake)
    assert isinstance(payload["token"], str)


def test_mint_mapkit_token_buckets_by_origin_in_cache() -> None:
    fake = _FakeRedis()
    first = service.mint_mapkit_token(origin="a", redis_client=fake)
    second = service.mint_mapkit_token(origin="b", redis_client=fake)
    # Different origins live under different keys and therefore produce
    # distinct signed tokens.
    assert first["token"] != second["token"]
    assert "apple_maps:mapkit_token:v1:a" in fake.store
    assert "apple_maps:mapkit_token:v1:b" in fake.store


def test_mint_mapkit_token_loads_key_from_disk_when_inline_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    key_path = tmp_path / "mapkit.p8"
    key_path.write_text(APPLE_MAPKIT_TEST_PEM, encoding="utf-8")

    monkeypatch.setenv("APPLE_MAPKIT_PRIVATE_KEY", "")
    monkeypatch.setenv("APPLE_MAPKIT_PRIVATE_KEY_PATH", str(key_path))

    payload = service.mint_mapkit_token(redis_client=None)
    decoded = jwt.decode(payload["token"], _public_key_pem(), algorithms=["ES256"])
    assert decoded["iss"] == "TESTTEAM01"


def test_mint_mapkit_token_raises_when_key_path_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPLE_MAPKIT_PRIVATE_KEY", "")
    monkeypatch.setenv("APPLE_MAPKIT_PRIVATE_KEY_PATH", "/nonexistent/definitely.p8")
    with pytest.raises(AppError) as exc:
        service.mint_mapkit_token(redis_client=None)
    assert exc.value.status_code == 500
    assert exc.value.code == APPLE_MAPS_UNAVAILABLE

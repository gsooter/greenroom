"""Tests for the Redis-backed rate limiter decorator.

Exercises the three behaviors that actually matter:

1. Under the limit — the wrapped function runs and returns normally.
2. At the limit — the next call raises :class:`RateLimitExceededError`
   with a ``retry_after_seconds`` hint.
3. Fail open — any Redis failure (no client, connection error) lets
   the call through so a flaky cache never takes down sign-in.

A tiny in-memory fake stands in for Redis so the tests run without
any external services.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
import redis as redis_module
from flask import Flask

from backend.core import rate_limit as rate_limit_module
from backend.core.exceptions import RateLimitExceededError
from backend.core.rate_limit import get_request_ip, rate_limit


class _FakePipeline:
    """Minimal pipeline supporting ``incr``, ``ttl``, and ``execute``."""

    def __init__(self, client: _FakeRedis) -> None:
        self._client = client
        self._ops: list[tuple[str, Any]] = []

    def incr(self, key: str, amount: int = 1) -> _FakePipeline:
        self._ops.append(("incr", (key, amount)))
        return self

    def ttl(self, key: str) -> _FakePipeline:
        self._ops.append(("ttl", key))
        return self

    def execute(self) -> list[Any]:
        results: list[Any] = []
        for op, args in self._ops:
            if op == "incr":
                key, amount = args
                self._client.store[key] = self._client.store.get(key, 0) + amount
                results.append(self._client.store[key])
            elif op == "ttl":
                results.append(self._client.ttls.get(args, -1))
        return results


class _FakeRedis:
    """Stand-in for a real :class:`redis.Redis` client.

    Implements only the subset the limiter touches: ``pipeline``,
    ``expire``, and bucket inspection.
    """

    def __init__(self) -> None:
        self.store: dict[str, int] = {}
        self.ttls: dict[str, int] = {}

    def pipeline(self) -> _FakePipeline:
        return _FakePipeline(self)

    def expire(self, key: str, seconds: int) -> bool:
        self.ttls[key] = seconds
        return True


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> Iterator[_FakeRedis]:
    """Inject a fake redis client into the rate-limit module.

    Yields:
        The :class:`_FakeRedis` the limiter will use for the test.
    """
    client = _FakeRedis()
    monkeypatch.setattr(rate_limit_module, "_get_redis", lambda: client)
    yield client


@pytest.fixture
def flask_app() -> Flask:
    """Build a tiny Flask app so ``get_request_ip`` has a request ctx.

    Returns:
        A new Flask app wired up with the limited route.
    """
    app = Flask(__name__)

    @app.route("/ping", methods=["POST"])
    @rate_limit("unit_test_ping", limit=3, window_seconds=60)
    def ping() -> tuple[dict[str, Any], int]:
        """Trivial route that returns ``ok``."""
        return {"ok": True}, 200

    @app.errorhandler(RateLimitExceededError)
    def handle_rate_limit(
        error: RateLimitExceededError,
    ) -> tuple[dict[str, Any], int, dict[str, str]]:
        """Mirror the real app's 429 response shape."""
        headers = {}
        if error.retry_after_seconds is not None:
            headers["Retry-After"] = str(error.retry_after_seconds)
        return (
            {"error": {"code": error.code, "message": error.message}},
            error.status_code,
            headers,
        )

    return app


def test_under_limit_calls_through(fake_redis: _FakeRedis, flask_app: Flask) -> None:
    """The first ``limit`` hits succeed and the counter increments."""
    with flask_app.test_client() as client:
        for _ in range(3):
            resp = client.post("/ping")
            assert resp.status_code == 200

    # Counter keyed on ``rule:subject`` — fake redis pins subject as
    # ``remote_addr`` which Flask's test client sets to ``127.0.0.1``.
    assert fake_redis.store["rl:unit_test_ping:127.0.0.1"] == 3


def test_over_limit_raises_429_with_retry_after(
    fake_redis: _FakeRedis, flask_app: Flask
) -> None:
    """The hit that exceeds the limit returns 429 with Retry-After."""
    with flask_app.test_client() as client:
        for _ in range(3):
            client.post("/ping")
        resp = client.post("/ping")
    assert resp.status_code == 429
    body = resp.get_json()
    assert body["error"]["code"] == "RATE_LIMITED"
    assert resp.headers["Retry-After"] == "60"


def test_fail_open_when_redis_client_is_none(
    monkeypatch: pytest.MonkeyPatch, flask_app: Flask
) -> None:
    """No Redis client → every call is allowed (fail-open behavior)."""
    monkeypatch.setattr(rate_limit_module, "_get_redis", lambda: None)
    with flask_app.test_client() as client:
        for _ in range(10):
            resp = client.post("/ping")
            assert resp.status_code == 200


def test_fail_open_on_redis_error(
    monkeypatch: pytest.MonkeyPatch, flask_app: Flask
) -> None:
    """Any ``RedisError`` mid-request lets the request through."""

    class _BrokenRedis:
        def pipeline(self) -> Any:
            raise redis_module.RedisError("boom")

    monkeypatch.setattr(rate_limit_module, "_get_redis", lambda: _BrokenRedis())
    with flask_app.test_client() as client:
        for _ in range(5):
            resp = client.post("/ping")
            assert resp.status_code == 200


def test_empty_subject_bypasses_limiter(
    fake_redis: _FakeRedis, monkeypatch: pytest.MonkeyPatch, flask_app: Flask
) -> None:
    """An empty key (e.g. missing email on magic-link body) skips counting."""
    monkeypatch.setattr(rate_limit_module, "get_request_ip", lambda: "")

    app = Flask(__name__)

    @app.route("/ping", methods=["POST"])
    @rate_limit("unit_test_empty", limit=1, window_seconds=60, key_fn=lambda: "")
    def ping() -> tuple[dict[str, Any], int]:
        """Trivial route that always 200s."""
        return {"ok": True}, 200

    with app.test_client() as client:
        for _ in range(5):
            resp = client.post("/ping")
            assert resp.status_code == 200
    assert not fake_redis.store


def test_request_ip_prefers_forwarded_header(flask_app: Flask) -> None:
    """``X-Forwarded-For`` wins over ``remote_addr`` when set."""
    with flask_app.test_request_context(
        "/", headers={"X-Forwarded-For": "203.0.113.9, 10.0.0.1"}
    ):
        assert get_request_ip() == "203.0.113.9"


def test_request_ip_falls_back_to_remote_addr(flask_app: Flask) -> None:
    """Without the header, use ``remote_addr``."""
    with flask_app.test_request_context("/"):
        assert get_request_ip() == "unknown"

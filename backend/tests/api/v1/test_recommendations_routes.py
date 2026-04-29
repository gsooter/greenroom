"""Route tests for :mod:`backend.api.v1.recommendations`."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from flask.testing import FlaskClient

from backend.api.v1 import recommendations as recs_route
from backend.data.models.users import User


def test_list_recs_caps_per_page(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
) -> None:
    client, _u, hdrs = authed_client
    resp = client.get("/api/v1/me/recommendations?per_page=500", headers=hdrs())
    assert resp.status_code == 422


def test_list_recs_happy_path(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _u, hdrs = authed_client
    monkeypatch.setattr(
        recs_route.recs_service,
        "list_recommendations_for_user",
        lambda *_a, **_k: ([object()], 1),
    )
    monkeypatch.setattr(
        recs_route.recs_service,
        "serialize_recommendation",
        lambda _r: {"score": 0.9},
    )
    resp = client.get("/api/v1/me/recommendations?page=1&per_page=5", headers=hdrs())
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["data"] == [{"score": 0.9}]
    assert body["meta"]["total"] == 1
    assert body["meta"]["has_next"] is False


def test_refresh_recs_returns_generated_count(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _u, hdrs = authed_client
    monkeypatch.setattr(
        recs_route.recs_service,
        "refresh_recommendations_for_user",
        lambda _s, _u: 42,
    )
    resp = client.post("/api/v1/me/recommendations/refresh", headers=hdrs())
    assert resp.status_code == 200
    assert resp.get_json()["data"] == {"generated": 42}


def test_refresh_recs_enforces_per_user_rate_limit(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 11th refresh from the same user within an hour is blocked.

    Uses a per-key fake Redis so the test counts in-process. The user
    id is the bucket subject, so this also confirms the resolver runs
    with an authenticated current user.
    """
    from backend.core import rate_limit as rate_limit_module

    client, _u, hdrs = authed_client
    monkeypatch.setattr(
        recs_route.recs_service,
        "refresh_recommendations_for_user",
        lambda _s, _u: 1,
    )

    counter = _make_keyed_counter()
    monkeypatch.setattr(rate_limit_module, "_get_redis", lambda: counter)

    for i in range(10):
        resp = client.post("/api/v1/me/recommendations/refresh", headers=hdrs())
        assert resp.status_code == 200, f"call {i} unexpectedly blocked"

    resp = client.post("/api/v1/me/recommendations/refresh", headers=hdrs())
    assert resp.status_code == 429
    assert resp.get_json()["error"]["code"] == "RATE_LIMITED"


def _make_keyed_counter() -> Any:
    """Return a per-key fake Redis client used by the limiter test.

    Mirrors the pipeline / expire interface that
    :func:`backend.core.rate_limit.rate_limit` relies on. Counts live
    in a plain dict keyed by the limiter's full cache key so rules
    and subjects never collide.

    Returns:
        An object that satisfies the limiter's redis-client contract.
    """

    class _Pipeline:
        def __init__(self, parent: Any) -> None:
            self._parent = parent
            self._key: str | None = None

        def incr(self, key: str, *_a: Any, **_k: Any) -> _Pipeline:
            self._key = key
            self._parent.counts[key] = self._parent.counts.get(key, 0) + 1
            return self

        def ttl(self, *_a: Any, **_k: Any) -> _Pipeline:
            return self

        def execute(self) -> list[int]:
            assert self._key is not None
            return [self._parent.counts[self._key], 30]

    class _Counter:
        def __init__(self) -> None:
            self.counts: dict[str, int] = {}

        def pipeline(self) -> Any:
            return _Pipeline(self)

        def expire(self, *_a: Any, **_k: Any) -> bool:
            return True

    return _Counter()

"""Route tests for :mod:`backend.api.v1.recommendations`."""

from __future__ import annotations

from typing import Callable

import pytest
from flask.testing import FlaskClient

from backend.api.v1 import recommendations as recs_route
from backend.data.models.users import User


def test_list_recs_caps_per_page(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
) -> None:
    client, _u, hdrs = authed_client
    resp = client.get(
        "/api/v1/me/recommendations?per_page=500", headers=hdrs()
    )
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
    resp = client.get(
        "/api/v1/me/recommendations?page=1&per_page=5", headers=hdrs()
    )
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

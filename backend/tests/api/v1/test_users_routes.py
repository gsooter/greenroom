"""Route tests for :mod:`backend.api.v1.users`."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from flask.testing import FlaskClient

from backend.api.v1 import users as users_route
from backend.data.models.users import User

# ---------------------------------------------------------------------------
# Auth enforcement
# ---------------------------------------------------------------------------


def test_me_requires_auth_header(client: FlaskClient) -> None:
    """Missing bearer token → 401."""
    resp = client.get("/api/v1/me")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /me
# ---------------------------------------------------------------------------


def test_get_me_returns_serialized_user(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, user, hdrs = authed_client
    monkeypatch.setattr(
        users_route.users_service,
        "serialize_user",
        lambda u: {"id": str(u.id), "email": u.email},
    )
    resp = client.get("/api/v1/me", headers=hdrs())
    assert resp.status_code == 200
    assert resp.get_json()["data"]["id"] == str(user.id)


# ---------------------------------------------------------------------------
# PATCH /me
# ---------------------------------------------------------------------------


def test_patch_me_rejects_non_dict_body(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
) -> None:
    client, _user, hdrs = authed_client
    resp = client.patch("/api/v1/me", data="not-json", headers=hdrs())
    assert resp.status_code == 422


def test_patch_me_calls_service_and_returns_updated(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _user, hdrs = authed_client
    captured: dict[str, Any] = {}

    def fake_update(_s: Any, _u: Any, payload: dict[str, Any]) -> User:
        captured["payload"] = payload
        user: User = _u
        return user

    monkeypatch.setattr(users_route.users_service, "update_user_profile", fake_update)
    monkeypatch.setattr(
        users_route.users_service, "serialize_user", lambda u: {"id": str(u.id)}
    )
    resp = client.patch("/api/v1/me", json={"display_name": "New"}, headers=hdrs())
    assert resp.status_code == 200
    assert captured["payload"] == {"display_name": "New"}


# ---------------------------------------------------------------------------
# GET /me/spotify/top-artists
# ---------------------------------------------------------------------------


def test_top_artists_returns_cached(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
) -> None:
    client, user, hdrs = authed_client
    user.spotify_top_artists = [{"id": "a", "name": "A"}]
    user.spotify_synced_at = datetime(2026, 5, 1, tzinfo=UTC)
    resp = client.get("/api/v1/me/spotify/top-artists", headers=hdrs())
    assert resp.status_code == 200
    body = resp.get_json()["data"]
    assert body["artists"] == [{"id": "a", "name": "A"}]
    assert body["synced_at"] == "2026-05-01T00:00:00+00:00"


def test_top_artists_triggers_sync_when_empty(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, user, hdrs = authed_client
    user.spotify_top_artists = None
    user.spotify_synced_at = None

    sync_hits: list[bool] = []

    def fake_sync(_s: Any, _u: Any) -> int:
        sync_hits.append(True)
        return 0

    monkeypatch.setattr(users_route.spotify_service, "sync_top_artists", fake_sync)
    resp = client.get("/api/v1/me/spotify/top-artists", headers=hdrs())
    assert resp.status_code == 200
    assert sync_hits == [True]
    assert resp.get_json()["data"]["artists"] == []


def test_top_artists_degrades_when_sync_raises(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sync failure must degrade gracefully, never 5xx the endpoint."""
    client, user, hdrs = authed_client
    user.spotify_top_artists = None
    user.spotify_synced_at = None

    def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("spotify")

    monkeypatch.setattr(users_route.spotify_service, "sync_top_artists", boom)
    resp = client.get("/api/v1/me/spotify/top-artists", headers=hdrs())
    assert resp.status_code == 200
    assert resp.get_json()["data"]["artists"] == []


# ---------------------------------------------------------------------------
# DELETE /me
# ---------------------------------------------------------------------------


def test_delete_me_returns_204(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _user, hdrs = authed_client
    dcalls: list[bool] = []
    monkeypatch.setattr(
        users_route.users_service,
        "deactivate_user",
        lambda *_a, **_k: dcalls.append(True),
    )
    resp = client.delete("/api/v1/me", headers=hdrs())
    assert resp.status_code == 204
    assert dcalls == [True]

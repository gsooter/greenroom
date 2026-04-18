"""Route tests for :mod:`backend.api.v1.saved_events`."""

from __future__ import annotations

import uuid
from typing import Any, Callable
from unittest.mock import MagicMock

import pytest
from flask.testing import FlaskClient

from backend.api.v1 import saved_events as saved_route
from backend.data.models.users import User


# ---------------------------------------------------------------------------
# POST /events/<event_id>/save
# ---------------------------------------------------------------------------


def test_save_event_rejects_bad_uuid(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
) -> None:
    client, _u, hdrs = authed_client
    resp = client.post("/api/v1/events/not-a-uuid/save", headers=hdrs())
    assert resp.status_code == 422


def test_save_event_returns_201_when_new(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _u, hdrs = authed_client
    # No existing saved row → 201 path.
    monkeypatch.setattr(
        saved_route.users_repo, "get_saved_event", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        saved_route.saved_events_service,
        "save_event",
        lambda *_a, **_k: object(),
    )
    monkeypatch.setattr(
        saved_route.saved_events_service,
        "serialize_saved_event",
        lambda _s: {"ok": True},
    )
    resp = client.post(
        f"/api/v1/events/{uuid.uuid4()}/save", headers=hdrs()
    )
    assert resp.status_code == 201


def test_save_event_returns_200_when_already_saved(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _u, hdrs = authed_client
    monkeypatch.setattr(
        saved_route.users_repo,
        "get_saved_event",
        lambda *_a, **_k: MagicMock(),
    )
    monkeypatch.setattr(
        saved_route.saved_events_service,
        "save_event",
        lambda *_a, **_k: object(),
    )
    monkeypatch.setattr(
        saved_route.saved_events_service,
        "serialize_saved_event",
        lambda _s: {"ok": True},
    )
    resp = client.post(
        f"/api/v1/events/{uuid.uuid4()}/save", headers=hdrs()
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# DELETE /events/<event_id>/save
# ---------------------------------------------------------------------------


def test_unsave_event_returns_204(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _u, hdrs = authed_client
    monkeypatch.setattr(
        saved_route.saved_events_service,
        "unsave_event",
        lambda *_a, **_k: True,
    )
    resp = client.delete(
        f"/api/v1/events/{uuid.uuid4()}/save", headers=hdrs()
    )
    assert resp.status_code == 204


def test_unsave_event_rejects_bad_uuid(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
) -> None:
    client, _u, hdrs = authed_client
    resp = client.delete("/api/v1/events/bogus/save", headers=hdrs())
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /me/saved-events
# ---------------------------------------------------------------------------


def test_list_saved_events_caps_per_page(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
) -> None:
    client, _u, hdrs = authed_client
    resp = client.get(
        "/api/v1/me/saved-events?per_page=500", headers=hdrs()
    )
    assert resp.status_code == 422


def test_list_saved_events_happy_path(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _u, hdrs = authed_client
    monkeypatch.setattr(
        saved_route.saved_events_service,
        "list_saved_events",
        lambda *_a, **_k: ([object(), object()], 5),
    )
    monkeypatch.setattr(
        saved_route.saved_events_service,
        "serialize_saved_event",
        lambda _s: {"id": "x"},
    )
    resp = client.get("/api/v1/me/saved-events?page=1&per_page=2", headers=hdrs())
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["meta"] == {
        "total": 5,
        "page": 1,
        "per_page": 2,
        "has_next": True,
    }
    assert len(body["data"]) == 2

"""Route tests for :mod:`backend.api.v1.onboarding`.

Covers the onboarding-state lifecycle, artist search, follow/unfollow,
and the genre catalog. Heavy lifting is monkeypatched at the service
layer so the route layer is exercised in isolation.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from unittest.mock import MagicMock

import pytest
from flask.testing import FlaskClient

from backend.api.v1 import onboarding as onboarding_route
from backend.core.exceptions import NotFoundError
from backend.data.models.users import User

# ---------------------------------------------------------------------------
# /me/onboarding
# ---------------------------------------------------------------------------


def test_get_onboarding_state_returns_serialized_payload(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _u, hdrs = authed_client
    monkeypatch.setattr(
        onboarding_route.onboarding_service,
        "get_state",
        lambda *_a, **_k: MagicMock(),
    )
    monkeypatch.setattr(
        onboarding_route.onboarding_service,
        "serialize_state",
        lambda _s: {"completed": False},
    )
    resp = client.get("/api/v1/me/onboarding", headers=hdrs())
    assert resp.status_code == 200
    assert resp.get_json() == {"data": {"completed": False}}


def test_complete_step_happy_path(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _u, hdrs = authed_client
    monkeypatch.setattr(
        onboarding_route.onboarding_service,
        "mark_step_complete",
        lambda *_a, **_k: MagicMock(),
    )
    monkeypatch.setattr(
        onboarding_route.onboarding_service,
        "serialize_state",
        lambda _s: {"steps": {"taste": True}},
    )
    resp = client.post("/api/v1/me/onboarding/steps/taste/complete", headers=hdrs())
    assert resp.status_code == 200


def test_complete_step_rejects_unknown_step(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
) -> None:
    client, _u, hdrs = authed_client
    resp = client.post("/api/v1/me/onboarding/steps/profile/complete", headers=hdrs())
    # Service raises ValidationError → HTTP 422.
    assert resp.status_code == 422


def test_skip_entire_flow(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _u, hdrs = authed_client
    monkeypatch.setattr(
        onboarding_route.onboarding_service,
        "mark_skipped_entirely",
        lambda *_a, **_k: MagicMock(),
    )
    monkeypatch.setattr(
        onboarding_route.onboarding_service,
        "serialize_state",
        lambda _s: {"skipped": True},
    )
    resp = client.post("/api/v1/me/onboarding/skip-all", headers=hdrs())
    assert resp.status_code == 200


def test_dismiss_banner(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _u, hdrs = authed_client
    monkeypatch.setattr(
        onboarding_route.onboarding_service,
        "dismiss_banner",
        lambda *_a, **_k: MagicMock(),
    )
    monkeypatch.setattr(
        onboarding_route.onboarding_service,
        "serialize_state",
        lambda _s: {},
    )
    resp = client.post("/api/v1/me/onboarding/banner/dismiss", headers=hdrs())
    assert resp.status_code == 200


def test_increment_browse_sessions(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _u, hdrs = authed_client
    monkeypatch.setattr(
        onboarding_route.onboarding_service,
        "increment_browse_sessions",
        lambda *_a, **_k: MagicMock(),
    )
    monkeypatch.setattr(
        onboarding_route.onboarding_service,
        "serialize_state",
        lambda _s: {},
    )
    resp = client.post("/api/v1/me/onboarding/sessions/increment", headers=hdrs())
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# /genres
# ---------------------------------------------------------------------------


def test_list_genres_is_public_and_returns_catalog(client: FlaskClient) -> None:
    resp = client.get("/api/v1/genres")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "genres" in body["data"]
    slugs = {g["slug"] for g in body["data"]["genres"]}
    assert {"indie-rock", "jazz", "punk"}.issubset(slugs)
    # Each entry has the required shape.
    for genre in body["data"]["genres"]:
        assert set(genre.keys()) == {"slug", "label", "emoji"}


# ---------------------------------------------------------------------------
# /artists (search)
# ---------------------------------------------------------------------------


def test_search_artists_happy_path(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _u, hdrs = authed_client
    monkeypatch.setattr(
        onboarding_route.follows_service,
        "search_artists_for_user",
        lambda *_a, **_k: [
            {"id": "a", "name": "A", "is_followed": False, "genres": []}
        ],
    )
    resp = client.get("/api/v1/artists?query=a", headers=hdrs())
    assert resp.status_code == 200
    body = resp.get_json()
    assert len(body["data"]["artists"]) == 1


def test_search_artists_requires_auth(client: FlaskClient) -> None:
    resp = client.get("/api/v1/artists?query=x")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# /me/followed-artists
# ---------------------------------------------------------------------------


def test_follow_artist_returns_201(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _u, hdrs = authed_client
    monkeypatch.setattr(
        onboarding_route.follows_service, "follow_artist", lambda *_a, **_k: None
    )
    resp = client.post(f"/api/v1/me/followed-artists/{uuid.uuid4()}", headers=hdrs())
    assert resp.status_code == 201


def test_follow_artist_rejects_bad_uuid(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
) -> None:
    client, _u, hdrs = authed_client
    resp = client.post("/api/v1/me/followed-artists/bogus", headers=hdrs())
    assert resp.status_code == 422


def test_follow_artist_returns_404_when_missing(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _u, hdrs = authed_client

    def _raise(*_a: object, **_k: object) -> None:
        raise NotFoundError(code="ARTIST_NOT_FOUND", message="missing")

    monkeypatch.setattr(onboarding_route.follows_service, "follow_artist", _raise)
    resp = client.post(f"/api/v1/me/followed-artists/{uuid.uuid4()}", headers=hdrs())
    assert resp.status_code == 404


def test_unfollow_artist_returns_204(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _u, hdrs = authed_client
    monkeypatch.setattr(
        onboarding_route.follows_service, "unfollow_artist", lambda *_a, **_k: None
    )
    resp = client.delete(f"/api/v1/me/followed-artists/{uuid.uuid4()}", headers=hdrs())
    assert resp.status_code == 204


def test_list_followed_artists_paginated(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _u, hdrs = authed_client
    monkeypatch.setattr(
        onboarding_route.follows_service,
        "list_followed_artists",
        lambda *_a, **_k: ([{"id": "x"}], 1),
    )
    resp = client.get("/api/v1/me/followed-artists", headers=hdrs())
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["meta"]["total"] == 1


def test_list_followed_artists_caps_per_page(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
) -> None:
    client, _u, hdrs = authed_client
    resp = client.get("/api/v1/me/followed-artists?per_page=500", headers=hdrs())
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# /me/followed-venues
# ---------------------------------------------------------------------------


def test_follow_venues_bulk_happy_path(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _u, hdrs = authed_client
    monkeypatch.setattr(
        onboarding_route.follows_service,
        "follow_venues_bulk",
        lambda *_a, **_k: 2,
    )
    body = {"venue_ids": [str(uuid.uuid4()), str(uuid.uuid4())]}
    resp = client.post("/api/v1/me/followed-venues", json=body, headers=hdrs())
    assert resp.status_code == 201
    assert resp.get_json()["data"] == {"written": 2}


def test_follow_venues_bulk_requires_json_object(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
) -> None:
    client, _u, hdrs = authed_client
    resp = client.post(
        "/api/v1/me/followed-venues",
        data="not json",
        content_type="text/plain",
        headers=hdrs(),
    )
    assert resp.status_code == 422


def test_follow_venues_bulk_rejects_bad_venue_ids(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
) -> None:
    client, _u, hdrs = authed_client
    resp = client.post(
        "/api/v1/me/followed-venues",
        json={"venue_ids": ["not-a-uuid"]},
        headers=hdrs(),
    )
    assert resp.status_code == 422


def test_unfollow_venue_returns_204(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _u, hdrs = authed_client
    monkeypatch.setattr(
        onboarding_route.follows_service, "unfollow_venue", lambda *_a, **_k: None
    )
    resp = client.delete(f"/api/v1/me/followed-venues/{uuid.uuid4()}", headers=hdrs())
    assert resp.status_code == 204


def test_list_followed_venues_paginated(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _u, hdrs = authed_client
    monkeypatch.setattr(
        onboarding_route.follows_service,
        "list_followed_venues",
        lambda *_a, **_k: ([{"id": "v"}], 1),
    )
    resp = client.get("/api/v1/me/followed-venues", headers=hdrs())
    assert resp.status_code == 200
    assert resp.get_json()["meta"]["total"] == 1

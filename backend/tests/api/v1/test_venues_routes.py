"""Route tests for :mod:`backend.api.v1.venues`."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from flask.testing import FlaskClient

from backend.api.v1 import venues as venues_route
from backend.core import auth as auth_module
from backend.core.exceptions import VENUE_NOT_FOUND, NotFoundError
from backend.data.models.users import User
from backend.tests.conftest import mint_knuckles_token


def test_list_venues_rejects_invalid_city_id(
    client: FlaskClient,
) -> None:
    """A non-UUID ``city_id`` returns 422 with a VALIDATION_ERROR code."""
    resp = client.get("/api/v1/venues?city_id=bogus")
    assert resp.status_code == 422
    body = resp.get_json()
    assert body["error"]["code"] == "VALIDATION_ERROR"


def test_list_venues_forwards_filters_and_pagination(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``active_only`` parses as bool; other args flow through unchanged."""
    captured: dict[str, Any] = {}

    def fake_list(_s: Any, **kw: Any) -> tuple[list[Any], int]:
        captured.update(kw)
        return [object()], 1

    monkeypatch.setattr(venues_route.venues_service, "list_venues", fake_list)
    monkeypatch.setattr(
        venues_route.venues_service,
        "serialize_venue_summary",
        lambda _v: {"id": "x"},
    )

    cid = str(uuid.uuid4())
    resp = client.get(
        f"/api/v1/venues?city_id={cid}&region=DMV&active_only=false&page=3&per_page=25"
    )

    assert resp.status_code == 200
    assert captured["city_id"] == uuid.UUID(cid)
    assert captured["region"] == "DMV"
    assert captured["active_only"] is False
    assert captured["page"] == 3
    assert captured["per_page"] == 25


def test_get_venue_embeds_upcoming_events(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Slug lookup fills in serialized upcoming events + count."""
    venue = type("V", (), {"id": uuid.uuid4()})()

    monkeypatch.setattr(
        venues_route.venues_service,
        "get_venue_by_slug",
        lambda _s, _slug: venue,
    )
    monkeypatch.setattr(
        venues_route.events_repo,
        "list_events_by_venue",
        lambda *_a, **_k: ([object(), object()], 2),
    )
    monkeypatch.setattr(
        venues_route.venues_service,
        "serialize_venue",
        lambda _v: {"slug": "black-cat"},
    )
    monkeypatch.setattr(
        venues_route.events_service,
        "serialize_event_summary",
        lambda _e: {"title": "Show"},
    )

    resp = client.get("/api/v1/venues/black-cat")
    assert resp.status_code == 200
    body = resp.get_json()["data"]
    assert body["slug"] == "black-cat"
    assert body["upcoming_event_count"] == 2
    assert len(body["upcoming_events"]) == 2


def test_get_venue_not_found(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing venue surfaces as 404 with VENUE_NOT_FOUND."""

    def boom(*_a: Any, **_k: Any) -> None:
        raise NotFoundError(VENUE_NOT_FOUND, "nope")

    monkeypatch.setattr(venues_route.venues_service, "get_venue_by_slug", boom)
    resp = client.get("/api/v1/venues/missing")
    assert resp.status_code == 404
    assert resp.get_json()["error"]["code"] == VENUE_NOT_FOUND


# ---------------------------------------------------------------------------
# GET /venues/<slug>/tips
# ---------------------------------------------------------------------------


def _tip_payload(**overrides: Any) -> dict[str, Any]:
    """Build a serialized tip dict for stubbed service returns.

    Args:
        **overrides: Keys to override in the default payload.

    Returns:
        A plain dict shaped like what list_tips_for_venue returns.
    """
    base: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "venue_id": str(uuid.uuid4()),
        "place_name": "El Taco Lab",
        "place_address": "2000 14th St NW, Washington, DC",
        "latitude": 38.917,
        "longitude": -77.032,
        "category": "food",
        "body": "Best tacos before the show.",
        "likes": 5,
        "dislikes": 0,
        "viewer_vote": None,
        "created_at": "2026-04-20T20:00:00+00:00",
        "suppressed": False,
        "distance_from_venue_m": 120,
    }
    base.update(overrides)
    return base


def test_list_venue_tips_returns_envelope(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Happy path — service output flows through the standard envelope."""
    venue = type("V", (), {"id": uuid.uuid4()})()
    captured: dict[str, Any] = {}

    def _fake_list(_s: Any, **kwargs: Any) -> list[dict[str, Any]]:
        captured.update(kwargs)
        return [_tip_payload(), _tip_payload()]

    monkeypatch.setattr(
        venues_route.venues_service, "get_venue_by_slug", lambda _s, _slug: venue
    )
    monkeypatch.setattr(venues_route.map_rec_service, "list_tips_for_venue", _fake_list)

    resp = client.get("/api/v1/venues/black-cat/tips?category=food&limit=50")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["meta"] == {"count": 2}
    assert len(body["data"]) == 2
    assert captured["venue"] is venue
    assert captured["category"] == "food"
    assert captured["limit"] == 50
    assert captured["viewer_user_id"] is None
    assert captured["viewer_session_id"] is None


def test_list_venue_tips_passes_session_id_for_guest(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A signed-out caller's ``session_id`` query arg flows through."""
    venue = type("V", (), {"id": uuid.uuid4()})()
    captured: dict[str, Any] = {}

    def _fake_list(_s: Any, **kwargs: Any) -> list[dict[str, Any]]:
        captured.update(kwargs)
        return []

    monkeypatch.setattr(
        venues_route.venues_service, "get_venue_by_slug", lambda _s, _slug: venue
    )
    monkeypatch.setattr(venues_route.map_rec_service, "list_tips_for_venue", _fake_list)

    resp = client.get("/api/v1/venues/black-cat/tips?session_id=guest-abc")
    assert resp.status_code == 200
    assert captured["viewer_session_id"] == "guest-abc"
    assert captured["viewer_user_id"] is None


def test_list_venue_tips_uses_user_when_authed(
    client: FlaskClient,
    monkeypatch: pytest.MonkeyPatch,
    knuckles_test_key: rsa.RSAPrivateKey,
    stub_knuckles_jwks: str,
) -> None:
    """With a valid bearer token, viewer_user_id wins over session_id."""
    venue = type("V", (), {"id": uuid.uuid4()})()
    captured: dict[str, Any] = {}

    def _fake_list(_s: Any, **kwargs: Any) -> list[dict[str, Any]]:
        captured.update(kwargs)
        return []

    stub = User(
        id=uuid.uuid4(),
        email="pat@example.test",
        display_name="Pat",
        is_active=True,
    )
    monkeypatch.setattr(
        venues_route.users_repo, "get_user_by_id", lambda _s, _uid: stub
    )
    monkeypatch.setattr(auth_module.users_repo, "get_user_by_id", lambda _s, _uid: stub)
    monkeypatch.setattr(
        venues_route.venues_service, "get_venue_by_slug", lambda _s, _slug: venue
    )
    monkeypatch.setattr(venues_route.map_rec_service, "list_tips_for_venue", _fake_list)

    token = mint_knuckles_token(
        signing_key=knuckles_test_key,
        kid=stub_knuckles_jwks,
        user_id=stub.id,
        email=stub.email,
    )
    resp = client.get(
        "/api/v1/venues/black-cat/tips?session_id=guest-abc",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert captured["viewer_user_id"] == stub.id
    assert captured["viewer_session_id"] is None


def test_list_venue_tips_venue_not_found(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing venue surfaces as 404 with VENUE_NOT_FOUND."""

    def boom(*_a: Any, **_k: Any) -> None:
        raise NotFoundError(VENUE_NOT_FOUND, "nope")

    monkeypatch.setattr(venues_route.venues_service, "get_venue_by_slug", boom)
    resp = client.get("/api/v1/venues/missing/tips")
    assert resp.status_code == 404
    assert resp.get_json()["error"]["code"] == VENUE_NOT_FOUND

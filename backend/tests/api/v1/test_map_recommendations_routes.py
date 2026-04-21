"""Route tests for :mod:`backend.api.v1.map_recommendations`.

These cover the HTTP contract — query/body parsing, envelopes, auth
gating, and error propagation — with the service layer stubbed. The
service itself is exercised in ``tests/services/test_map_recommendations.py``
and the repo in ``tests/data/test_map_recommendations_repo.py``.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import pytest
from flask.testing import FlaskClient

from backend.api.v1 import map_recommendations as route
from backend.core.exceptions import (
    PLACE_NOT_VERIFIED,
    AppError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
    ValidationError,
)
from backend.data.models.users import User


def _rec_payload(**overrides: Any) -> dict[str, Any]:
    """Build a serialized recommendation dict for stubbed service returns.

    Args:
        **overrides: Keys to override in the default payload.

    Returns:
        A plain dict shaped like what the service layer returns.
    """
    base: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "place_name": "The Gibson",
        "place_address": "2009 14th St NW, Washington, DC",
        "latitude": 38.9195,
        "longitude": -77.0319,
        "category": "drinks",
        "body": "Great cocktails after a show.",
        "likes": 3,
        "dislikes": 0,
        "viewer_vote": None,
        "created_at": "2026-04-20T20:00:00+00:00",
        "suppressed": False,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# GET /maps/recommendations
# ---------------------------------------------------------------------------


def test_list_recommendations_returns_envelope_with_count(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Happy path — service output flows through the standard envelope."""
    captured: dict[str, Any] = {}

    def _fake_list(_session: Any, **kwargs: Any) -> list[dict[str, Any]]:
        captured.update(kwargs)
        return [_rec_payload()]

    monkeypatch.setattr(route.service, "list_recommendations", _fake_list)
    resp = client.get(
        "/api/v1/maps/recommendations"
        "?sw_lat=38.8&sw_lng=-77.1&ne_lat=38.95&ne_lng=-77.0"
    )

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["meta"] == {"count": 1}
    assert len(body["data"]) == 1
    assert body["data"][0]["place_name"] == "The Gibson"
    assert captured["sw_lat"] == pytest.approx(38.8)
    assert captured["ne_lng"] == pytest.approx(-77.0)
    assert captured["viewer_user_id"] is None
    assert captured["viewer_session_id"] is None


def test_list_recommendations_forwards_filters_and_session_id(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def _fake_list(_session: Any, **kwargs: Any) -> list[dict[str, Any]]:
        captured.update(kwargs)
        return []

    monkeypatch.setattr(route.service, "list_recommendations", _fake_list)
    resp = client.get(
        "/api/v1/maps/recommendations"
        "?sw_lat=38.8&sw_lng=-77.1&ne_lat=38.95&ne_lng=-77.0"
        "&category=drinks&sort=new&limit=25&session_id=guest-abc"
    )

    assert resp.status_code == 200
    assert captured["category"] == "drinks"
    assert captured["sort"] == "new"
    assert captured["limit"] == 25
    assert captured["viewer_session_id"] == "guest-abc"


def test_list_recommendations_rejects_missing_bbox(client: FlaskClient) -> None:
    """A request missing any bbox corner is a 422, not a silent default."""
    resp = client.get("/api/v1/maps/recommendations?sw_lat=38.8&sw_lng=-77.1")
    assert resp.status_code == 422
    assert resp.get_json()["error"]["code"] == "VALIDATION_ERROR"


def test_list_recommendations_rejects_non_numeric_bbox(client: FlaskClient) -> None:
    resp = client.get(
        "/api/v1/maps/recommendations?sw_lat=abc&sw_lng=-77.1&ne_lat=38.95&ne_lng=-77.0"
    )
    assert resp.status_code == 422
    assert resp.get_json()["error"]["code"] == "VALIDATION_ERROR"


def test_list_recommendations_clamps_limit(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Requesting more than the route max still gets clamped to 200."""
    captured: dict[str, Any] = {}

    def _fake_list(_session: Any, **kwargs: Any) -> list[dict[str, Any]]:
        captured.update(kwargs)
        return []

    monkeypatch.setattr(route.service, "list_recommendations", _fake_list)
    resp = client.get(
        "/api/v1/maps/recommendations"
        "?sw_lat=38.8&sw_lng=-77.1&ne_lat=38.95&ne_lng=-77.0&limit=1000"
    )

    assert resp.status_code == 200
    assert captured["limit"] == 200


def test_list_recommendations_rejects_negative_limit(client: FlaskClient) -> None:
    resp = client.get(
        "/api/v1/maps/recommendations"
        "?sw_lat=38.8&sw_lng=-77.1&ne_lat=38.95&ne_lng=-77.0&limit=-5"
    )
    assert resp.status_code == 422


def test_list_recommendations_passes_viewer_user_id_when_authed(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Signed-in callers populate viewer_user_id, not viewer_session_id."""
    client, stub, headers = authed_client
    captured: dict[str, Any] = {}

    def _fake_list(_session: Any, **kwargs: Any) -> list[dict[str, Any]]:
        captured.update(kwargs)
        return []

    monkeypatch.setattr(route.service, "list_recommendations", _fake_list)
    resp = client.get(
        "/api/v1/maps/recommendations"
        "?sw_lat=38.8&sw_lng=-77.1&ne_lat=38.95&ne_lng=-77.0"
        "&session_id=ignored-when-authed",
        headers=headers(),
    )

    assert resp.status_code == 200
    assert captured["viewer_user_id"] == stub.id
    assert captured["viewer_session_id"] is None


def test_list_recommendations_propagates_service_validation(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An inverted bbox raises at the service and surfaces as 422."""

    def _raise(_session: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        raise ValidationError("Bounding box SW corner must be below/left of NE.")

    monkeypatch.setattr(route.service, "list_recommendations", _raise)
    resp = client.get(
        "/api/v1/maps/recommendations"
        "?sw_lat=38.95&sw_lng=-77.0&ne_lat=38.8&ne_lng=-77.1"
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /maps/recommendations
# ---------------------------------------------------------------------------


def _submit_body(**overrides: Any) -> dict[str, Any]:
    """Build a default submit payload for the POST route.

    Args:
        **overrides: Keys to override in the default payload.

    Returns:
        A JSON-serializable dict with a valid default shape.
    """
    base: dict[str, Any] = {
        "query": "The Gibson",
        "by": "name",
        "lat": 38.917,
        "lng": -77.032,
        "category": "drinks",
        "body": "Great cocktails after a show.",
        "session_id": "guest-abc",
    }
    base.update(overrides)
    return base


def test_submit_recommendation_returns_201_with_envelope(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}
    payload = _rec_payload()

    def _fake_submit(_session: Any, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return payload

    monkeypatch.setattr(route.service, "submit_recommendation", _fake_submit)
    resp = client.post("/api/v1/maps/recommendations", json=_submit_body())

    assert resp.status_code == 201
    assert resp.get_json() == {"data": payload}
    assert captured["query"] == "The Gibson"
    assert captured["by"] == "name"
    assert captured["near_latitude"] == pytest.approx(38.917)
    assert captured["category"] == "drinks"
    assert captured["user"] is None
    assert captured["session_id"] == "guest-abc"
    assert captured["ip_hash"]  # something non-empty


def test_submit_recommendation_uses_user_not_session_when_authed(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, stub, headers = authed_client
    captured: dict[str, Any] = {}

    def _fake_submit(_session: Any, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return _rec_payload()

    monkeypatch.setattr(route.service, "submit_recommendation", _fake_submit)
    resp = client.post(
        "/api/v1/maps/recommendations",
        json=_submit_body(),
        headers=headers(),
    )

    assert resp.status_code == 201
    assert captured["user"] is stub
    assert captured["session_id"] is None


def test_submit_recommendation_requires_json_body(client: FlaskClient) -> None:
    resp = client.post(
        "/api/v1/maps/recommendations",
        data="not-json",
        content_type="application/json",
    )
    assert resp.status_code == 422
    assert resp.get_json()["error"]["code"] == "VALIDATION_ERROR"


def test_submit_recommendation_rejects_missing_required_fields(
    client: FlaskClient,
) -> None:
    resp = client.post(
        "/api/v1/maps/recommendations",
        json={"by": "name", "category": "drinks"},
    )
    assert resp.status_code == 422


def test_submit_recommendation_propagates_place_not_verified(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise(_session: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AppError(
            code=PLACE_NOT_VERIFIED,
            message="Apple could not verify that place.",
            status_code=404,
        )

    monkeypatch.setattr(route.service, "submit_recommendation", _raise)
    resp = client.post("/api/v1/maps/recommendations", json=_submit_body())

    assert resp.status_code == 404
    assert resp.get_json()["error"]["code"] == PLACE_NOT_VERIFIED


def test_submit_recommendation_returns_401_when_service_rejects_unauthed(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise(_session: Any, **_kwargs: Any) -> dict[str, Any]:
        raise UnauthorizedError("You must sign in or have a session to post.")

    monkeypatch.setattr(route.service, "submit_recommendation", _raise)
    resp = client.post(
        "/api/v1/maps/recommendations", json=_submit_body(session_id=None)
    )

    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /maps/recommendations/<id>/vote
# ---------------------------------------------------------------------------


def test_vote_returns_envelope_with_counts(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec_id = uuid.uuid4()
    result = {"likes": 2, "dislikes": 0, "viewer_vote": 1, "suppressed": False}
    captured: dict[str, Any] = {}

    def _fake_vote(_session: Any, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return result

    monkeypatch.setattr(route.service, "cast_vote", _fake_vote)
    resp = client.post(
        f"/api/v1/maps/recommendations/{rec_id}/vote",
        json={"value": 1, "session_id": "guest-abc"},
    )

    assert resp.status_code == 200
    assert resp.get_json() == {"data": result}
    assert captured["recommendation_id"] == rec_id
    assert captured["value"] == 1
    assert captured["user"] is None
    assert captured["session_id"] == "guest-abc"


def test_vote_rejects_non_uuid_path(client: FlaskClient) -> None:
    resp = client.post(
        "/api/v1/maps/recommendations/not-a-uuid/vote",
        json={"value": 1, "session_id": "guest-abc"},
    )
    assert resp.status_code == 422


def test_vote_rejects_out_of_range_value(client: FlaskClient) -> None:
    resp = client.post(
        f"/api/v1/maps/recommendations/{uuid.uuid4()}/vote",
        json={"value": 2, "session_id": "guest-abc"},
    )
    assert resp.status_code == 422


def test_vote_rejects_boolean_value(client: FlaskClient) -> None:
    """True passes isinstance(int) but is not a legal vote value."""
    resp = client.post(
        f"/api/v1/maps/recommendations/{uuid.uuid4()}/vote",
        json={"value": True, "session_id": "guest-abc"},
    )
    assert resp.status_code == 422


def test_vote_404s_when_service_raises_not_found(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise(_session: Any, **_kwargs: Any) -> dict[str, Any]:
        raise NotFoundError(
            code="RECOMMENDATION_NOT_FOUND",
            message="No recommendation with that id.",
        )

    monkeypatch.setattr(route.service, "cast_vote", _raise)
    resp = client.post(
        f"/api/v1/maps/recommendations/{uuid.uuid4()}/vote",
        json={"value": 1, "session_id": "guest-abc"},
    )
    assert resp.status_code == 404


def test_vote_passes_user_when_authed(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, stub, headers = authed_client
    captured: dict[str, Any] = {}

    def _fake_vote(_session: Any, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"likes": 1, "dislikes": 0, "viewer_vote": 1, "suppressed": False}

    monkeypatch.setattr(route.service, "cast_vote", _fake_vote)
    resp = client.post(
        f"/api/v1/maps/recommendations/{uuid.uuid4()}/vote",
        json={"value": 1},
        headers=headers(),
    )

    assert resp.status_code == 200
    assert captured["user"] is stub
    assert captured["session_id"] is None


# ---------------------------------------------------------------------------
# DELETE /maps/recommendations/<id>
# ---------------------------------------------------------------------------


def test_delete_recommendation_requires_auth(client: FlaskClient) -> None:
    """No bearer token → 401 before the service ever runs."""
    resp = client.delete(f"/api/v1/maps/recommendations/{uuid.uuid4()}")
    assert resp.status_code == 401


def test_delete_recommendation_returns_204_on_success(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, stub, headers = authed_client
    captured: dict[str, Any] = {}

    def _fake_delete(_session: Any, **kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(route.service, "delete_recommendation", _fake_delete)
    rec_id = uuid.uuid4()
    resp = client.delete(f"/api/v1/maps/recommendations/{rec_id}", headers=headers())

    assert resp.status_code == 204
    assert resp.data == b""
    assert captured["recommendation_id"] == rec_id
    assert captured["user"] is stub


def test_delete_recommendation_rejects_non_uuid(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
) -> None:
    client, _stub, headers = authed_client
    resp = client.delete("/api/v1/maps/recommendations/not-a-uuid", headers=headers())
    assert resp.status_code == 422


def test_delete_recommendation_forwards_forbidden_from_service(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _stub, headers = authed_client

    def _raise(_session: Any, **_kwargs: Any) -> None:
        raise ForbiddenError("You are not the author of this recommendation.")

    monkeypatch.setattr(route.service, "delete_recommendation", _raise)
    resp = client.delete(
        f"/api/v1/maps/recommendations/{uuid.uuid4()}", headers=headers()
    )
    assert resp.status_code == 403


def test_delete_recommendation_forwards_not_found(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _stub, headers = authed_client

    def _raise(_session: Any, **_kwargs: Any) -> None:
        raise NotFoundError(
            code="RECOMMENDATION_NOT_FOUND",
            message="No recommendation with that id.",
        )

    monkeypatch.setattr(route.service, "delete_recommendation", _raise)
    resp = client.delete(
        f"/api/v1/maps/recommendations/{uuid.uuid4()}", headers=headers()
    )
    assert resp.status_code == 404

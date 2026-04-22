"""Route tests for :mod:`backend.api.v1.maps`.

These cover the non-venue-scoped map endpoints — place verification
and lat/lng-based nearby search. The venue-scoped surface
(``/maps/token``, ``/venues/<slug>/map-snapshot``,
``/venues/<slug>/nearby``) lives under
``backend.api.v1.apple_maps`` and is exercised in
``test_apple_maps_routes.py``.

Service-layer behavior is stubbed so these tests stay focused on
the HTTP contract: query-string parsing, JSON envelopes, and error
propagation.
"""

from __future__ import annotations

from typing import Any

import pytest
from flask.testing import FlaskClient

from backend.api.v1 import maps as route
from backend.core.exceptions import APPLE_MAPS_UNAVAILABLE, AppError
from backend.services.apple_maps import NearbyPlace, VerifiedPlace

# ---------------------------------------------------------------------------
# GET /maps/places/nearby
# ---------------------------------------------------------------------------


def test_nearby_places_returns_serialized_dataclasses(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Service dataclasses serialize through the JSON envelope as plain dicts."""
    captured: dict[str, Any] = {}

    def _fake_search(**kwargs: Any) -> list[NearbyPlace]:
        captured.update(kwargs)
        return [
            NearbyPlace(
                name="The Gibson",
                category="Bar",
                address="2009 14th St NW, Washington, DC",
                latitude=38.9195,
                longitude=-77.0319,
                distance_m=120,
            )
        ]

    monkeypatch.setattr(route.service, "search_nearby_places", _fake_search)
    resp = client.get("/api/v1/maps/places/nearby?lat=38.917&lng=-77.032")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body == {
        "data": [
            {
                "name": "The Gibson",
                "category": "Bar",
                "address": "2009 14th St NW, Washington, DC",
                "latitude": 38.9195,
                "longitude": -77.0319,
                "distance_m": 120,
            }
        ],
        "meta": {"count": 1},
    }
    assert captured["latitude"] == pytest.approx(38.917)
    assert captured["longitude"] == pytest.approx(-77.032)


def test_nearby_places_parses_categories_radius_and_limit(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def _fake_search(**kwargs: Any) -> list[NearbyPlace]:
        captured.update(kwargs)
        return []

    monkeypatch.setattr(route.service, "search_nearby_places", _fake_search)
    resp = client.get(
        "/api/v1/maps/places/nearby"
        "?lat=38.9&lng=-77.0&categories=Bar,Cafe&radius_m=300&limit=5"
    )

    assert resp.status_code == 200
    assert captured["categories"] == ("Bar", "Cafe")
    assert captured["radius_m"] == 300
    assert captured["limit"] == 5


def test_nearby_places_rejects_missing_coordinates(client: FlaskClient) -> None:
    """A request without lat/lng is a 400, not a silent fallback to (0, 0)."""
    resp = client.get("/api/v1/maps/places/nearby")
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "INVALID_REQUEST"


def test_nearby_places_propagates_unavailable_error(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fake_search(**_kwargs: Any) -> list[NearbyPlace]:
        raise AppError(
            code=APPLE_MAPS_UNAVAILABLE,
            message="Apple Maps is not configured on this environment.",
            status_code=503,
        )

    monkeypatch.setattr(route.service, "search_nearby_places", _fake_search)
    resp = client.get("/api/v1/maps/places/nearby?lat=38.9&lng=-77.0")

    assert resp.status_code == 503
    assert resp.get_json()["error"]["code"] == APPLE_MAPS_UNAVAILABLE


# ---------------------------------------------------------------------------
# GET /maps/places/verify
# ---------------------------------------------------------------------------


def test_verify_place_by_name_returns_serialized_payload(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def _fake_verify_name(**kwargs: Any) -> VerifiedPlace:
        captured.update(kwargs)
        return VerifiedPlace(
            name="Black Cat",
            address="1811 14th St NW, Washington, DC",
            latitude=38.9152,
            longitude=-77.0316,
            similarity=0.97,
        )

    monkeypatch.setattr(route.service, "verify_place_by_name", _fake_verify_name)
    resp = client.get(
        "/api/v1/maps/places/verify?by=name&q=Black+Cat&lat=38.917&lng=-77.032"
    )

    assert resp.status_code == 200
    body = resp.get_json()
    assert body == {
        "data": {
            "name": "Black Cat",
            "address": "1811 14th St NW, Washington, DC",
            "latitude": 38.9152,
            "longitude": -77.0316,
            "similarity": 0.97,
        }
    }
    assert captured["query"] == "Black Cat"
    assert captured["near_latitude"] == pytest.approx(38.917)
    assert captured["near_longitude"] == pytest.approx(-77.032)


def test_verify_place_by_address_returns_serialized_payload(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def _fake_verify_address(**kwargs: Any) -> VerifiedPlace:
        captured.update(kwargs)
        return VerifiedPlace(
            name="1811 14th St NW",
            address="1811 14th St NW, Washington, DC",
            latitude=38.9152,
            longitude=-77.0316,
            similarity=0.91,
        )

    monkeypatch.setattr(route.service, "verify_place_by_address", _fake_verify_address)
    resp = client.get(
        "/api/v1/maps/places/verify?by=address&q=1811+14th+St+NW+Washington+DC"
    )

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["data"]["similarity"] == 0.91
    assert captured["query"] == "1811 14th St NW Washington DC"


def test_verify_place_returns_404_when_apple_has_no_match(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Apple-no-match is not a 200-with-null — it's a verifier-rejected 404."""
    monkeypatch.setattr(
        route.service,
        "verify_place_by_name",
        lambda **_kwargs: None,
    )
    resp = client.get("/api/v1/maps/places/verify?by=name&q=Nope&lat=38.9&lng=-77.0")

    assert resp.status_code == 404
    assert resp.get_json()["error"]["code"] == "PLACE_NOT_VERIFIED"


def test_verify_place_rejects_unknown_by(client: FlaskClient) -> None:
    """`by` must be `name` or `address`; anything else is a 400."""
    resp = client.get("/api/v1/maps/places/verify?by=phone&q=anything")
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "INVALID_REQUEST"


def test_verify_place_by_name_requires_anchor_coordinates(
    client: FlaskClient,
) -> None:
    """Name verification needs a search anchor; the route enforces both."""
    resp = client.get("/api/v1/maps/places/verify?by=name&q=Black+Cat")
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "INVALID_REQUEST"


def test_verify_place_requires_query(client: FlaskClient) -> None:
    """A blank `q` is rejected before we even hit the service."""
    resp = client.get("/api/v1/maps/places/verify?by=name&q=&lat=38.9&lng=-77.0")
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "INVALID_REQUEST"


def test_verify_place_propagates_unavailable_error(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fake_verify(**_kwargs: Any) -> VerifiedPlace | None:
        raise AppError(
            code=APPLE_MAPS_UNAVAILABLE,
            message="Apple Maps is not configured on this environment.",
            status_code=503,
        )

    monkeypatch.setattr(route.service, "verify_place_by_name", _fake_verify)
    resp = client.get(
        "/api/v1/maps/places/verify?by=name&q=Black+Cat&lat=38.9&lng=-77.0"
    )
    assert resp.status_code == 503
    assert resp.get_json()["error"]["code"] == APPLE_MAPS_UNAVAILABLE


# ---------------------------------------------------------------------------
# GET /maps/tonight
# ---------------------------------------------------------------------------


def _tonight_envelope(*, count: int = 1, date: str = "2026-04-20") -> dict[str, Any]:
    """Return a fake envelope with ``count`` identical pin rows.

    Args:
        count: Number of pin rows to include.
        date: ISO date string for ``meta.date``.

    Returns:
        A service-shaped envelope dict the route can return verbatim.
    """
    row = {
        "id": "11111111-1111-1111-1111-111111111111",
        "slug": "phoebe-bridgers-930-club",
        "title": "Phoebe Bridgers",
        "starts_at": "2026-04-20T20:00:00+00:00",
        "artists": ["Phoebe Bridgers"],
        "genres": ["indie"],
        "image_url": "https://example.test/img.jpg",
        "ticket_url": "https://tickets.test/x",
        "min_price": 35.0,
        "max_price": 65.0,
        "venue": {
            "id": "22222222-2222-2222-2222-222222222222",
            "name": "9:30 Club",
            "slug": "930-club",
            "latitude": 38.9187,
            "longitude": -77.0311,
        },
    }
    return {"data": [row] * count, "meta": {"count": count, "date": date}}


def test_tonight_map_returns_service_envelope(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Route passes the envelope through untouched with a 200."""
    captured: dict[str, Any] = {}

    def _fake_list(_session: Any, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return _tonight_envelope()

    monkeypatch.setattr(route.events_service, "list_tonight_map_events", _fake_list)
    resp = client.get("/api/v1/maps/tonight")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["meta"]["count"] == 1
    assert body["data"][0]["venue"]["latitude"] == 38.9187
    assert captured["genres"] is None


def test_tonight_map_forwards_genres_filter(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A comma-separated `genres` query arg is parsed into a list."""
    captured: dict[str, Any] = {}

    def _fake_list(_session: Any, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return _tonight_envelope(count=0)

    monkeypatch.setattr(route.events_service, "list_tonight_map_events", _fake_list)
    resp = client.get("/api/v1/maps/tonight?genres=indie,punk,indie")

    assert resp.status_code == 200
    assert captured["genres"] == ["indie", "punk"]


def test_tonight_map_treats_blank_genres_as_no_filter(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def _fake_list(_session: Any, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return _tonight_envelope(count=0)

    monkeypatch.setattr(route.events_service, "list_tonight_map_events", _fake_list)
    resp = client.get("/api/v1/maps/tonight?genres=%20%20")

    assert resp.status_code == 200
    assert captured["genres"] is None


def test_tonight_map_is_public(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No auth header required — the map is a public surface."""
    monkeypatch.setattr(
        route.events_service,
        "list_tonight_map_events",
        lambda _s, **_k: _tonight_envelope(count=0, date="2026-04-20"),
    )
    resp = client.get("/api/v1/maps/tonight")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /maps/near-me
# ---------------------------------------------------------------------------


def _near_me_envelope(*, count: int = 1) -> dict[str, Any]:
    """Return a fake near-me envelope with ``count`` identical rows.

    Args:
        count: Number of rows to include.

    Returns:
        A service-shaped envelope dict the route can return verbatim.
    """
    row = {
        "id": "11111111-1111-1111-1111-111111111111",
        "slug": "phoebe-bridgers-930-club",
        "title": "Phoebe Bridgers",
        "starts_at": "2026-04-22T20:00:00+00:00",
        "artists": ["Phoebe Bridgers"],
        "genres": ["indie"],
        "image_url": None,
        "ticket_url": None,
        "min_price": 35.0,
        "max_price": 65.0,
        "venue": {
            "id": "22222222-2222-2222-2222-222222222222",
            "name": "9:30 Club",
            "slug": "930-club",
            "latitude": 38.9187,
            "longitude": -77.0311,
        },
        "distance_km": 1.23,
    }
    return {
        "data": [row] * count,
        "meta": {
            "count": count,
            "center": {"latitude": 38.9072, "longitude": -77.0369},
            "radius_km": 10.0,
            "window": "tonight",
            "date_from": "2026-04-22",
            "date_to": "2026-04-22",
        },
    }


def test_near_me_forwards_coords_radius_and_window(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Happy-path parse: lat, lng, radius_km, and window reach the service."""
    captured: dict[str, Any] = {}

    def _fake_list(_session: Any, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return _near_me_envelope()

    monkeypatch.setattr(route.events_service, "list_events_near", _fake_list)
    resp = client.get(
        "/api/v1/maps/near-me"
        "?lat=38.9072&lng=-77.0369&radius_km=7.5&window=week&limit=25"
    )

    assert resp.status_code == 200
    assert captured["latitude"] == pytest.approx(38.9072)
    assert captured["longitude"] == pytest.approx(-77.0369)
    assert captured["radius_km"] == pytest.approx(7.5)
    assert captured["window"] == "week"
    assert captured["limit"] == 25


def test_near_me_applies_defaults_when_optional_args_missing(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only lat/lng are required; the rest get sensible defaults."""
    captured: dict[str, Any] = {}

    def _fake_list(_session: Any, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return _near_me_envelope(count=0)

    monkeypatch.setattr(route.events_service, "list_events_near", _fake_list)
    resp = client.get("/api/v1/maps/near-me?lat=38.9&lng=-77.0")

    assert resp.status_code == 200
    assert captured["radius_km"] == pytest.approx(10.0)
    assert captured["window"] == "tonight"
    assert captured["limit"] == 50


def test_near_me_rejects_missing_coordinates(client: FlaskClient) -> None:
    """Both lat and lng are required; omitting either is a 400."""
    resp = client.get("/api/v1/maps/near-me")
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "INVALID_REQUEST"


def test_near_me_rejects_non_numeric_coords(client: FlaskClient) -> None:
    """Malformed lat/lng surfaces as 400, not 500."""
    resp = client.get("/api/v1/maps/near-me?lat=here&lng=-77.0")
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "INVALID_REQUEST"


def test_near_me_clamps_radius_and_limit(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Radius and limit are clamped at the route boundary, not the service."""
    captured: dict[str, Any] = {}

    def _fake_list(_session: Any, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return _near_me_envelope(count=0)

    monkeypatch.setattr(route.events_service, "list_events_near", _fake_list)
    resp = client.get(
        "/api/v1/maps/near-me?lat=38.9&lng=-77.0&radius_km=9999&limit=9999"
    )

    assert resp.status_code == 200
    # Clamped to the max values defined in maps.py.
    assert captured["radius_km"] <= 100.0
    assert captured["limit"] <= 100


def test_near_me_propagates_validation_error_from_service(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An invalid window reaching the service is relayed as a 422."""
    from backend.core.exceptions import ValidationError

    def _fake_list(_session: Any, **_kwargs: Any) -> dict[str, Any]:
        raise ValidationError("Invalid window: 'forever'.")

    monkeypatch.setattr(route.events_service, "list_events_near", _fake_list)
    resp = client.get("/api/v1/maps/near-me?lat=38.9&lng=-77.0&window=forever")
    assert resp.status_code == 422
    assert resp.get_json()["error"]["code"] == "VALIDATION_ERROR"


def test_near_me_is_public(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No auth header required — the near-me surface is public."""
    monkeypatch.setattr(
        route.events_service,
        "list_events_near",
        lambda _s, **_k: _near_me_envelope(count=0),
    )
    resp = client.get("/api/v1/maps/near-me?lat=38.9&lng=-77.0")
    assert resp.status_code == 200

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

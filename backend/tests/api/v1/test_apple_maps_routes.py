"""Route tests for :mod:`backend.api.v1.apple_maps`.

These tests exercise the HTTP contract only. The underlying service
is stubbed so no ES256 signing or Redis round-trip happens here —
:mod:`backend.tests.services.test_apple_maps` covers the cryptography
and cache behavior.
"""

from __future__ import annotations

from typing import Any

import pytest
from flask.testing import FlaskClient

from backend.api.v1 import apple_maps as route
from backend.core.exceptions import APPLE_MAPS_UNAVAILABLE, AppError


def test_mapkit_token_returns_service_payload(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Happy path: the service payload is wrapped in ``{"data": ...}``."""
    captured: dict[str, Any] = {}

    def _fake_mint(*, origin: str | None = None) -> dict[str, Any]:
        captured["origin"] = origin
        return {"token": "signed.jwt.here", "expires_at": 1700000000}

    monkeypatch.setattr(route.service, "mint_mapkit_token", _fake_mint)
    resp = client.get("/api/v1/maps/token")

    assert resp.status_code == 200
    assert resp.get_json() == {
        "data": {"token": "signed.jwt.here", "expires_at": 1700000000}
    }
    assert captured["origin"] is None


def test_mapkit_token_forwards_origin_query_param(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def _fake_mint(*, origin: str | None = None) -> dict[str, Any]:
        captured["origin"] = origin
        return {"token": "t", "expires_at": 1}

    monkeypatch.setattr(route.service, "mint_mapkit_token", _fake_mint)
    resp = client.get("/api/v1/maps/token?origin=https://example.test")

    assert resp.status_code == 200
    assert captured["origin"] == "https://example.test"


def test_mapkit_token_treats_empty_origin_as_none(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Browsers that send ``?origin=`` shouldn't bind an empty claim."""
    captured: dict[str, Any] = {}

    def _fake_mint(*, origin: str | None = None) -> dict[str, Any]:
        captured["origin"] = origin
        return {"token": "t", "expires_at": 1}

    monkeypatch.setattr(route.service, "mint_mapkit_token", _fake_mint)
    resp = client.get("/api/v1/maps/token?origin=")

    assert resp.status_code == 200
    assert captured["origin"] is None


def test_mapkit_token_surfaces_unavailable_error(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 503 from the service should propagate verbatim."""

    def _fake_mint(*, origin: str | None = None) -> dict[str, Any]:
        raise AppError(
            code=APPLE_MAPS_UNAVAILABLE,
            message="Apple Maps is not configured on this environment.",
            status_code=503,
        )

    monkeypatch.setattr(route.service, "mint_mapkit_token", _fake_mint)
    resp = client.get("/api/v1/maps/token")

    assert resp.status_code == 503
    body = resp.get_json()
    assert body["error"]["code"] == APPLE_MAPS_UNAVAILABLE


# ---------------------------------------------------------------------------
# GET /venues/<slug>/map-snapshot
# ---------------------------------------------------------------------------


class _StubVenue:
    """Minimal venue stand-in — has just the geocoded attrs the route reads."""

    def __init__(
        self,
        *,
        latitude: float | None = 38.9,
        longitude: float | None = -77.0,
    ) -> None:
        self.latitude = latitude
        self.longitude = longitude


def test_map_snapshot_returns_signed_url(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def _fake_get_venue(_session: Any, slug: str) -> _StubVenue:
        captured["slug"] = slug
        return _StubVenue()

    def _fake_build(**kwargs: Any) -> str:
        captured["build_kwargs"] = kwargs
        return "https://snapshot.apple-mapkit.com/api/v1/snapshot?foo=bar&signature=x"

    monkeypatch.setattr(route.venues_repo, "get_venue_by_slug", _fake_get_venue)
    monkeypatch.setattr(route.service, "build_snapshot_url", _fake_build)

    resp = client.get("/api/v1/venues/black-cat/map-snapshot")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["data"] == {
        "url": "https://snapshot.apple-mapkit.com/api/v1/snapshot?foo=bar&signature=x",
        "width": 600,
        "height": 400,
    }
    assert captured["slug"] == "black-cat"
    assert captured["build_kwargs"]["latitude"] == 38.9
    assert captured["build_kwargs"]["longitude"] == -77.0
    assert captured["build_kwargs"]["width"] == 600
    assert captured["build_kwargs"]["height"] == 400
    assert captured["build_kwargs"]["color_scheme"] == "light"


def test_map_snapshot_forwards_custom_dimensions_and_scheme(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def _fake_build(**kwargs: Any) -> str:
        captured.update(kwargs)
        return "https://snapshot.apple-mapkit.com/api/v1/snapshot?signature=x"

    monkeypatch.setattr(
        route.venues_repo, "get_venue_by_slug", lambda _s, _slug: _StubVenue()
    )
    monkeypatch.setattr(route.service, "build_snapshot_url", _fake_build)

    resp = client.get(
        "/api/v1/venues/black-cat/map-snapshot"
        "?width=320&height=200&zoom=13.5&scheme=dark&label=BC"
    )
    assert resp.status_code == 200
    assert captured["width"] == 320
    assert captured["height"] == 200
    assert captured["zoom"] == 13.5
    assert captured["color_scheme"] == "dark"
    assert captured["annotation_label"] == "BC"


def test_map_snapshot_falls_back_to_defaults_on_bad_numeric_args(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def _fake_build(**kwargs: Any) -> str:
        captured.update(kwargs)
        return "https://snapshot.apple-mapkit.com/api/v1/snapshot?signature=x"

    monkeypatch.setattr(
        route.venues_repo, "get_venue_by_slug", lambda _s, _slug: _StubVenue()
    )
    monkeypatch.setattr(route.service, "build_snapshot_url", _fake_build)

    resp = client.get("/api/v1/venues/black-cat/map-snapshot?width=abc&zoom=xx")
    assert resp.status_code == 200
    assert captured["width"] == 600
    assert captured["zoom"] == 15.0


def test_map_snapshot_404_when_slug_missing(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(route.venues_repo, "get_venue_by_slug", lambda _s, _slug: None)
    resp = client.get("/api/v1/venues/does-not-exist/map-snapshot")
    assert resp.status_code == 404
    assert resp.get_json()["error"]["code"] == "VENUE_NOT_FOUND"


def test_map_snapshot_404_when_venue_has_no_coordinates(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        route.venues_repo,
        "get_venue_by_slug",
        lambda _s, _slug: _StubVenue(latitude=None, longitude=None),
    )
    resp = client.get("/api/v1/venues/black-cat/map-snapshot")
    assert resp.status_code == 404
    assert resp.get_json()["error"]["code"] == "VENUE_NOT_FOUND"


def test_map_snapshot_surfaces_unavailable_error(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise(**_kwargs: Any) -> str:
        raise AppError(
            code=APPLE_MAPS_UNAVAILABLE,
            message="Apple Maps is not configured on this environment.",
            status_code=503,
        )

    monkeypatch.setattr(
        route.venues_repo, "get_venue_by_slug", lambda _s, _slug: _StubVenue()
    )
    monkeypatch.setattr(route.service, "build_snapshot_url", _raise)

    resp = client.get("/api/v1/venues/black-cat/map-snapshot")
    assert resp.status_code == 503
    assert resp.get_json()["error"]["code"] == APPLE_MAPS_UNAVAILABLE


# ---------------------------------------------------------------------------
# GET /venues/<slug>/nearby
# ---------------------------------------------------------------------------


def test_nearby_returns_poi_list_with_count(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Happy path: route wraps the service result in ``{data, meta}``."""
    captured: dict[str, Any] = {}

    def _fake_fetch(**kwargs: Any) -> list[dict[str, Any]]:
        captured.update(kwargs)
        return [
            {
                "name": "Bar Pilar",
                "category": "Bar",
                "address": "1833 14th St NW",
                "latitude": 38.9143,
                "longitude": -77.0321,
                "distance_m": 120,
            },
            {
                "name": "Pearl Dive",
                "category": "Restaurant",
                "address": "1612 14th St NW",
                "latitude": 38.912,
                "longitude": -77.0321,
                "distance_m": 210,
            },
        ]

    monkeypatch.setattr(
        route.venues_repo, "get_venue_by_slug", lambda _s, _slug: _StubVenue()
    )
    monkeypatch.setattr(route.service, "fetch_nearby_poi", _fake_fetch)

    resp = client.get("/api/v1/venues/black-cat/nearby")
    assert resp.status_code == 200
    body = resp.get_json()
    assert len(body["data"]) == 2
    assert body["meta"]["count"] == 2
    assert captured["latitude"] == 38.9
    assert captured["longitude"] == -77.0
    assert captured["categories"] == ("Restaurant", "Bar", "Cafe")
    assert captured["limit"] == 12


def test_nearby_forwards_custom_categories_and_limit(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def _fake_fetch(**kwargs: Any) -> list[dict[str, Any]]:
        captured.update(kwargs)
        return []

    monkeypatch.setattr(
        route.venues_repo, "get_venue_by_slug", lambda _s, _slug: _StubVenue()
    )
    monkeypatch.setattr(route.service, "fetch_nearby_poi", _fake_fetch)

    resp = client.get("/api/v1/venues/black-cat/nearby?categories=Cafe,Bakery&limit=5")
    assert resp.status_code == 200
    assert captured["categories"] == ("Cafe", "Bakery")
    assert captured["limit"] == 5


def test_nearby_empty_categories_arg_falls_back_to_default(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def _fake_fetch(**kwargs: Any) -> list[dict[str, Any]]:
        captured.update(kwargs)
        return []

    monkeypatch.setattr(
        route.venues_repo, "get_venue_by_slug", lambda _s, _slug: _StubVenue()
    )
    monkeypatch.setattr(route.service, "fetch_nearby_poi", _fake_fetch)

    resp = client.get("/api/v1/venues/black-cat/nearby?categories=")
    assert resp.status_code == 200
    assert captured["categories"] == ("Restaurant", "Bar", "Cafe")


def test_nearby_404_when_slug_missing(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(route.venues_repo, "get_venue_by_slug", lambda _s, _slug: None)
    resp = client.get("/api/v1/venues/nope/nearby")
    assert resp.status_code == 404
    assert resp.get_json()["error"]["code"] == "VENUE_NOT_FOUND"


def test_nearby_404_when_venue_has_no_coordinates(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        route.venues_repo,
        "get_venue_by_slug",
        lambda _s, _slug: _StubVenue(latitude=None, longitude=None),
    )
    resp = client.get("/api/v1/venues/black-cat/nearby")
    assert resp.status_code == 404
    assert resp.get_json()["error"]["code"] == "VENUE_NOT_FOUND"


def test_nearby_surfaces_unavailable_error(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise(**_kwargs: Any) -> list[dict[str, Any]]:
        raise AppError(
            code=APPLE_MAPS_UNAVAILABLE,
            message="Apple Maps is not configured on this environment.",
            status_code=503,
        )

    monkeypatch.setattr(
        route.venues_repo, "get_venue_by_slug", lambda _s, _slug: _StubVenue()
    )
    monkeypatch.setattr(route.service, "fetch_nearby_poi", _raise)

    resp = client.get("/api/v1/venues/black-cat/nearby")
    assert resp.status_code == 503
    assert resp.get_json()["error"]["code"] == APPLE_MAPS_UNAVAILABLE

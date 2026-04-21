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

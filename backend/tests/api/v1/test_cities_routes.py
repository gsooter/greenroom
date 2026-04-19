"""Route tests for :mod:`backend.api.v1.cities`."""

from __future__ import annotations

from typing import Any

import pytest
from flask.testing import FlaskClient

from backend.api.v1 import cities as cities_route
from backend.core.exceptions import CITY_NOT_FOUND, NotFoundError


def test_list_cities_passes_region_filter(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def fake_list(_s: Any, *, region: str | None) -> list[Any]:
        captured["region"] = region
        return [object(), object()]

    monkeypatch.setattr(cities_route.cities_service, "list_cities", fake_list)
    monkeypatch.setattr(
        cities_route.cities_service, "serialize_city", lambda _c: {"slug": "x"}
    )

    resp = client.get("/api/v1/cities?region=DMV")
    assert resp.status_code == 200
    body = resp.get_json()
    assert captured["region"] == "DMV"
    assert body["meta"]["total"] == 2
    assert len(body["data"]) == 2


def test_get_city_by_slug(client: FlaskClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cities_route.cities_service,
        "get_city_by_slug",
        lambda _s, slug: {"slug": slug},
    )
    monkeypatch.setattr(cities_route.cities_service, "serialize_city", lambda c: c)
    resp = client.get("/api/v1/cities/washington-dc")
    assert resp.status_code == 200
    assert resp.get_json()["data"] == {"slug": "washington-dc"}


def test_get_city_404(client: FlaskClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: Any, **_k: Any) -> None:
        raise NotFoundError(CITY_NOT_FOUND, "nope")

    monkeypatch.setattr(cities_route.cities_service, "get_city_by_slug", boom)
    resp = client.get("/api/v1/cities/ghost")
    assert resp.status_code == 404
    assert resp.get_json()["error"]["code"] == CITY_NOT_FOUND

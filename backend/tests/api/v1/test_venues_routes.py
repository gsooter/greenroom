"""Route tests for :mod:`backend.api.v1.venues`."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from flask.testing import FlaskClient

from backend.api.v1 import venues as venues_route
from backend.core.exceptions import VENUE_NOT_FOUND, NotFoundError


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
        f"/api/v1/venues?city_id={cid}&region=DMV&active_only=false"
        "&page=3&per_page=25"
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

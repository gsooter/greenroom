"""Route tests for :mod:`backend.api.v1.events`."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from flask.testing import FlaskClient

from backend.api.v1 import events as events_route
from backend.core.exceptions import EVENT_NOT_FOUND, NotFoundError, ValidationError


def _fake_event() -> Any:
    """Return a stub event object; route never inspects the shape directly."""
    return object()


def test_list_events_parses_and_forwards_filters(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every query-string arg reaches the service with the right types."""
    captured: dict[str, Any] = {}

    def fake_list(_session: Any, **kwargs: Any) -> tuple[list[Any], int]:
        captured.update(kwargs)
        return [_fake_event()], 1

    monkeypatch.setattr(events_route.events_service, "list_events", fake_list)
    monkeypatch.setattr(
        events_route.events_service,
        "serialize_event_summary",
        lambda _e: {"id": "x"},
    )

    city_id = str(uuid.uuid4())
    venue_id = str(uuid.uuid4())
    resp = client.get(
        "/api/v1/events"
        f"?city_id={city_id}&region=DMV&venue_id={venue_id}"
        "&date_from=2026-05-01&date_to=2026-05-31"
        "&genre=indie&event_type=concert&status=confirmed"
        "&page=2&per_page=10"
    )

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["meta"] == {
        "total": 1,
        "page": 2,
        "per_page": 10,
        "has_next": False,
    }
    assert captured["city_id"] == uuid.UUID(city_id)
    assert captured["region"] == "DMV"
    assert captured["venue_ids"] == [uuid.UUID(venue_id)]
    assert str(captured["date_from"]) == "2026-05-01"
    assert str(captured["date_to"]) == "2026-05-31"
    assert captured["genres"] == ["indie"]
    assert captured["event_type"] == "concert"
    assert captured["status"] == "confirmed"


def test_list_events_ignores_invalid_uuid_city(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unparseable city_id drops silently to None rather than 400."""
    captured: dict[str, Any] = {}

    def fake_list(_session: Any, **kwargs: Any) -> tuple[list[Any], int]:
        captured.update(kwargs)
        return [], 0

    monkeypatch.setattr(events_route.events_service, "list_events", fake_list)
    resp = client.get("/api/v1/events?city_id=not-a-uuid")
    assert resp.status_code == 200
    assert captured["city_id"] is None


def test_list_events_rejects_malformed_date_as_none(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bad YYYY-MM-DD values become None rather than returning 400."""
    captured: dict[str, Any] = {}

    def fake_list(_session: Any, **kwargs: Any) -> tuple[list[Any], int]:
        captured.update(kwargs)
        return [], 0

    monkeypatch.setattr(events_route.events_service, "list_events", fake_list)
    resp = client.get("/api/v1/events?date_from=tomorrow")
    assert resp.status_code == 200
    assert captured["date_from"] is None


def test_list_events_defaults_date_from_to_today(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Omitted ``date_from`` defaults to today so past events stay hidden."""
    from datetime import date

    captured: dict[str, Any] = {}

    def fake_list(_session: Any, **kwargs: Any) -> tuple[list[Any], int]:
        captured.update(kwargs)
        return [], 0

    monkeypatch.setattr(events_route.events_service, "list_events", fake_list)
    resp = client.get("/api/v1/events?region=DMV")
    assert resp.status_code == 200
    assert captured["date_from"] == date.today()


def test_list_events_surfaces_validation_error(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ValidationError bubbling up from the service → 422 with code."""

    def boom(*_a: Any, **_k: Any) -> None:
        raise ValidationError("per_page too big")

    monkeypatch.setattr(events_route.events_service, "list_events", boom)
    resp = client.get("/api/v1/events?per_page=500")
    assert resp.status_code == 422
    assert resp.get_json()["error"]["code"] == "VALIDATION_ERROR"


def test_get_event_by_uuid(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Valid UUID path delegates to get_event; slug path is not hit."""
    event = _fake_event()
    monkeypatch.setattr(events_route.events_service, "get_event", lambda _s, _i: event)
    monkeypatch.setattr(
        events_route.events_service,
        "get_event_by_slug",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("not slug")),
    )
    monkeypatch.setattr(
        events_route.events_service, "serialize_event", lambda _e: {"id": "x"}
    )
    eid = str(uuid.uuid4())
    resp = client.get(f"/api/v1/events/{eid}")
    assert resp.status_code == 200
    assert resp.get_json()["data"] == {"id": "x"}


def test_get_event_by_slug_falls_through(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-UUID path delegates to get_event_by_slug."""
    monkeypatch.setattr(
        events_route.events_service,
        "get_event_by_slug",
        lambda _s, slug: {"slug": slug},
    )
    monkeypatch.setattr(events_route.events_service, "serialize_event", lambda e: e)
    resp = client.get("/api/v1/events/phoebe-bridgers-930-club-2026-05-01-abc")
    assert resp.status_code == 200
    assert resp.get_json()["data"]["slug"] == "phoebe-bridgers-930-club-2026-05-01-abc"


def test_get_event_not_found(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NotFoundError → 404 with a structured error body."""

    def boom(*_a: Any, **_k: Any) -> None:
        raise NotFoundError(EVENT_NOT_FOUND, "gone")

    monkeypatch.setattr(events_route.events_service, "get_event_by_slug", boom)
    resp = client.get("/api/v1/events/nope-slug")
    assert resp.status_code == 404
    assert resp.get_json()["error"]["code"] == EVENT_NOT_FOUND


def test_event_feed_returns_plain_text(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Feed endpoint returns text/plain and calls the formatter."""
    monkeypatch.setattr(
        events_route.events_service,
        "list_events",
        lambda *_a, **_k: ([_fake_event()], 1),
    )
    monkeypatch.setattr(
        events_route.events_service,
        "format_event_feed",
        lambda events, generated_at: "TONIGHT\n• Phoebe Bridgers @ 9:30 Club",
    )
    resp = client.get("/api/v1/feed/events")
    assert resp.status_code == 200
    assert resp.mimetype == "text/plain"
    assert b"Phoebe Bridgers" in resp.data


def test_event_feed_defaults_region_to_dmv(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No region, no city → DMV is injected on the service call."""
    captured: dict[str, Any] = {}

    def fake_list(_s: Any, **kw: Any) -> tuple[list[Any], int]:
        captured.update(kw)
        return [], 0

    monkeypatch.setattr(events_route.events_service, "list_events", fake_list)
    monkeypatch.setattr(
        events_route.events_service, "format_event_feed", lambda *_a, **_k: ""
    )
    client.get("/api/v1/feed/events")
    assert captured["region"] == "DMV"
    assert captured["city_id"] is None


def test_event_feed_city_override_drops_region(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A city_id in the query should zero out the default region injection."""
    captured: dict[str, Any] = {}

    def fake_list(_s: Any, **kw: Any) -> tuple[list[Any], int]:
        captured.update(kw)
        return [], 0

    monkeypatch.setattr(events_route.events_service, "list_events", fake_list)
    monkeypatch.setattr(
        events_route.events_service, "format_event_feed", lambda *_a, **_k: ""
    )
    cid = str(uuid.uuid4())
    client.get(f"/api/v1/feed/events?city_id={cid}")
    assert captured["city_id"] == uuid.UUID(cid)
    assert captured["region"] is None

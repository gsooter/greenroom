"""Route tests for :mod:`backend.api.v1.events`."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from flask.testing import FlaskClient

from backend.api.v1 import events as events_route
from backend.core.exceptions import EVENT_NOT_FOUND, NotFoundError, ValidationError
from backend.services import tickets as tickets_service


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


def test_list_events_forwards_new_filter_params(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """artist_id, artist_search, price_max, free_only, available_only round-trip."""
    captured: dict[str, Any] = {}

    def fake_list(_session: Any, **kwargs: Any) -> tuple[list[Any], int]:
        captured.update(kwargs)
        return [], 0

    monkeypatch.setattr(events_route.events_service, "list_events", fake_list)
    artist_id = str(uuid.uuid4())
    resp = client.get(
        "/api/v1/events"
        f"?artist_id={artist_id}&artist_search=phoebe"
        "&price_max=45.5&free_only=true&available_only=1"
    )
    assert resp.status_code == 200
    assert captured["artist_ids"] == [uuid.UUID(artist_id)]
    assert captured["artist_search"] == "phoebe"
    assert captured["price_max"] == 45.5
    assert captured["free_only"] is True
    assert captured["available_only"] is True


def test_list_events_bool_flags_default_to_false(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Omitted boolean flags pass as False, not None."""
    captured: dict[str, Any] = {}

    def fake_list(_session: Any, **kwargs: Any) -> tuple[list[Any], int]:
        captured.update(kwargs)
        return [], 0

    monkeypatch.setattr(events_route.events_service, "list_events", fake_list)
    client.get("/api/v1/events")
    assert captured["free_only"] is False
    assert captured["available_only"] is False
    assert captured["price_max"] is None
    assert captured["artist_ids"] is None
    assert captured["artist_search"] is None


def test_list_events_default_sort_is_none_and_user_id_unset(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without ``?sort`` or auth, the route forwards ``sort=None`` and no user_id."""
    captured: dict[str, Any] = {}

    def fake_list(_session: Any, **kwargs: Any) -> tuple[list[Any], int]:
        captured.update(kwargs)
        return [], 0

    monkeypatch.setattr(events_route.events_service, "list_events", fake_list)
    resp = client.get("/api/v1/events")
    assert resp.status_code == 200
    assert captured["sort"] is None
    assert captured["user_id"] is None


def test_list_events_for_you_anonymous_passes_sort_without_user_id(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Anonymous ``?sort=for_you`` reaches the service with user_id=None."""
    captured: dict[str, Any] = {}

    def fake_list(_session: Any, **kwargs: Any) -> tuple[list[Any], int]:
        captured.update(kwargs)
        return [], 0

    monkeypatch.setattr(events_route.events_service, "list_events", fake_list)
    resp = client.get("/api/v1/events?sort=for_you")
    assert resp.status_code == 200
    assert captured["sort"] == "for_you"
    assert captured["user_id"] is None


def test_list_events_for_you_authed_resolves_user_id(
    authed_client: tuple[Any, Any, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A valid bearer token + ``?sort=for_you`` forwards the caller's user_id."""
    auth_client, user, headers = authed_client
    captured: dict[str, Any] = {}

    def fake_list(_session: Any, **kwargs: Any) -> tuple[list[Any], int]:
        captured.update(kwargs)
        return [], 0

    monkeypatch.setattr(events_route.events_service, "list_events", fake_list)
    resp = auth_client.get("/api/v1/events?sort=for_you", headers=headers())
    assert resp.status_code == 200
    assert captured["sort"] == "for_you"
    assert captured["user_id"] == user.id


def test_list_events_advanced_query_params_round_trip(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """day_of_week, time_of_day, has_image, has_price reach the service typed."""
    captured: dict[str, Any] = {}

    def fake_list(_session: Any, **kwargs: Any) -> tuple[list[Any], int]:
        captured.update(kwargs)
        return [], 0

    monkeypatch.setattr(events_route.events_service, "list_events", fake_list)
    resp = client.get(
        "/api/v1/events"
        "?day_of_week=0&day_of_week=6"
        "&time_of_day=evening&time_of_day=late"
        "&has_image=true&has_price=1"
    )
    assert resp.status_code == 200
    assert captured["day_of_week"] == [0, 6]
    assert captured["time_of_day"] == ["evening", "late"]
    assert captured["has_image"] is True
    assert captured["has_price"] is True


def test_list_events_followed_only_resolves_user_when_authed(
    authed_client: tuple[Any, Any, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``followed_venues_only`` triggers user lookup so the filter can apply."""
    auth_client, user, headers = authed_client
    captured: dict[str, Any] = {}

    def fake_list(_session: Any, **kwargs: Any) -> tuple[list[Any], int]:
        captured.update(kwargs)
        return [], 0

    monkeypatch.setattr(events_route.events_service, "list_events", fake_list)
    resp = auth_client.get(
        "/api/v1/events?followed_venues_only=true", headers=headers()
    )
    assert resp.status_code == 200
    assert captured["followed_venues_only"] is True
    assert captured["user_id"] == user.id


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
    monkeypatch.setattr(
        events_route.tickets_service,
        "serialize_pricing_state",
        lambda _s, _e: {"refreshed_at": None, "sources": []},
    )
    eid = str(uuid.uuid4())
    resp = client.get(f"/api/v1/events/{eid}")
    assert resp.status_code == 200
    body = resp.get_json()["data"]
    assert body["id"] == "x"
    assert body["pricing"] == {"refreshed_at": None, "sources": []}


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
    monkeypatch.setattr(
        events_route.tickets_service,
        "serialize_pricing_state",
        lambda _s, _e: {"refreshed_at": None, "sources": []},
    )
    resp = client.get("/api/v1/events/phoebe-bridgers-930-club-2026-05-01-abc")
    assert resp.status_code == 200
    body = resp.get_json()["data"]
    assert body["slug"] == "phoebe-bridgers-930-club-2026-05-01-abc"
    assert body["pricing"]["sources"] == []


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


def test_get_event_pricing_returns_serialized_state(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pricing endpoint returns the merged sources payload directly."""
    event = _fake_event()
    monkeypatch.setattr(events_route.events_service, "get_event", lambda _s, _i: event)
    monkeypatch.setattr(
        events_route.tickets_service,
        "serialize_pricing_state",
        lambda _s, _e: {"refreshed_at": "2026-04-25T14:31:00+00:00", "sources": []},
    )
    eid = str(uuid.uuid4())
    resp = client.get(f"/api/v1/events/{eid}/pricing")
    assert resp.status_code == 200
    assert resp.get_json()["data"]["refreshed_at"] == "2026-04-25T14:31:00+00:00"


def test_get_event_pricing_resolves_slug(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-UUID path falls through to slug lookup."""
    captured: dict[str, Any] = {}

    def fake_by_slug(_s: Any, slug: str) -> Any:
        captured["slug"] = slug
        return _fake_event()

    monkeypatch.setattr(events_route.events_service, "get_event_by_slug", fake_by_slug)
    monkeypatch.setattr(
        events_route.tickets_service,
        "serialize_pricing_state",
        lambda _s, _e: {"refreshed_at": None, "sources": []},
    )
    resp = client.get("/api/v1/events/phoebe-slug/pricing")
    assert resp.status_code == 200
    assert captured["slug"] == "phoebe-slug"


def test_refresh_event_pricing_returns_full_envelope(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST sweeps providers and returns refresh + pricing in one body."""
    from datetime import UTC, datetime

    event = _fake_event()
    monkeypatch.setattr(events_route.events_service, "get_event", lambda _s, _i: event)

    eid = uuid.uuid4()
    refreshed = datetime(2026, 4, 25, 14, 31, tzinfo=UTC)

    def fake_refresh(_s: Any, _e: Any) -> Any:
        return tickets_service.RefreshResult(
            event_id=eid,
            refreshed_at=refreshed,
            cooldown_active=False,
            quotes_persisted=2,
            links_upserted=3,
            provider_errors=("seatgeek",),
        )

    monkeypatch.setattr(
        events_route.tickets_service, "refresh_event_pricing", fake_refresh
    )
    monkeypatch.setattr(
        events_route.tickets_service,
        "serialize_pricing_state",
        lambda _s, _e: {"refreshed_at": refreshed.isoformat(), "sources": []},
    )

    resp = client.post(f"/api/v1/events/{eid}/refresh-pricing")
    assert resp.status_code == 200
    body = resp.get_json()["data"]
    assert body["refresh"]["event_id"] == str(eid)
    assert body["refresh"]["refreshed_at"] == refreshed.isoformat()
    assert body["refresh"]["cooldown_active"] is False
    assert body["refresh"]["quotes_persisted"] == 2
    assert body["refresh"]["links_upserted"] == 3
    assert body["refresh"]["provider_errors"] == ["seatgeek"]
    assert body["pricing"]["refreshed_at"] == refreshed.isoformat()


def test_refresh_event_pricing_propagates_cooldown(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cooldown short-circuit still returns 200 with the flag set true.

    The frontend treats this as a successful "your view is already
    fresh" rather than an error — the data was already persisted.
    """
    from datetime import UTC, datetime

    event = _fake_event()
    monkeypatch.setattr(events_route.events_service, "get_event", lambda _s, _i: event)

    eid = uuid.uuid4()
    refreshed = datetime(2026, 4, 25, 14, 30, tzinfo=UTC)

    def fake_refresh(_s: Any, _e: Any) -> Any:
        return tickets_service.RefreshResult(
            event_id=eid,
            refreshed_at=refreshed,
            cooldown_active=True,
            quotes_persisted=0,
            links_upserted=0,
            provider_errors=(),
        )

    monkeypatch.setattr(
        events_route.tickets_service, "refresh_event_pricing", fake_refresh
    )
    monkeypatch.setattr(
        events_route.tickets_service,
        "serialize_pricing_state",
        lambda _s, _e: {"refreshed_at": refreshed.isoformat(), "sources": []},
    )

    resp = client.post(f"/api/v1/events/{eid}/refresh-pricing")
    assert resp.status_code == 200
    body = resp.get_json()["data"]
    assert body["refresh"]["cooldown_active"] is True
    assert body["refresh"]["quotes_persisted"] == 0


def test_pricing_freshness_returns_max_timestamp(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The freshness endpoint exposes the listing-page banner anchor."""
    from datetime import UTC, datetime

    refreshed = datetime(2026, 4, 26, 9, 0, tzinfo=UTC)
    monkeypatch.setattr(
        events_route.events_repo,
        "get_latest_pricing_refresh",
        lambda _s: refreshed,
    )

    resp = client.get("/api/v1/pricing/freshness")

    assert resp.status_code == 200
    assert resp.get_json()["data"]["refreshed_at"] == refreshed.isoformat()


def test_pricing_freshness_returns_null_when_never_swept(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No upcoming event has been priced → null timestamp, not 404.

    The frontend renders this as "never" rather than treating the
    listing page as broken.
    """
    monkeypatch.setattr(
        events_route.events_repo,
        "get_latest_pricing_refresh",
        lambda _s: None,
    )

    resp = client.get("/api/v1/pricing/freshness")

    assert resp.status_code == 200
    assert resp.get_json()["data"] == {"refreshed_at": None}


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

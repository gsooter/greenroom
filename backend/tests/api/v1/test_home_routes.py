"""Route tests for :mod:`backend.api.v1.home`."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from flask.testing import FlaskClient

from backend.api.v1 import home as home_route
from backend.data.models.users import User


def _stub_recs(monkeypatch: pytest.MonkeyPatch, *, recs: list[Any]) -> None:
    """Replace the recs service with a fixed page of fake recommendations."""
    monkeypatch.setattr(
        home_route.recs_service,
        "list_recommendations_for_user",
        lambda *_a, **_k: (recs, len(recs)),
    )
    monkeypatch.setattr(
        home_route.recs_service,
        "serialize_recommendation",
        lambda r: {"id": getattr(r, "id", "rec"), "score": getattr(r, "score", 0.5)},
    )


def _stub_event_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        home_route.events_service,
        "serialize_event_summary",
        lambda e: {"id": getattr(e, "id", "evt")},
    )


def test_home_returns_full_payload_when_user_has_signal(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """has_signal=True populates recommendations and skips the popularity fallback."""
    client, user, hdrs = authed_client

    rec_a = type("R", (), {"id": "a", "score": 0.9, "event_id": "e1"})()
    rec_b = type("R", (), {"id": "b", "score": 0.8, "event_id": "e2"})()
    rec_c = type("R", (), {"id": "c", "score": 0.7, "event_id": "e3"})()
    rec_d = type("R", (), {"id": "d", "score": 0.6, "event_id": "e4"})()
    _stub_recs(monkeypatch, recs=[rec_a, rec_b, rec_c, rec_d])
    _stub_event_summary(monkeypatch)

    monkeypatch.setattr(home_route.home_service, "has_signal", lambda _s, _u: True)
    monkeypatch.setattr(
        home_route.home_service,
        "get_new_since_last_visit",
        lambda _s, _u: [type("E", (), {"id": "n1"})()],
    )
    enqueued: list[str] = []
    monkeypatch.setattr(
        home_route, "_enqueue_visit_update", lambda uid: enqueued.append(str(uid))
    )

    user.last_home_visit_at = datetime(2026, 5, 1, 12, tzinfo=UTC)

    resp = client.get("/api/v1/me/home", headers=hdrs())
    assert resp.status_code == 200
    body = resp.get_json()["data"]
    assert body["has_signal"] is True
    assert body["last_home_visit_at"] == "2026-05-01T12:00:00+00:00"
    assert len(body["recommendations"]) == 4
    assert body["popularity_fallback"] == []
    assert body["new_since_last_visit"] == [{"id": "n1"}]
    assert enqueued == [str(user.id)]


def test_home_supplements_thin_recs_with_popularity_fallback(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fewer than 4 real recs triggers the "Popular in DC" fallback fill."""
    client, _u, hdrs = authed_client

    rec_a = type("R", (), {"id": "a", "score": 0.9, "event_id": "e1"})()
    _stub_recs(monkeypatch, recs=[rec_a])
    _stub_event_summary(monkeypatch)

    monkeypatch.setattr(home_route.home_service, "has_signal", lambda _s, _u: True)
    monkeypatch.setattr(
        home_route.home_service, "get_new_since_last_visit", lambda _s, _u: []
    )

    fallbacks = [type("E", (), {"id": f"f{i}"})() for i in range(3)]
    monkeypatch.setattr(
        home_route, "_list_popularity_fallback", lambda *_a, **_k: fallbacks
    )
    monkeypatch.setattr(home_route, "_enqueue_visit_update", lambda _u: None)

    resp = client.get("/api/v1/me/home", headers=hdrs())
    body = resp.get_json()["data"]
    assert len(body["recommendations"]) == 1
    assert len(body["popularity_fallback"]) == 3


def test_home_skips_fallback_when_no_signal(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """has_signal=False suppresses the popularity supplement.

    The frontend uses `has_signal` to render the welcome prompt — there
    is no value in surfacing "Popular in DC" rows because the user
    hasn't asked for personalization yet.
    """
    client, _u, hdrs = authed_client

    _stub_recs(monkeypatch, recs=[])
    _stub_event_summary(monkeypatch)

    monkeypatch.setattr(home_route.home_service, "has_signal", lambda _s, _u: False)
    monkeypatch.setattr(
        home_route.home_service, "get_new_since_last_visit", lambda _s, _u: []
    )

    fallback_called = False

    def _spy(*_a: Any, **_k: Any) -> list[Any]:
        nonlocal fallback_called
        fallback_called = True
        return []

    monkeypatch.setattr(home_route, "_list_popularity_fallback", _spy)
    monkeypatch.setattr(home_route, "_enqueue_visit_update", lambda _u: None)

    resp = client.get("/api/v1/me/home", headers=hdrs())
    body = resp.get_json()["data"]
    assert body["has_signal"] is False
    assert body["recommendations"] == []
    assert body["popularity_fallback"] == []
    assert fallback_called is False


def test_enqueue_visit_update_swallows_broker_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broken Celery broker must not bubble up as a 500."""

    class _FailingTask:
        def delay(self, _uid: str) -> None:
            raise RuntimeError("broker down")

    import sys
    import types

    fake_module = types.ModuleType("backend.services.home_tasks")
    fake_module.record_home_visit = _FailingTask()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "backend.services.home_tasks", fake_module)

    # No assertion needed beyond "doesn't raise" — _enqueue_visit_update
    # is wrapped in contextlib.suppress so the route always continues.
    home_route._enqueue_visit_update("00000000-0000-0000-0000-000000000000")

"""Route tests for :mod:`backend.api.v1.feedback`.

Cover the public POST (anonymous + authed) and the admin list/resolve
endpoints. Service calls are monkeypatched so route logic is exercised
in isolation.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import pytest
from flask.testing import FlaskClient

from backend.api.v1 import feedback as feedback_route
from backend.core.config import get_settings
from backend.data.models.feedback import FeedbackKind
from backend.data.models.users import User


def _admin_hdr() -> dict[str, str]:
    return {"X-Admin-Key": get_settings().admin_secret_key}


def _stub_serialized() -> dict[str, Any]:
    """Return a deterministic serialized payload for the route tests."""
    return {
        "id": str(uuid.uuid4()),
        "user_id": None,
        "email": None,
        "message": "hi",
        "kind": "general",
        "page_url": None,
        "user_agent": None,
        "is_resolved": False,
        "created_at": "2026-04-27T12:00:00+00:00",
    }


# ---------------------------------------------------------------------------
# POST /api/v1/feedback
# ---------------------------------------------------------------------------


def test_submit_feedback_anonymous_returns_201(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Anonymous post stores email from the form and returns 201."""
    captured: dict[str, Any] = {}

    def fake_submit(_s: Any, **kwargs: Any) -> Any:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(feedback_route.feedback_service, "submit_feedback", fake_submit)
    monkeypatch.setattr(
        feedback_route.feedback_service,
        "serialize_feedback",
        lambda _r: _stub_serialized(),
    )

    resp = client.post(
        "/api/v1/feedback",
        json={
            "message": "found a bug",
            "kind": "bug",
            "email": "anon@example.com",
            "page_url": "https://greenroom.example/events",
        },
        headers={"User-Agent": "TestUA/1.0"},
    )
    assert resp.status_code == 201
    assert captured["message"] == "found a bug"
    assert captured["kind"] == "bug"
    assert captured["email"] == "anon@example.com"
    assert captured["page_url"] == "https://greenroom.example/events"
    assert captured["user"] is None
    assert captured["user_agent"] == "TestUA/1.0"


def test_submit_feedback_signed_in_passes_user(
    monkeypatch: pytest.MonkeyPatch,
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
) -> None:
    """A bearer token causes the route to forward the User to the service."""
    client, stub_user, headers = authed_client
    captured: dict[str, Any] = {}

    def fake_submit(_s: Any, **kwargs: Any) -> Any:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(feedback_route.feedback_service, "submit_feedback", fake_submit)
    monkeypatch.setattr(
        feedback_route.feedback_service,
        "serialize_feedback",
        lambda _r: _stub_serialized(),
    )

    resp = client.post(
        "/api/v1/feedback",
        json={"message": "love it", "kind": "general"},
        headers=headers(),
    )
    assert resp.status_code == 201
    assert captured["user"] is stub_user


def test_submit_feedback_rejects_missing_message(client: FlaskClient) -> None:
    """A request without a string message returns 422."""
    resp = client.post(
        "/api/v1/feedback",
        json={"kind": "general"},
    )
    assert resp.status_code == 422


def test_submit_feedback_rejects_non_object_payload(client: FlaskClient) -> None:
    """A non-object JSON body returns 422."""
    resp = client.post(
        "/api/v1/feedback",
        json=["bad"],
    )
    assert resp.status_code == 422


def test_submit_feedback_rejects_non_string_email(client: FlaskClient) -> None:
    """Email must be a string when provided."""
    resp = client.post(
        "/api/v1/feedback",
        json={"message": "hi", "kind": "general", "email": 42},
    )
    assert resp.status_code == 422


def test_submit_feedback_enforces_per_ip_rate_limit(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 11th submission from the same IP within an hour is blocked.

    Stubs the service so storage isn't exercised, points the limiter at
    a fake Redis that counts in-process, then fires 10 successful posts
    plus one expected 429.
    """
    from backend.core import rate_limit as rate_limit_module

    monkeypatch.setattr(
        feedback_route.feedback_service,
        "submit_feedback",
        lambda *_a, **_k: object(),
    )
    monkeypatch.setattr(
        feedback_route.feedback_service,
        "serialize_feedback",
        lambda _r: _stub_serialized(),
    )

    counter = _make_keyed_counter()
    monkeypatch.setattr(rate_limit_module, "_get_redis", lambda: counter)

    payload = {"message": "hi", "kind": "general"}
    for i in range(10):
        resp = client.post("/api/v1/feedback", json=payload)
        assert resp.status_code == 201, f"call {i} unexpectedly blocked"

    resp = client.post("/api/v1/feedback", json=payload)
    assert resp.status_code == 429
    assert resp.get_json()["error"]["code"] == "RATE_LIMITED"


def _make_keyed_counter() -> Any:
    """Return a per-key fake Redis client used by the limiter tests.

    Mirrors the pipeline / expire interface that
    :func:`backend.core.rate_limit.rate_limit` relies on. Counts live in
    a plain dict keyed by the limiter's full cache key so different
    rules and subjects never collide.

    Returns:
        An object that satisfies the limiter's redis-client contract.
    """

    class _Pipeline:
        def __init__(self, parent: Any) -> None:
            self._parent = parent
            self._key: str | None = None

        def incr(self, key: str, *_a: Any, **_k: Any) -> _Pipeline:
            self._key = key
            self._parent.counts[key] = self._parent.counts.get(key, 0) + 1
            return self

        def ttl(self, *_a: Any, **_k: Any) -> _Pipeline:
            return self

        def execute(self) -> list[int]:
            assert self._key is not None
            return [self._parent.counts[self._key], 30]

    class _Counter:
        def __init__(self) -> None:
            self.counts: dict[str, int] = {}

        def pipeline(self) -> Any:
            return _Pipeline(self)

        def expire(self, *_a: Any, **_k: Any) -> bool:
            return True

    return _Counter()


# ---------------------------------------------------------------------------
# GET /api/v1/admin/feedback
# ---------------------------------------------------------------------------


def test_admin_list_feedback_forwards_filters(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """kind / is_resolved / page / per_page reach the service intact."""
    captured: dict[str, Any] = {}

    def fake_list(_s: Any, **kw: Any) -> tuple[list[Any], int]:
        captured.update(kw)
        return [object()], 7

    monkeypatch.setattr(feedback_route.feedback_service, "list_feedback", fake_list)
    monkeypatch.setattr(
        feedback_route.feedback_service,
        "serialize_feedback",
        lambda _r: _stub_serialized(),
    )

    resp = client.get(
        "/api/v1/admin/feedback?kind=bug&is_resolved=false&page=2&per_page=10",
        headers=_admin_hdr(),
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert captured["kind"] == "bug"
    assert captured["is_resolved"] == "false"
    assert captured["page"] == 2
    assert captured["per_page"] == 10
    assert body["meta"]["total"] == 7
    assert body["meta"]["has_next"] is False


def test_admin_list_feedback_requires_key(client: FlaskClient) -> None:
    """Missing X-Admin-Key returns 401."""
    resp = client.get("/api/v1/admin/feedback")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/v1/admin/feedback/<id>/resolve
# ---------------------------------------------------------------------------


def test_admin_resolve_feedback_default_marks_resolved(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Posting with no body defaults to is_resolved=True."""
    captured: dict[str, Any] = {}

    def fake_set(_s: Any, fid: uuid.UUID, *, is_resolved: bool) -> Any:
        captured["fid"] = fid
        captured["is_resolved"] = is_resolved
        return object()

    monkeypatch.setattr(feedback_route.feedback_service, "set_resolved", fake_set)
    monkeypatch.setattr(
        feedback_route.feedback_service,
        "serialize_feedback",
        lambda _r: _stub_serialized(),
    )

    fid = uuid.uuid4()
    resp = client.post(
        f"/api/v1/admin/feedback/{fid}/resolve",
        headers=_admin_hdr(),
    )
    assert resp.status_code == 200
    assert captured["fid"] == fid
    assert captured["is_resolved"] is True


def test_admin_resolve_feedback_can_reopen(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Body field ``is_resolved=false`` reopens the submission."""
    captured: dict[str, Any] = {}

    def fake_set(_s: Any, fid: uuid.UUID, *, is_resolved: bool) -> Any:
        captured["is_resolved"] = is_resolved
        return object()

    monkeypatch.setattr(feedback_route.feedback_service, "set_resolved", fake_set)
    monkeypatch.setattr(
        feedback_route.feedback_service,
        "serialize_feedback",
        lambda _r: _stub_serialized(),
    )

    resp = client.post(
        f"/api/v1/admin/feedback/{uuid.uuid4()}/resolve",
        json={"is_resolved": False},
        headers=_admin_hdr(),
    )
    assert resp.status_code == 200
    assert captured["is_resolved"] is False


def test_admin_resolve_feedback_rejects_non_bool(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-boolean ``is_resolved`` returns 422 before hitting the service."""
    monkeypatch.setattr(
        feedback_route.feedback_service,
        "set_resolved",
        lambda *_a, **_kw: pytest.fail("service should not be called"),
    )
    resp = client.post(
        f"/api/v1/admin/feedback/{uuid.uuid4()}/resolve",
        json={"is_resolved": "yes"},
        headers=_admin_hdr(),
    )
    assert resp.status_code == 422


def test_admin_resolve_feedback_returns_404_on_malformed_uuid(
    client: FlaskClient,
) -> None:
    """A malformed path UUID returns 404 with FEEDBACK_NOT_FOUND."""
    resp = client.post(
        "/api/v1/admin/feedback/not-a-uuid/resolve",
        headers=_admin_hdr(),
    )
    assert resp.status_code == 404
    assert resp.get_json()["error"]["code"] == "FEEDBACK_NOT_FOUND"


# ---------------------------------------------------------------------------
# Smoke: FeedbackKind enum is correctly recognized at the boundary
# ---------------------------------------------------------------------------


def test_feedback_kind_enum_round_trip() -> None:
    """The enum still has the three documented members."""
    assert {k.value for k in FeedbackKind} == {"bug", "feature", "general"}

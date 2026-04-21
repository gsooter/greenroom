"""Route tests for :mod:`backend.api.v1.venue_comments`.

The underlying service is monkeypatched on every test so these stay
fast and focused on HTTP contract: status codes, body validation,
optional-auth routing, and the per-IP rate-limit decorator wrapping
:func:`submit_venue_comment`.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import pytest
from flask.testing import FlaskClient

from backend.api.v1 import venue_comments as route
from backend.data.models.users import User

# ---------------------------------------------------------------------------
# GET /venues/<slug>/comments
# ---------------------------------------------------------------------------


def test_list_comments_public_no_auth_needed(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def _fake_list(_session: Any, slug: str, **kwargs: Any) -> list[dict[str, Any]]:
        captured["slug"] = slug
        captured["kwargs"] = kwargs
        return [{"id": "a"}, {"id": "b"}]

    monkeypatch.setattr(route.service, "list_comments", _fake_list)
    resp = client.get("/api/v1/venues/black-cat/comments")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["data"] == [{"id": "a"}, {"id": "b"}]
    assert body["meta"] == {"count": 2}
    assert captured["slug"] == "black-cat"
    # Default sort/category passed through as request args.
    assert captured["kwargs"]["viewer_user_id"] is None
    assert captured["kwargs"]["viewer_session_id"] is None


def test_list_comments_forwards_query_params(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def _fake_list(_session: Any, _slug: str, **kwargs: Any) -> list[dict[str, Any]]:
        captured.update(kwargs)
        return []

    monkeypatch.setattr(route.service, "list_comments", _fake_list)
    resp = client.get(
        "/api/v1/venues/black-cat/comments"
        "?category=tickets&sort=new&limit=10&session_id=guest-xyz"
    )

    assert resp.status_code == 200
    assert captured["category"] == "tickets"
    assert captured["sort"] == "new"
    assert captured["limit"] == 10
    assert captured["viewer_session_id"] == "guest-xyz"


def test_list_comments_rejects_non_integer_limit(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(route.service, "list_comments", lambda *_a, **_k: [])
    resp = client.get("/api/v1/venues/black-cat/comments?limit=abc")
    assert resp.status_code == 422


def test_list_comments_clamps_limit_to_max(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        route.service,
        "list_comments",
        lambda *_a, **kw: captured.update(kw) or [],
    )
    resp = client.get("/api/v1/venues/black-cat/comments?limit=9999")
    assert resp.status_code == 200
    assert captured["limit"] == 100


def test_list_comments_with_valid_auth_sets_viewer_user_id(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, user, hdrs = authed_client
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        route.service,
        "list_comments",
        lambda *_a, **kw: captured.update(kw) or [],
    )
    resp = client.get("/api/v1/venues/black-cat/comments", headers=hdrs())
    assert resp.status_code == 200
    assert captured["viewer_user_id"] == user.id
    assert captured["viewer_session_id"] is None


def test_list_comments_ignores_invalid_bearer_token(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        route.service,
        "list_comments",
        lambda *_a, **kw: captured.update(kw) or [],
    )
    resp = client.get(
        "/api/v1/venues/black-cat/comments",
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert resp.status_code == 200
    assert captured["viewer_user_id"] is None


# ---------------------------------------------------------------------------
# POST /venues/<slug>/comments
# ---------------------------------------------------------------------------


def test_submit_comment_requires_auth(client: FlaskClient) -> None:
    resp = client.post(
        "/api/v1/venues/black-cat/comments",
        json={"category": "tickets", "body": "hello"},
    )
    assert resp.status_code == 401


def test_submit_comment_requires_json_object(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
) -> None:
    client, _u, hdrs = authed_client
    resp = client.post(
        "/api/v1/venues/black-cat/comments",
        data="not-json",
        headers={**hdrs(), "Content-Type": "application/json"},
    )
    assert resp.status_code == 422


def test_submit_comment_requires_body_field(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
) -> None:
    client, _u, hdrs = authed_client
    resp = client.post(
        "/api/v1/venues/black-cat/comments",
        json={"category": "tickets"},
        headers=hdrs(),
    )
    assert resp.status_code == 422


def test_submit_comment_requires_category_field(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
) -> None:
    client, _u, hdrs = authed_client
    resp = client.post(
        "/api/v1/venues/black-cat/comments",
        json={"body": "hello"},
        headers=hdrs(),
    )
    assert resp.status_code == 422


def test_submit_comment_happy_path(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, user, hdrs = authed_client
    captured: dict[str, Any] = {}

    def _fake_submit(_session: Any, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"id": "c1"}

    monkeypatch.setattr(route.service, "submit_comment", _fake_submit)
    # Short-circuit the real ip-hasher so it doesn't touch settings.
    monkeypatch.setattr(route.service, "hash_request_ip", lambda _ip: "hash-abc")
    resp = client.post(
        "/api/v1/venues/black-cat/comments",
        json={
            "category": "tickets",
            "body": "great show last night",
            "honeypot": "",
        },
        headers=hdrs(),
    )
    assert resp.status_code == 201
    assert resp.get_json() == {"data": {"id": "c1"}}
    assert captured["venue_slug"] == "black-cat"
    assert captured["user"] is user
    assert captured["category"] == "tickets"
    assert captured["body"] == "great show last night"
    assert captured["honeypot"] == ""
    assert captured["ip_hash"] == "hash-abc"


def test_submit_comment_forwards_honeypot_value(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _u, hdrs = authed_client
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        route.service,
        "submit_comment",
        lambda *_a, **kw: captured.update(kw) or {"id": "c1"},
    )
    monkeypatch.setattr(route.service, "hash_request_ip", lambda _ip: "h")
    client.post(
        "/api/v1/venues/black-cat/comments",
        json={"category": "tickets", "body": "hi", "honeypot": "bot-input"},
        headers=hdrs(),
    )
    assert captured["honeypot"] == "bot-input"


# ---------------------------------------------------------------------------
# POST /venues/<slug>/comments/<id>/vote
# ---------------------------------------------------------------------------


def test_vote_rejects_bad_comment_uuid(client: FlaskClient) -> None:
    resp = client.post(
        "/api/v1/venues/black-cat/comments/not-a-uuid/vote",
        json={"value": 1, "session_id": "guest"},
    )
    assert resp.status_code == 422


def test_vote_rejects_invalid_value(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(route.service, "cast_vote", lambda *_a, **_k: {})
    resp = client.post(
        f"/api/v1/venues/black-cat/comments/{uuid.uuid4()}/vote",
        json={"value": 5, "session_id": "guest"},
    )
    assert resp.status_code == 422


def test_vote_rejects_boolean_true_as_value(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(route.service, "cast_vote", lambda *_a, **_k: {})
    resp = client.post(
        f"/api/v1/venues/black-cat/comments/{uuid.uuid4()}/vote",
        json={"value": True, "session_id": "guest"},
    )
    assert resp.status_code == 422


def test_vote_happy_path_guest(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def _fake_cast(_session: Any, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"likes": 2, "dislikes": 0, "viewer_vote": 1}

    monkeypatch.setattr(route.service, "cast_vote", _fake_cast)
    comment_id = uuid.uuid4()
    resp = client.post(
        f"/api/v1/venues/black-cat/comments/{comment_id}/vote",
        json={"value": 1, "session_id": "guest-xyz"},
    )
    assert resp.status_code == 200
    assert resp.get_json() == {"data": {"likes": 2, "dislikes": 0, "viewer_vote": 1}}
    assert captured["comment_id"] == comment_id
    assert captured["value"] == 1
    assert captured["user"] is None
    assert captured["session_id"] == "guest-xyz"


def test_vote_happy_path_authed_ignores_session_id(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, user, hdrs = authed_client
    captured: dict[str, Any] = {}

    def _fake_cast(_session: Any, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"likes": 0, "dislikes": 0, "viewer_vote": -1}

    monkeypatch.setattr(route.service, "cast_vote", _fake_cast)
    resp = client.post(
        f"/api/v1/venues/black-cat/comments/{uuid.uuid4()}/vote",
        json={"value": -1, "session_id": "guest-should-be-ignored"},
        headers=hdrs(),
    )
    assert resp.status_code == 200
    assert captured["user"] is user
    assert captured["session_id"] is None


def test_vote_zero_clears_vote(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def _fake_cast(_session: Any, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"likes": 0, "dislikes": 0, "viewer_vote": None}

    monkeypatch.setattr(route.service, "cast_vote", _fake_cast)
    resp = client.post(
        f"/api/v1/venues/black-cat/comments/{uuid.uuid4()}/vote",
        json={"value": 0, "session_id": "guest"},
    )
    assert resp.status_code == 200
    assert captured["value"] == 0


# ---------------------------------------------------------------------------
# DELETE /venues/<slug>/comments/<id>
# ---------------------------------------------------------------------------


def test_delete_comment_requires_auth(client: FlaskClient) -> None:
    resp = client.delete(f"/api/v1/venues/black-cat/comments/{uuid.uuid4()}")
    assert resp.status_code == 401


def test_delete_comment_rejects_bad_uuid(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
) -> None:
    client, _u, hdrs = authed_client
    resp = client.delete("/api/v1/venues/black-cat/comments/not-a-uuid", headers=hdrs())
    assert resp.status_code == 422


def test_delete_comment_returns_204(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, user, hdrs = authed_client
    captured: dict[str, Any] = {}

    def _fake_delete(_session: Any, **kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(route.service, "delete_comment", _fake_delete)
    comment_id = uuid.uuid4()
    resp = client.delete(
        f"/api/v1/venues/black-cat/comments/{comment_id}", headers=hdrs()
    )
    assert resp.status_code == 204
    assert captured["comment_id"] == comment_id
    assert captured["user"] is user

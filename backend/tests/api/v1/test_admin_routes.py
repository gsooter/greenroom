"""Route tests for :mod:`backend.api.v1.admin` beyond the existing auth tests."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from flask.testing import FlaskClient

from backend.api.v1 import admin as admin_route
from backend.core.config import get_settings


def _hdr() -> dict[str, str]:
    return {"X-Admin-Key": get_settings().admin_secret_key}


def test_list_scraper_runs_happy_path(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def fake_list(_s: Any, **kw: Any) -> tuple[list[Any], int]:
        captured.update(kw)
        return [object()], 1

    monkeypatch.setattr(admin_route.admin_service, "list_scraper_runs", fake_list)
    monkeypatch.setattr(
        admin_route.admin_service,
        "serialize_scraper_run",
        lambda _r: {"id": str(uuid.uuid4())},
    )
    resp = client.get(
        "/api/v1/admin/scraper-runs?venue_slug=black-cat&status=success"
        "&page=2&per_page=10",
        headers=_hdr(),
    )
    assert resp.status_code == 200
    assert captured["venue_slug"] == "black-cat"
    assert captured["status"] == "success"
    assert captured["page"] == 2
    assert captured["per_page"] == 10


def test_trigger_scraper_run_happy_path(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        admin_route.admin_service,
        "trigger_scraper_run",
        lambda _s, slug: {"status": "success", "slug": slug},
    )
    resp = client.post("/api/v1/admin/scrapers/black-cat/run", headers=_hdr())
    assert resp.status_code == 200
    assert resp.get_json()["data"]["slug"] == "black-cat"


def test_list_users_forwards_filters(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Query-string filters reach the service intact."""
    captured: dict[str, Any] = {}

    def fake_list(_s: Any, **kw: Any) -> tuple[list[Any], int]:
        captured.update(kw)
        return [object()], 1

    monkeypatch.setattr(admin_route.admin_service, "list_users", fake_list)
    monkeypatch.setattr(
        admin_route.admin_service,
        "serialize_user_summary",
        lambda _u: {"id": "x"},
    )
    resp = client.get(
        "/api/v1/admin/users?search=pat&is_active=true&page=2&per_page=10",
        headers=_hdr(),
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["data"] == [{"id": "x"}]
    assert body["meta"] == {
        "total": 1,
        "page": 2,
        "per_page": 10,
        "has_next": False,
    }
    assert captured["search"] == "pat"
    assert captured["is_active"] == "true"
    assert captured["page"] == 2
    assert captured["per_page"] == 10


def test_deactivate_user_calls_service(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Valid UUID path delegates to the service and returns the summary."""
    uid = uuid.uuid4()
    captured: dict[str, Any] = {}

    def fake_deactivate(_s: Any, user_id: uuid.UUID) -> Any:
        captured["user_id"] = user_id
        return object()

    monkeypatch.setattr(admin_route.admin_service, "deactivate_user", fake_deactivate)
    monkeypatch.setattr(
        admin_route.admin_service,
        "serialize_user_summary",
        lambda _u: {"id": str(uid), "is_active": False},
    )
    resp = client.post(f"/api/v1/admin/users/{uid}/deactivate", headers=_hdr())
    assert resp.status_code == 200
    assert resp.get_json()["data"]["is_active"] is False
    assert captured["user_id"] == uid


def test_reactivate_user_calls_service(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reactivation route mirrors the deactivate contract."""
    uid = uuid.uuid4()
    monkeypatch.setattr(
        admin_route.admin_service,
        "reactivate_user",
        lambda _s, _uid: object(),
    )
    monkeypatch.setattr(
        admin_route.admin_service,
        "serialize_user_summary",
        lambda _u: {"id": str(uid), "is_active": True},
    )
    resp = client.post(f"/api/v1/admin/users/{uid}/reactivate", headers=_hdr())
    assert resp.status_code == 200
    assert resp.get_json()["data"]["is_active"] is True


def test_delete_user_calls_service(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DELETE route returns ``deleted: true`` and forwards the parsed UUID."""
    uid = uuid.uuid4()
    captured: dict[str, Any] = {}

    def fake_delete(_s: Any, user_id: uuid.UUID) -> None:
        captured["user_id"] = user_id

    monkeypatch.setattr(admin_route.admin_service, "delete_user", fake_delete)
    resp = client.delete(f"/api/v1/admin/users/{uid}", headers=_hdr())
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["data"] == {"id": str(uid), "deleted": True}
    assert captured["user_id"] == uid


def test_delete_user_malformed_uuid_returns_404(
    client: FlaskClient,
) -> None:
    """A non-UUID path segment is treated as a 404, not a 500."""
    resp = client.delete("/api/v1/admin/users/not-a-uuid", headers=_hdr())
    assert resp.status_code == 404
    assert resp.get_json()["error"]["code"] == "USER_NOT_FOUND"


def test_send_test_alert_route_returns_payload(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /admin/alerts/test surfaces the service payload directly."""
    monkeypatch.setattr(
        admin_route.admin_service,
        "send_test_alert",
        lambda _s: {
            "delivered": True,
            "slack_configured": True,
            "email_configured": False,
            "title": "Greenroom alert pipeline test",
            "severity": "info",
        },
    )
    resp = client.post("/api/v1/admin/alerts/test", headers=_hdr())
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["data"]["delivered"] is True
    assert body["data"]["slack_configured"] is True
    assert body["data"]["email_configured"] is False
    assert body["data"]["severity"] == "info"


def test_send_test_alert_route_requires_admin_key(client: FlaskClient) -> None:
    """The button is gated behind the same shared admin key."""
    resp = client.post("/api/v1/admin/alerts/test")
    assert resp.status_code == 401

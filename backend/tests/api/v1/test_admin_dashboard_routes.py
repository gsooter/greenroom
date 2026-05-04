"""Route tests for the admin dashboard endpoint."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from flask.testing import FlaskClient

from backend.api.v1 import admin as admin_route
from backend.core.config import get_settings


def _hdr() -> dict[str, str]:
    """Build the X-Admin-Key header for admin route tests.

    Returns:
        Header dict with the test admin secret.
    """
    return {"X-Admin-Key": get_settings().admin_secret_key}


def test_dashboard_endpoint_requires_admin_key(client: FlaskClient) -> None:
    resp = client.get("/api/v1/admin/dashboard")
    assert resp.status_code == 401


def test_dashboard_endpoint_returns_serialized_snapshot(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = MagicMock()
    monkeypatch.setattr(admin_route, "build_dashboard_snapshot", lambda _s: snapshot)
    captured: dict[str, Any] = {}

    def fake_serialize(snap: Any) -> dict[str, Any]:
        captured["snap"] = snap
        return {
            "users": {"total": 7, "breakdown": {}},
            "artists": {"total": 9, "breakdown": {}},
            "events": {"total": 12, "breakdown": {}},
            "venues": {"total": 4, "breakdown": {}},
            "music_connections": {"spotify": 1},
            "push_subscriptions": {"active": 0, "disabled": 0},
            "email_enabled_users": 3,
            "activity": [],
            "health": [],
            "most_hydrated": [],
            "best_candidates": [],
            "daily_hydration_remaining": 100,
        }

    monkeypatch.setattr(admin_route, "serialize_dashboard_snapshot", fake_serialize)

    resp = client.get("/api/v1/admin/dashboard", headers=_hdr())
    assert resp.status_code == 200
    body = resp.get_json()["data"]
    assert body["users"]["total"] == 7
    assert body["daily_hydration_remaining"] == 100
    assert captured["snap"] is snapshot

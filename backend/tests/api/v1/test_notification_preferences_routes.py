"""Route tests for :mod:`backend.api.v1.notification_preferences`."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from flask.testing import FlaskClient

from backend.api.v1 import notification_preferences as prefs_route
from backend.data.models.notifications import NotificationPreferences
from backend.data.models.users import User


def _stub_prefs(**overrides: Any) -> NotificationPreferences:
    """Return a NotificationPreferences with sensible defaults.

    Args:
        **overrides: Field overrides applied on top of the defaults.

    Returns:
        A NotificationPreferences populated for serialization.
    """
    defaults: dict[str, Any] = {
        "artist_announcements": True,
        "venue_announcements": True,
        "selling_fast_alerts": True,
        "show_reminders": True,
        "show_reminder_days_before": 1,
        "staff_picks": False,
        "artist_spotlights": False,
        "similar_artist_suggestions": False,
        "weekly_digest": False,
        "digest_day_of_week": "monday",
        "digest_hour": 8,
        "max_emails_per_week": 3,
        "quiet_hours_start": 21,
        "quiet_hours_end": 8,
        "timezone": "America/New_York",
        "paused_at": None,
        "paused_snapshot": None,
    }
    defaults.update(overrides)
    return NotificationPreferences(**defaults)


# ---------------------------------------------------------------------------
# Auth enforcement
# ---------------------------------------------------------------------------


def test_get_requires_auth(client: FlaskClient) -> None:
    """Missing bearer token → 401 on the GET path."""
    resp = client.get("/api/v1/me/notification-preferences")
    assert resp.status_code == 401


def test_patch_requires_auth(client: FlaskClient) -> None:
    """Missing bearer token → 401 on the PATCH path."""
    resp = client.patch("/api/v1/me/notification-preferences", json={})
    assert resp.status_code == 401


def test_pause_requires_auth(client: FlaskClient) -> None:
    """Missing bearer token → 401 on the pause-all path."""
    resp = client.post("/api/v1/me/notification-preferences/pause-all")
    assert resp.status_code == 401


def test_resume_requires_auth(client: FlaskClient) -> None:
    """Missing bearer token → 401 on the resume-all path."""
    resp = client.post("/api/v1/me/notification-preferences/resume-all")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /me/notification-preferences
# ---------------------------------------------------------------------------


def test_get_returns_serialized_preferences(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: returns the serialized prefs row for the auth'd user."""
    client, user, hdrs = authed_client
    prefs = _stub_prefs()
    captured: dict[str, Any] = {}

    def fake_get(_s: Any, user_id: Any) -> NotificationPreferences:
        captured["user_id"] = user_id
        return prefs

    monkeypatch.setattr(prefs_route.prefs_service, "get_preferences_for_user", fake_get)

    resp = client.get("/api/v1/me/notification-preferences", headers=hdrs())

    assert resp.status_code == 200
    body = resp.get_json()["data"]
    assert body["weekly_digest"] is False
    assert body["paused"] is False
    assert captured["user_id"] == user.id


# ---------------------------------------------------------------------------
# PATCH /me/notification-preferences
# ---------------------------------------------------------------------------


def test_patch_calls_service_and_returns_serialized(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Patch payload is forwarded to the service; response is serialized."""
    client, user, hdrs = authed_client
    captured: dict[str, Any] = {}
    updated = _stub_prefs(weekly_digest=True, digest_hour=18)

    def fake_update(
        _s: Any, user_id: Any, payload: dict[str, Any]
    ) -> NotificationPreferences:
        captured["user_id"] = user_id
        captured["payload"] = payload
        return updated

    monkeypatch.setattr(
        prefs_route.prefs_service, "update_preferences_for_user", fake_update
    )

    resp = client.patch(
        "/api/v1/me/notification-preferences",
        json={"weekly_digest": True, "digest_hour": 18},
        headers=hdrs(),
    )

    assert resp.status_code == 200
    body = resp.get_json()["data"]
    assert body["weekly_digest"] is True
    assert body["digest_hour"] == 18
    assert captured["user_id"] == user.id
    assert captured["payload"] == {"weekly_digest": True, "digest_hour": 18}


def test_patch_rejects_non_dict_body(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
) -> None:
    """A non-JSON body is rejected with 422."""
    client, _user, hdrs = authed_client
    resp = client.patch(
        "/api/v1/me/notification-preferences", data="garbage", headers=hdrs()
    )
    assert resp.status_code == 422


def test_patch_surfaces_validation_error(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Service ValidationError surfaces as 422 with code VALIDATION_ERROR."""
    client, _user, hdrs = authed_client

    from backend.core.exceptions import ValidationError

    def boom(_s: Any, _uid: Any, _payload: Any) -> None:
        raise ValidationError("digest_hour must be between 0 and 23.")

    monkeypatch.setattr(prefs_route.prefs_service, "update_preferences_for_user", boom)

    resp = client.patch(
        "/api/v1/me/notification-preferences",
        json={"digest_hour": 99},
        headers=hdrs(),
    )

    assert resp.status_code == 422
    body = resp.get_json()
    assert body["error"]["code"] == "VALIDATION_ERROR"


# ---------------------------------------------------------------------------
# Pause / resume
# ---------------------------------------------------------------------------


def test_pause_all_calls_service(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pause-all endpoint forwards to the service and returns serialized prefs."""
    client, user, hdrs = authed_client
    paused = _stub_prefs(paused_at=datetime(2026, 4, 26, tzinfo=UTC))
    captured: dict[str, Any] = {}

    def fake_pause(_s: Any, user_id: Any) -> NotificationPreferences:
        captured["user_id"] = user_id
        return paused

    monkeypatch.setattr(prefs_route.prefs_service, "pause_all_emails", fake_pause)

    resp = client.post("/api/v1/me/notification-preferences/pause-all", headers=hdrs())

    assert resp.status_code == 200
    body = resp.get_json()["data"]
    assert body["paused"] is True
    assert captured["user_id"] == user.id


def test_resume_all_calls_service(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resume-all endpoint forwards to the service and returns serialized prefs."""
    client, _user, hdrs = authed_client
    resumed = _stub_prefs(paused_at=None)

    monkeypatch.setattr(
        prefs_route.prefs_service,
        "resume_all_emails",
        lambda _s, _uid: resumed,
    )

    resp = client.post("/api/v1/me/notification-preferences/resume-all", headers=hdrs())

    assert resp.status_code == 200
    body = resp.get_json()["data"]
    assert body["paused"] is False

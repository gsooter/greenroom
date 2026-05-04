"""Route tests for the mass-hydration trigger endpoint."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from flask.testing import FlaskClient

from backend.core.config import get_settings


def _hdr() -> dict[str, str]:
    """Build the X-Admin-Key header for admin route tests.

    Returns:
        Header dict with the test admin secret.
    """
    return {"X-Admin-Key": get_settings().admin_secret_key}


def test_trigger_endpoint_requires_admin_key(client: FlaskClient) -> None:
    resp = client.post(
        "/api/v1/admin/hydrate-mass",
        json={"admin_email": "ops@x"},
    )
    assert resp.status_code == 401


def test_trigger_endpoint_rejects_missing_body(client: FlaskClient) -> None:
    resp = client.post("/api/v1/admin/hydrate-mass", data="not-json", headers=_hdr())
    assert resp.status_code == 422


def test_trigger_endpoint_rejects_missing_email(client: FlaskClient) -> None:
    resp = client.post("/api/v1/admin/hydrate-mass", json={}, headers=_hdr())
    assert resp.status_code == 422


def test_trigger_endpoint_enqueues_celery_task(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Endpoint forwards the email and returns the queued task id."""
    captured: dict[str, Any] = {}
    fake_result = MagicMock()
    fake_result.id = "abc-123"

    def fake_send_task(name: str, args: list[Any]) -> MagicMock:
        captured["name"] = name
        captured["args"] = args
        return fake_result

    fake_app = MagicMock()
    fake_app.send_task.side_effect = fake_send_task

    import backend.celery_app as celery_app_module

    monkeypatch.setattr(celery_app_module, "celery_app", fake_app)

    resp = client.post(
        "/api/v1/admin/hydrate-mass",
        json={"admin_email": "ops@greenroom.test"},
        headers=_hdr(),
    )
    assert resp.status_code == 202
    body = resp.get_json()["data"]
    assert body["task_id"] == "abc-123"
    assert body["status"] == "queued"
    assert body["admin_email"] == "ops@greenroom.test"
    fake_app.send_task.assert_called_once_with(
        "backend.services.artist_hydration_tasks.mass_hydrate_task",
        args=["ops@greenroom.test"],
    )

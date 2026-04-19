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

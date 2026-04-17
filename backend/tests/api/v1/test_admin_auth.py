"""Tests for the :func:`require_admin` decorator.

Covers the three header states the decorator distinguishes between:
missing, wrong, and correct. The decorator is attached to a real
Flask app so the AppError handler also formats the JSON shape we
ship back to clients.
"""

from __future__ import annotations

from typing import Iterator

import pytest
from flask import Flask
from flask.testing import FlaskClient

from backend.api.v1.admin import require_admin
from backend.app import create_app
from backend.core.config import get_settings


@pytest.fixture
def client() -> Iterator[FlaskClient]:
    """Boot the real Flask app and return its test client.

    Yields:
        A Flask test client bound to the configured app.
    """
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as test_client:
        yield test_client


def test_missing_admin_key_returns_401(client: FlaskClient) -> None:
    """Absent ``X-Admin-Key`` header is a 401 UNAUTHORIZED."""
    response = client.get("/api/v1/admin/scrapers")
    assert response.status_code == 401
    body = response.get_json()
    assert body["error"]["code"] == "UNAUTHORIZED"


def test_wrong_admin_key_returns_403(client: FlaskClient) -> None:
    """Wrong ``X-Admin-Key`` header is a 403 FORBIDDEN."""
    response = client.get(
        "/api/v1/admin/scrapers",
        headers={"X-Admin-Key": "nope"},
    )
    assert response.status_code == 403
    body = response.get_json()
    assert body["error"]["code"] == "FORBIDDEN"


def test_correct_admin_key_allows_access(client: FlaskClient) -> None:
    """A matching ``X-Admin-Key`` header returns the fleet summary."""
    response = client.get(
        "/api/v1/admin/scrapers",
        headers={"X-Admin-Key": get_settings().admin_secret_key},
    )
    assert response.status_code == 200
    body = response.get_json()
    assert "enabled" in body["data"]
    assert "by_region" in body["data"]
    assert isinstance(body["data"]["venues"], list)


def test_decorator_uses_constant_time_comparison() -> None:
    """Sanity-check that the decorator references ``hmac.compare_digest``.

    We don't want an open-coded ``==`` slipping back in during a refactor
    — it would reintroduce a timing-attack surface on the admin key.
    """
    import inspect

    source = inspect.getsource(require_admin)
    assert "compare_digest" in source

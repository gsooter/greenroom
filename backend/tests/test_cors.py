"""Tests for the CORS policy configured on the Flask app.

The policy pins cross-origin access to the single configured frontend
origin — ``*`` is never acceptable now that the browser only talks to
Greenroom and the API carries bearer tokens that a third-party site
could replay.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from flask.testing import FlaskClient

from backend.app import create_app


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[FlaskClient]:
    """Build a Flask test client with a known frontend origin configured.

    Yields:
        A test client bound to the app factory's output.
    """
    monkeypatch.setenv("FRONTEND_BASE_URL", "https://app.greenroom.test")
    app = create_app()
    with app.test_client() as c:
        yield c


def test_allowed_origin_gets_echoed(client: FlaskClient) -> None:
    """A request from the configured frontend origin gets the CORS headers."""
    resp = client.get("/health", headers={"Origin": "https://app.greenroom.test"})
    assert resp.status_code == 200
    assert resp.headers["Access-Control-Allow-Origin"] == "https://app.greenroom.test"
    assert resp.headers["Vary"] == "Origin"
    assert "Authorization" in resp.headers["Access-Control-Allow-Headers"]
    assert "POST" in resp.headers["Access-Control-Allow-Methods"]
    assert resp.headers["Access-Control-Max-Age"] == "600"


def test_disallowed_origin_gets_no_cors_headers(client: FlaskClient) -> None:
    """A request from a foreign origin gets a response with no CORS leakage."""
    resp = client.get("/health", headers={"Origin": "https://evil.example"})
    assert resp.status_code == 200
    assert "Access-Control-Allow-Origin" not in resp.headers
    assert "Access-Control-Allow-Methods" not in resp.headers


def test_request_without_origin_header_gets_no_cors_headers(
    client: FlaskClient,
) -> None:
    """Same-origin / server-to-server calls skip the CORS preamble entirely."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert "Access-Control-Allow-Origin" not in resp.headers


def test_preflight_options_allows_allowed_origin(client: FlaskClient) -> None:
    """OPTIONS preflight from the configured origin receives full CORS headers."""
    resp = client.options(
        "/health",
        headers={
            "Origin": "https://app.greenroom.test",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type, Authorization",
        },
    )
    assert resp.status_code in (200, 204)
    assert resp.headers["Access-Control-Allow-Origin"] == "https://app.greenroom.test"
    assert "Authorization" in resp.headers["Access-Control-Allow-Headers"]


def test_no_wildcard_origin_on_any_response(client: FlaskClient) -> None:
    """A cross-origin response never returns ``*`` as the allowed origin."""
    resp = client.get("/health", headers={"Origin": "https://app.greenroom.test"})
    assert resp.headers["Access-Control-Allow-Origin"] != "*"

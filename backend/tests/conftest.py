"""Shared test fixtures for the backend test suite.

Fixtures here are process-scoped and cheap — the Flask app factory
and stubbed settings in particular are reused across the unit-test
layer, which does not touch the database.

DB-backed integration tests (repositories, end-to-end API flows) live
under ``tests/data`` and ``tests/api`` and pull in their own session
fixtures once a ``greenroom_test`` Postgres database exists. Until
then, the unit tests here provide the coverage.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

# ---------------------------------------------------------------------------
# Environment stubs for Pydantic Settings
# ---------------------------------------------------------------------------
#
# ``backend.core.config.Settings`` declares every env var as required. Unit
# tests should never depend on the developer's real ``.env``, so we inject
# safe placeholders before any backend module is imported.

_TEST_ENV = {
    "SPOTIFY_CLIENT_ID": "test-spotify-id",
    "SPOTIFY_CLIENT_SECRET": "test-spotify-secret",
    "SPOTIFY_REDIRECT_URI": "http://localhost/callback",
    "DATABASE_URL": "postgresql://localhost/greenroom_test",
    "REDIS_URL": "redis://localhost:6379/1",
    "JWT_SECRET_KEY": "test-jwt-secret-key-with-minimum-32-bytes-for-hs256",
    "JWT_EXPIRY_SECONDS": "3600",
    "SENDGRID_API_KEY": "x",
    "SENDGRID_FROM_EMAIL": "alerts@greenroom.test",
    "TICKETMASTER_API_KEY": "test-tm-key",
    "SEATGEEK_CLIENT_ID": "test-sg-id",
    "SEATGEEK_CLIENT_SECRET": "test-sg-secret",
    "ADMIN_SECRET_KEY": "test-admin-secret",
    "SLACK_WEBHOOK_URL": "x",
    "ALERT_EMAIL": "x@x.com",
    "POSTHOG_API_KEY": "x",
    "POSTHOG_HOST": "http://localhost:8000",
}

for _key, _value in _TEST_ENV.items():
    os.environ.setdefault(_key, _value)


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    """Ensure each test sees freshly loaded settings.

    ``get_settings`` is not memoized today, but fixtures that monkey-patch
    env vars between tests would otherwise risk leakage. This hook is a
    cheap safety net.

    Yields:
        None; teardown is a no-op.
    """
    yield

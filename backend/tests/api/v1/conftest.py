"""Shared fixtures for Flask API route tests.

API tests use the real Flask app factory but replace the SQLAlchemy
session factory with a MagicMock so no Postgres connection is attempted.
Every route module's ``get_db`` import resolves through
``backend.core.database._session_factory``; rebinding that one pointer
intercepts every call.
"""

from __future__ import annotations

import uuid
from typing import Callable, Iterator
from unittest.mock import MagicMock

import pytest
from flask.testing import FlaskClient

from backend.app import create_app
from backend.core import auth as auth_module
from backend.core import database as database_module
from backend.core.auth import issue_token
from backend.data.models.users import User


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[FlaskClient]:
    """Flask test client with a MagicMock-backed session factory."""
    app = create_app()
    app.config["TESTING"] = True

    def _fake_factory() -> MagicMock:
        return MagicMock()

    monkeypatch.setattr(database_module, "_session_factory", _fake_factory)

    with app.test_client() as test_client:
        yield test_client


@pytest.fixture
def authed_client(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> tuple[FlaskClient, User, Callable[[], dict[str, str]]]:
    """Flask test client pre-wired to pass ``@require_auth``.

    Returns a tuple of (client, stub user, header-builder). The stub
    user is a real ``User`` instance (passes the isinstance check) but
    is never written to a session. ``users_repo.get_user_by_id`` is
    stubbed to hand it back so the decorator completes successfully.
    """
    stub = User(
        id=uuid.uuid4(),
        email="pat@example.test",
        display_name="Pat",
        is_active=True,
    )
    monkeypatch.setattr(
        auth_module.users_repo, "get_user_by_id", lambda _s, _uid: stub
    )
    token = issue_token(stub.id)

    def make_headers() -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    return client, stub, make_headers

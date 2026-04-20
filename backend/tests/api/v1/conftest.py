"""Shared fixtures for Flask API route tests.

API tests use the real Flask app factory but replace the SQLAlchemy
session factory with a MagicMock so no Postgres connection is attempted.
Every route module's ``get_db`` import resolves through
``backend.core.database._session_factory``; rebinding that one pointer
intercepts every call.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from unittest.mock import MagicMock

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from flask.testing import FlaskClient

from backend.app import create_app
from backend.core import auth as auth_module
from backend.core import database as database_module
from backend.data.models.users import User
from backend.tests.conftest import mint_knuckles_token


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[FlaskClient]:
    """Flask test client with a MagicMock-backed session factory.

    Args:
        monkeypatch: pytest's monkeypatch fixture, used to swap the
            DB session factory.

    Yields:
        A configured Flask test client.
    """
    app = create_app()
    app.config["TESTING"] = True

    def _fake_factory() -> MagicMock:
        return MagicMock()

    monkeypatch.setattr(database_module, "_session_factory", _fake_factory)

    with app.test_client() as test_client:
        yield test_client


@pytest.fixture
def authed_client(
    client: FlaskClient,
    monkeypatch: pytest.MonkeyPatch,
    knuckles_test_key: rsa.RSAPrivateKey,
    stub_knuckles_jwks: str,
) -> tuple[FlaskClient, User, Callable[[], dict[str, str]]]:
    """Flask test client pre-wired to pass ``@require_auth``.

    Mints a Knuckles-style RS256 access token signed by the session
    test key, with the JWKS endpoint stubbed so verification succeeds.
    The stub user is a real ``User`` instance (passes the isinstance
    check) but is never written to a session;
    ``users_repo.get_user_by_id`` is stubbed to hand it back so the
    decorator completes successfully.

    Args:
        client: Base Flask test client.
        monkeypatch: pytest's monkeypatch fixture.
        knuckles_test_key: Session-scoped RSA key for signing tokens.
        stub_knuckles_jwks: Returns the kid the JWKS publishes the
            test key under, also installs the JWKS stub.

    Returns:
        Tuple of (client, stub user, header builder). Calling the
        builder returns an ``Authorization`` header dict ready to
        pass to ``client.get(..., headers=headers())``.
    """
    stub = User(
        id=uuid.uuid4(),
        email="pat@example.test",
        display_name="Pat",
        is_active=True,
    )
    monkeypatch.setattr(auth_module.users_repo, "get_user_by_id", lambda _s, _uid: stub)
    token = mint_knuckles_token(
        signing_key=knuckles_test_key,
        kid=stub_knuckles_jwks,
        user_id=stub.id,
        email=stub.email,
    )

    def make_headers() -> dict[str, str]:
        """Return an Authorization header dict for the stubbed user.

        Returns:
            Mapping with a single ``Authorization: Bearer <token>``
            entry.
        """
        return {"Authorization": f"Bearer {token}"}

    return client, stub, make_headers

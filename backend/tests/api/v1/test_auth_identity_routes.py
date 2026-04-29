"""Route tests for :mod:`backend.api.v1.auth_identity`.

These endpoints are server-side proxies that drive the Knuckles SDK
ceremonies (magic link, Google, Apple, passkey) so the app-client
secret never ships to the browser. The tests mock
:func:`backend.core.knuckles.get_client` to return a :class:`MagicMock`
standing in for the SDK — the proxy's job is plumbing, not network
behavior, so we don't need a live HTTP stub.

Covered per endpoint:
- forwarding of the expected payload (including the server-built
  ``redirect_url``),
- shaping of the SDK's typed dataclasses into the JSON envelope the
  frontend consumes,
- lazy user provisioning on the session-completing halves,
- validation errors for missing body fields,
- ``@require_auth`` enforcement on the passkey register proxies.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from flask.testing import FlaskClient
from knuckles_client.exceptions import KnucklesAuthError
from knuckles_client.models import CeremonyStart, PasskeyChallenge, TokenPair

from backend.api.v1 import auth_identity as route
from backend.data.models.users import User
from backend.tests.conftest import mint_knuckles_token

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_SAMPLE_PAIR = TokenPair(
    access_token="a.b.c",
    access_token_expires_at=datetime(2030, 1, 1, tzinfo=UTC),
    refresh_token="r-xyz",
    refresh_token_expires_at=datetime(2030, 2, 1, tzinfo=UTC),
)


def _install_client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace ``route.get_client`` with a fresh mock SDK client.

    Args:
        monkeypatch: pytest's monkeypatch fixture.

    Returns:
        The :class:`MagicMock` standing in for the SDK client. Callers
        configure return values on its sub-clients (``magic_link``,
        ``google``, ``apple``, ``passkey``) before exercising the route.
    """
    client = MagicMock()
    monkeypatch.setattr(route, "get_client", lambda: client)
    return client


def _stub_session_exchange(
    monkeypatch: pytest.MonkeyPatch,
    *,
    user: User,
    access_token: str,
) -> MagicMock:
    """Replace ``_exchange_session`` so sign-in proxies don't need JWKS.

    Args:
        monkeypatch: pytest's monkeypatch fixture.
        user: User whose serialized form is returned in the envelope.
        access_token: Token to echo back in the envelope.

    Returns:
        The mock so callers can assert it was invoked.
    """
    exchange = MagicMock(
        return_value={
            "token": access_token,
            "user": {"id": str(user.id), "email": user.email},
        }
    )
    monkeypatch.setattr(route, "_exchange_session", exchange)
    return exchange


# ---------------------------------------------------------------------------
# Magic link
# ---------------------------------------------------------------------------


def test_magic_link_request_forwards_email_and_redirect(
    client: FlaskClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Greenroom fills in the frontend ``redirect_url`` from settings."""
    sdk = _install_client(monkeypatch)
    sdk.magic_link.start.return_value = None

    resp = client.post(
        "/api/v1/auth/magic-link/request",
        json={"email": "pat@example.test"},
    )
    assert resp.status_code == 200
    assert resp.get_json()["data"] == {"email_sent": True}
    sdk.magic_link.start.assert_called_once()
    kwargs = sdk.magic_link.start.call_args.kwargs
    assert kwargs["email"] == "pat@example.test"
    assert kwargs["redirect_url"].endswith("/auth/verify")


def test_magic_link_request_rejects_missing_email(client: FlaskClient) -> None:
    """No email field → 422 ValidationError without contacting Knuckles."""
    resp = client.post("/api/v1/auth/magic-link/request", json={})
    assert resp.status_code == 422


def test_magic_link_verify_returns_token_and_user(
    client: FlaskClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path returns the exchange envelope verbatim."""
    user = User(id=uuid.uuid4(), email="pat@example.test", is_active=True)
    exchange = _stub_session_exchange(monkeypatch, user=user, access_token="a.b.c")
    sdk = _install_client(monkeypatch)
    sdk.magic_link.verify.return_value = _SAMPLE_PAIR

    resp = client.post("/api/v1/auth/magic-link/verify", json={"token": "link-tok"})
    assert resp.status_code == 200
    body = resp.get_json()["data"]
    assert body["token"] == "a.b.c"
    assert body["user"]["id"] == str(user.id)
    sdk.magic_link.verify.assert_called_once_with("link-tok")
    exchange.assert_called_once_with(_SAMPLE_PAIR)


def test_magic_link_verify_rejects_missing_token(client: FlaskClient) -> None:
    """No token → 422 without contacting Knuckles."""
    resp = client.post("/api/v1/auth/magic-link/verify", json={})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Google
# ---------------------------------------------------------------------------


def test_google_start_forwards_redirect_and_returns_data(
    client: FlaskClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Start passes the callback URL and returns the SDK's CeremonyStart."""
    sdk = _install_client(monkeypatch)
    sdk.google.start.return_value = CeremonyStart(
        authorize_url="https://accounts.google.com/x", state="s"
    )

    resp = client.get("/api/v1/auth/google/start")
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["authorize_url"].startswith("https://accounts.google.com/")
    assert data["state"] == "s"
    redirect = sdk.google.start.call_args.kwargs["redirect_url"]
    assert redirect.endswith("/auth/google/callback")


def test_google_complete_exchanges_and_returns_token(
    client: FlaskClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Complete round-trips code/state and returns a session envelope."""
    user = User(id=uuid.uuid4(), email="pat@example.test", is_active=True)
    _stub_session_exchange(monkeypatch, user=user, access_token="g.t")
    sdk = _install_client(monkeypatch)
    sdk.google.complete.return_value = _SAMPLE_PAIR

    resp = client.post("/api/v1/auth/google/complete", json={"code": "c", "state": "s"})
    assert resp.status_code == 200
    assert resp.get_json()["data"]["token"] == "g.t"
    sdk.google.complete.assert_called_once_with(code="c", state="s")


def test_google_complete_rejects_missing_fields(client: FlaskClient) -> None:
    """code and state are both required."""
    resp = client.post("/api/v1/auth/google/complete", json={"state": "s"})
    assert resp.status_code == 422
    resp = client.post("/api/v1/auth/google/complete", json={"code": "c"})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Apple
# ---------------------------------------------------------------------------


def test_apple_start_returns_data(
    client: FlaskClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Apple start returns the SDK's CeremonyStart shape."""
    sdk = _install_client(monkeypatch)
    sdk.apple.start.return_value = CeremonyStart(
        authorize_url="https://appleid.apple.com/x", state="s"
    )
    resp = client.get("/api/v1/auth/apple/start")
    assert resp.status_code == 200
    assert resp.get_json()["data"]["state"] == "s"


def test_apple_complete_forwards_user_payload(
    client: FlaskClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First-login ``user`` blob is forwarded verbatim."""
    sdk = _install_client(monkeypatch)
    sdk.apple.complete.return_value = _SAMPLE_PAIR
    user = User(id=uuid.uuid4(), email="pat@example.test", is_active=True)
    _stub_session_exchange(monkeypatch, user=user, access_token="a.t")

    user_blob = {"name": {"firstName": "Pat", "lastName": "Q"}}
    resp = client.post(
        "/api/v1/auth/apple/complete",
        json={"code": "c", "state": "s", "user": user_blob},
    )
    assert resp.status_code == 200
    sdk.apple.complete.assert_called_once_with(code="c", state="s", user=user_blob)


def test_apple_complete_rejects_non_object_user(client: FlaskClient) -> None:
    """A non-null ``user`` field that isn't an object is a validation error."""
    resp = client.post(
        "/api/v1/auth/apple/complete",
        json={"code": "c", "state": "s", "user": "bad"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Passkey — registration (require_auth)
# ---------------------------------------------------------------------------


def test_passkey_register_start_requires_auth(client: FlaskClient) -> None:
    """No bearer → 401 before any Knuckles call."""
    resp = client.post("/api/v1/auth/passkey/register/start")
    assert resp.status_code == 401


def test_passkey_register_start_forwards_bearer(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The caller's token is forwarded to the SDK as ``access_token``."""
    client, _user, headers = authed_client
    sdk = _install_client(monkeypatch)
    sdk.passkey.register_begin.return_value = PasskeyChallenge(
        options={"challenge": "x"}, state="s"
    )

    resp = client.post(
        "/api/v1/auth/passkey/register/start",
        headers=headers(),
    )
    assert resp.status_code == 200
    expected_token = headers()["Authorization"].split(" ", 1)[1]
    sdk.passkey.register_begin.assert_called_once_with(access_token=expected_token)


def test_passkey_register_complete_returns_registered_flag(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Success collapses Knuckles' credential_id into ``{registered: True}``."""
    client, _user, headers = authed_client
    sdk = _install_client(monkeypatch)
    sdk.passkey.register_complete.return_value = "cred-id"

    resp = client.post(
        "/api/v1/auth/passkey/register/complete",
        json={"credential": {"id": "x"}, "state": "s", "name": "Laptop"},
        headers=headers(),
    )
    assert resp.status_code == 200
    assert resp.get_json()["data"] == {"registered": True}
    kwargs = sdk.passkey.register_complete.call_args.kwargs
    assert kwargs["credential"] == {"id": "x"}
    assert kwargs["state"] == "s"
    assert kwargs["name"] == "Laptop"


def test_passkey_register_complete_rejects_missing_fields(
    authed_client: tuple[FlaskClient, User, Callable[[], dict[str, str]]],
) -> None:
    """credential and state are required."""
    client, _user, headers = authed_client
    resp = client.post(
        "/api/v1/auth/passkey/register/complete",
        json={"state": "s"},
        headers=headers(),
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Passkey — sign-in (anonymous)
# ---------------------------------------------------------------------------


def test_passkey_authenticate_start_is_public(
    client: FlaskClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sign-in begin does not require a bearer token."""
    sdk = _install_client(monkeypatch)
    sdk.passkey.sign_in_begin.return_value = PasskeyChallenge(
        options={"challenge": "x"}, state="s"
    )
    resp = client.post("/api/v1/auth/passkey/authenticate/start")
    assert resp.status_code == 200
    assert resp.get_json()["data"]["state"] == "s"


def test_passkey_authenticate_complete_returns_session(
    client: FlaskClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Completing the assertion returns the normalized session envelope."""
    user = User(id=uuid.uuid4(), email="pat@example.test", is_active=True)
    _stub_session_exchange(monkeypatch, user=user, access_token="p.t")
    sdk = _install_client(monkeypatch)
    sdk.passkey.sign_in_complete.return_value = _SAMPLE_PAIR

    resp = client.post(
        "/api/v1/auth/passkey/authenticate/complete",
        json={"credential": {"id": "x"}, "state": "s"},
    )
    assert resp.status_code == 200
    body = resp.get_json()["data"]
    assert body["token"] == "p.t"
    assert body["user"]["id"] == str(user.id)


# ---------------------------------------------------------------------------
# _exchange_session — branches
# ---------------------------------------------------------------------------


def _make_pair(access_token: str) -> TokenPair:
    """Build a :class:`TokenPair` carrying the provided access token.

    Args:
        access_token: Encoded JWT to embed.

    Returns:
        A :class:`TokenPair` with stable expiry timestamps for assertions.
    """
    return TokenPair(
        access_token=access_token,
        access_token_expires_at=datetime(2030, 1, 1, tzinfo=UTC),
        refresh_token="r-xyz",
        refresh_token_expires_at=datetime(2030, 2, 1, tzinfo=UTC),
    )


def test_exchange_session_auto_provisions_missing_user(
    monkeypatch: pytest.MonkeyPatch,
    knuckles_test_key: rsa.RSAPrivateKey,
    stub_knuckles_jwks: str,
) -> None:
    """An unknown ``sub`` triggers lazy user creation from claims."""
    user_id = uuid.uuid4()
    created = User(id=user_id, email="new@example.test", is_active=True)
    create = MagicMock(return_value=created)
    monkeypatch.setattr(route, "get_db", lambda: MagicMock())
    monkeypatch.setattr(route.users_repo, "get_user_by_id", lambda _s, _uid: None)
    monkeypatch.setattr(route.users_repo, "get_user_by_email", lambda _s, _e: None)
    monkeypatch.setattr(route.users_repo, "create_user", create)
    monkeypatch.setattr(route.users_repo, "update_last_login", MagicMock())
    monkeypatch.setattr(
        route.users_service, "serialize_user", lambda u: {"id": str(u.id)}
    )

    token = mint_knuckles_token(
        signing_key=knuckles_test_key,
        kid=stub_knuckles_jwks,
        user_id=user_id,
        email="new@example.test",
    )
    result = route._exchange_session(_make_pair(token))
    assert result["token"] == token
    assert result["user"]["id"] == str(user_id)
    create.assert_called_once()


def test_exchange_session_rejects_when_email_belongs_to_different_id(
    monkeypatch: pytest.MonkeyPatch,
    knuckles_test_key: rsa.RSAPrivateKey,
    stub_knuckles_jwks: str,
) -> None:
    """A legacy row with the claimed email blocks auto-provisioning."""
    user_id = uuid.uuid4()
    legacy = User(id=uuid.uuid4(), email="dup@example.test", is_active=True)
    monkeypatch.setattr(route, "get_db", lambda: MagicMock())
    monkeypatch.setattr(route.users_repo, "get_user_by_id", lambda _s, _uid: None)
    monkeypatch.setattr(route.users_repo, "get_user_by_email", lambda _s, _e: legacy)

    token = mint_knuckles_token(
        signing_key=knuckles_test_key,
        kid=stub_knuckles_jwks,
        user_id=user_id,
        email="dup@example.test",
    )
    from backend.core.exceptions import AppError

    with pytest.raises(AppError) as exc_info:
        route._exchange_session(_make_pair(token))
    assert exc_info.value.status_code == 409


def test_exchange_session_reactivates_deactivated_user(
    monkeypatch: pytest.MonkeyPatch,
    knuckles_test_key: rsa.RSAPrivateKey,
    stub_knuckles_jwks: str,
) -> None:
    """A soft-deleted user who signs back in is reactivated in place.

    Deactivation is a pause, not a tombstone — saved events, follows,
    and preferences are all still intact. A fresh Knuckles exchange is
    unambiguous intent to return, so the row flips back to active and
    the envelope lands as a normal sign-in instead of a dead-end error.
    """
    user_id = uuid.uuid4()
    deactivated = User(id=user_id, email="p@example.test", is_active=False)

    def _reactivate(_s: object, user: User) -> User:
        """Flip ``is_active`` and echo the user, mirroring the real service.

        Args:
            _s: Unused session arg the real ``reactivate_user`` accepts.
            user: The deactivated row to flip on.

        Returns:
            The same ``user`` after mutation.
        """
        user.is_active = True
        return user

    reactivate_mock = MagicMock(side_effect=_reactivate)
    monkeypatch.setattr(route, "get_db", lambda: MagicMock())
    monkeypatch.setattr(
        route.users_repo, "get_user_by_id", lambda _s, _uid: deactivated
    )
    monkeypatch.setattr(route.users_service, "reactivate_user", reactivate_mock)
    monkeypatch.setattr(route.users_repo, "update_last_login", MagicMock())
    monkeypatch.setattr(
        route.users_service, "serialize_user", lambda u: {"id": str(u.id)}
    )

    token = mint_knuckles_token(
        signing_key=knuckles_test_key,
        kid=stub_knuckles_jwks,
        user_id=user_id,
        email="p@example.test",
    )
    envelope = route._exchange_session(_make_pair(token))

    reactivate_mock.assert_called_once()
    assert envelope["user"] == {"id": str(user_id)}
    assert deactivated.is_active is True


def test_exchange_session_envelope_exposes_refresh_fields(
    monkeypatch: pytest.MonkeyPatch,
    knuckles_test_key: rsa.RSAPrivateKey,
    stub_knuckles_jwks: str,
) -> None:
    """Sign-in envelope carries refresh token + both expiries verbatim."""
    user_id = uuid.uuid4()
    existing = User(id=user_id, email="p@example.test", is_active=True)
    monkeypatch.setattr(route, "get_db", lambda: MagicMock())
    monkeypatch.setattr(route.users_repo, "get_user_by_id", lambda _s, _uid: existing)
    monkeypatch.setattr(route.users_repo, "update_last_login", MagicMock())
    monkeypatch.setattr(
        route.users_service, "serialize_user", lambda u: {"id": str(u.id)}
    )
    token = mint_knuckles_token(
        signing_key=knuckles_test_key,
        kid=stub_knuckles_jwks,
        user_id=user_id,
        email="p@example.test",
    )
    pair = TokenPair(
        access_token=token,
        access_token_expires_at=datetime(2030, 1, 1, tzinfo=UTC),
        refresh_token="r-xyz",
        refresh_token_expires_at=datetime(2030, 2, 1, tzinfo=UTC),
    )
    result = route._exchange_session(pair)
    assert result["token"] == token
    assert result["token_expires_at"] == "2030-01-01T00:00:00+00:00"
    assert result["refresh_token"] == "r-xyz"
    assert result["refresh_token_expires_at"] == "2030-02-01T00:00:00+00:00"


# ---------------------------------------------------------------------------
# /auth/refresh
# ---------------------------------------------------------------------------


def test_refresh_rejects_missing_token(client: FlaskClient) -> None:
    """No refresh_token → 422 without contacting Knuckles."""
    resp = client.post("/api/v1/auth/refresh", json={})
    assert resp.status_code == 422


def test_refresh_forwards_token_and_skips_last_login_bump(
    client: FlaskClient,
    monkeypatch: pytest.MonkeyPatch,
    knuckles_test_key: rsa.RSAPrivateKey,
    stub_knuckles_jwks: str,
) -> None:
    """Refresh proxies through to the SDK and does not bump last_login_at."""
    user_id = uuid.uuid4()
    existing = User(id=user_id, email="p@example.test", is_active=True)
    monkeypatch.setattr(route.users_repo, "get_user_by_id", lambda _s, _uid: existing)
    last_login = MagicMock()
    monkeypatch.setattr(route.users_repo, "update_last_login", last_login)
    monkeypatch.setattr(
        route.users_service, "serialize_user", lambda u: {"id": str(u.id)}
    )

    new_token = mint_knuckles_token(
        signing_key=knuckles_test_key,
        kid=stub_knuckles_jwks,
        user_id=user_id,
        email="p@example.test",
    )
    sdk = _install_client(monkeypatch)
    sdk.refresh.return_value = TokenPair(
        access_token=new_token,
        access_token_expires_at=datetime(2030, 1, 1, tzinfo=UTC),
        refresh_token="r-new",
        refresh_token_expires_at=datetime(2030, 2, 1, tzinfo=UTC),
    )

    resp = client.post("/api/v1/auth/refresh", json={"refresh_token": "r-old"})
    assert resp.status_code == 200
    body = resp.get_json()["data"]
    assert body["token"] == new_token
    assert body["refresh_token"] == "r-new"
    assert body["user"]["id"] == str(user_id)
    sdk.refresh.assert_called_once_with("r-old")
    last_login.assert_not_called()


def test_refresh_bubbles_knuckles_errors(
    client: FlaskClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Knuckles rejecting the refresh token surfaces as the same status."""
    sdk = _install_client(monkeypatch)
    sdk.refresh.side_effect = KnucklesAuthError(
        code="REFRESH_TOKEN_REUSED",
        message="reuse-detected",
        status_code=401,
    )
    resp = client.post("/api/v1/auth/refresh", json={"refresh_token": "dead"})
    assert resp.status_code == 401
    assert resp.get_json()["error"]["code"] == "REFRESH_TOKEN_REUSED"


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


def test_magic_link_request_enforces_per_ip_rate_limit(
    client: FlaskClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After the per-IP quota is exhausted, magic-link requests 429.

    Uses a per-key fake Redis so the IP and email limiters count
    independently. Sends 10 requests with unique emails (staying under
    the per-email limit) to exhaust the per-IP quota, then confirms
    the 11th is blocked with the standard ``RATE_LIMITED`` code and a
    ``Retry-After`` header.
    """
    from backend.core import rate_limit as rate_limit_module

    class _KeyedCounter:
        """Fake Redis keyed by cache key so per-IP vs per-email don't collide."""

        def __init__(self) -> None:
            """Create an empty counter store."""
            self.counts: dict[str, int] = {}
            self._pending_key: str | None = None

        def pipeline(self) -> Any:
            """Return a pipeline that mirrors INCR + TTL semantics."""
            return _KeyedPipeline(self)

        def expire(self, *_a: Any, **_k: Any) -> bool:
            """Accept the TTL set from the limiter; no-op for the test."""
            return True

    class _KeyedPipeline:
        def __init__(self, parent: _KeyedCounter) -> None:
            """Hold a reference to the parent counter.

            Args:
                parent: The :class:`_KeyedCounter` tracking state.
            """
            self._parent = parent
            self._key: str | None = None

        def incr(self, key: str, *_a: Any, **_k: Any) -> _KeyedPipeline:
            """Bump the counter for ``key`` by one.

            Args:
                key: Full Redis cache key for the rule+subject pair.

            Returns:
                Self, for fluent chaining.
            """
            self._key = key
            self._parent.counts[key] = self._parent.counts.get(key, 0) + 1
            return self

        def ttl(self, *_a: Any, **_k: Any) -> _KeyedPipeline:
            """Fluent no-op — the stub reports a constant TTL."""
            return self

        def execute(self) -> list[int]:
            """Return ``(counter, ttl)`` like a real pipeline would."""
            assert self._key is not None
            return [self._parent.counts[self._key], 30]

    counter_instance = _KeyedCounter()
    monkeypatch.setattr(rate_limit_module, "_get_redis", lambda: counter_instance)

    sdk = _install_client(monkeypatch)
    sdk.magic_link.start.return_value = None
    for i in range(10):
        resp = client.post(
            "/api/v1/auth/magic-link/request",
            json={"email": f"fan{i}@example.test"},
        )
        assert resp.status_code == 200, f"call {i} unexpectedly blocked"
    resp = client.post(
        "/api/v1/auth/magic-link/request",
        json={"email": "fan-final@example.test"},
    )
    assert resp.status_code == 429
    assert resp.get_json()["error"]["code"] == "RATE_LIMITED"
    assert resp.headers.get("Retry-After") == "30"

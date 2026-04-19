"""Unit tests for the WebAuthn passkey service.

The crypto-heavy pieces live in the ``webauthn`` package; here we patch
its four entry points (``generate_registration_options``,
``verify_registration_response``, ``generate_authentication_options``,
``verify_authentication_response``) so the orchestrator can be exercised
without a real authenticator in the loop.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.core.exceptions import (
    PASSKEY_AUTH_FAILED,
    PASSKEY_REGISTRATION_FAILED,
    AppError,
)
from backend.data.models.users import User
from backend.services import auth as auth_service


def _configure_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install a deterministic settings stub for the passkey flow."""
    fake = MagicMock()
    fake.webauthn_rp_id = "greenroom.test"
    fake.webauthn_rp_name = "Greenroom"
    fake.webauthn_origin = "https://greenroom.test"
    fake.jwt_secret_key = "test-secret"
    fake.jwt_expiry_seconds = 3600
    monkeypatch.setattr(auth_service, "get_settings", lambda: fake)


# ---------------------------------------------------------------------------
# registration_options
# ---------------------------------------------------------------------------


def test_registration_options_returns_options_and_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The start call returns JSON options and a signed state token."""
    _configure_settings(monkeypatch)

    user = User(id=uuid.uuid4(), email="pat@example.test", display_name="Pat")

    monkeypatch.setattr(
        auth_service.passkeys_repo, "list_by_user", lambda *_a, **_k: []
    )

    fake_opts = MagicMock(challenge=b"chal-bytes-xxxxx")
    monkeypatch.setattr(
        auth_service.webauthn, "generate_registration_options", lambda **_: fake_opts
    )
    monkeypatch.setattr(
        auth_service.webauthn,
        "options_to_json",
        lambda opts: '{"challenge":"c","user":{"id":"u"}}',
    )

    result = auth_service.passkey_registration_options(MagicMock(), user=user)
    assert "challenge" in result.options
    assert isinstance(result.state, str) and result.state


def test_registration_options_excludes_existing_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Existing credentials are passed as ``excludeCredentials`` to prevent dupes."""
    _configure_settings(monkeypatch)
    user = User(id=uuid.uuid4(), email="pat@example.test")

    # The library call receives our decoded bytes list — capture the kwargs.
    captured: dict[str, Any] = {}

    def fake_gen(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return MagicMock(challenge=b"chal-xxxxxxxxx")

    existing_cred = MagicMock()
    # base64url for the ASCII string "prev-id-bytes" (no padding)
    existing_cred.credential_id = "cHJldi1pZC1ieXRlcw"
    monkeypatch.setattr(
        auth_service.passkeys_repo, "list_by_user", lambda *_a, **_k: [existing_cred]
    )
    monkeypatch.setattr(
        auth_service.webauthn, "generate_registration_options", fake_gen
    )
    monkeypatch.setattr(auth_service.webauthn, "options_to_json", lambda _o: "{}")

    auth_service.passkey_registration_options(MagicMock(), user=user)

    exclude = captured.get("exclude_credentials")
    assert exclude is not None and len(exclude) == 1


# ---------------------------------------------------------------------------
# register_complete
# ---------------------------------------------------------------------------


def _issue_reg_state(
    monkeypatch: pytest.MonkeyPatch, *, user_id: uuid.UUID, challenge: str
) -> str:
    """Mint a registration state JWT exactly as the service would."""
    _configure_settings(monkeypatch)
    return auth_service._issue_passkey_state(
        purpose="passkey_register_challenge",
        claims={"user_id": str(user_id), "challenge": challenge},
    )


def test_register_complete_persists_credential_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful attestation writes a row via ``passkeys_repo.create``."""
    user = User(id=uuid.uuid4(), email="pat@example.test")
    state = _issue_reg_state(monkeypatch, user_id=user.id, challenge="Y2hhbA")

    verification = MagicMock(
        credential_id=b"cred-id-bytes",
        credential_public_key=b"pub-key-bytes",
        sign_count=0,
    )
    monkeypatch.setattr(
        auth_service.webauthn,
        "verify_registration_response",
        lambda **_: verification,
    )
    create_mock = MagicMock()
    monkeypatch.setattr(auth_service.passkeys_repo, "create", create_mock)

    credential = {
        "id": "cred-id",
        "response": {"transports": ["internal", "hybrid"]},
    }
    auth_service.passkey_register_complete(
        MagicMock(),
        user=user,
        credential=credential,
        state=state,
        name="iPhone",
    )
    create_mock.assert_called_once()
    kwargs = create_mock.call_args.kwargs
    assert kwargs["user_id"] == user.id
    assert kwargs["transports"] == "internal,hybrid"
    assert kwargs["name"] == "iPhone"


def test_register_complete_rejects_state_for_different_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A state minted for a different user must not be redeemable."""
    other_user = User(id=uuid.uuid4(), email="other@example.test")
    state = _issue_reg_state(monkeypatch, user_id=other_user.id, challenge="Y2hhbA")

    caller = User(id=uuid.uuid4(), email="pat@example.test")
    with pytest.raises(AppError) as exc:
        auth_service.passkey_register_complete(
            MagicMock(), user=caller, credential={}, state=state
        )
    assert exc.value.code == PASSKEY_REGISTRATION_FAILED


def test_register_complete_rejects_bad_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A garbage state token is rejected before touching ``webauthn``."""
    _configure_settings(monkeypatch)
    with pytest.raises(AppError) as exc:
        auth_service.passkey_register_complete(
            MagicMock(),
            user=User(id=uuid.uuid4(), email="pat@example.test"),
            credential={},
            state="not-a-jwt",
        )
    assert exc.value.code == PASSKEY_REGISTRATION_FAILED


def test_register_complete_rejects_wrong_purpose_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid JWT minted for the auth ceremony must not register."""
    _configure_settings(monkeypatch)
    user = User(id=uuid.uuid4(), email="pat@example.test")
    wrong_state = auth_service._issue_passkey_state(
        purpose="passkey_auth_challenge",
        claims={"challenge": "Y2hhbA"},
    )
    with pytest.raises(AppError) as exc:
        auth_service.passkey_register_complete(
            MagicMock(), user=user, credential={}, state=wrong_state
        )
    assert exc.value.code == PASSKEY_REGISTRATION_FAILED


def test_register_complete_library_error_becomes_app_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Any exception from the webauthn lib maps to PASSKEY_REGISTRATION_FAILED."""
    user = User(id=uuid.uuid4(), email="pat@example.test")
    state = _issue_reg_state(monkeypatch, user_id=user.id, challenge="Y2hhbA")

    def boom(**_: Any) -> Any:
        raise ValueError("attestation mismatch")

    monkeypatch.setattr(auth_service.webauthn, "verify_registration_response", boom)

    with pytest.raises(AppError) as exc:
        auth_service.passkey_register_complete(
            MagicMock(), user=user, credential={"id": "c"}, state=state
        )
    assert exc.value.code == PASSKEY_REGISTRATION_FAILED


# ---------------------------------------------------------------------------
# authentication_options
# ---------------------------------------------------------------------------


def test_authentication_options_returns_options_and_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The start call returns options + a state token for the auth ceremony."""
    _configure_settings(monkeypatch)
    fake_opts = MagicMock(challenge=b"auth-challenge-bytes")
    monkeypatch.setattr(
        auth_service.webauthn, "generate_authentication_options", lambda **_: fake_opts
    )
    monkeypatch.setattr(
        auth_service.webauthn, "options_to_json", lambda _o: '{"challenge":"a"}'
    )

    result = auth_service.passkey_authentication_options()
    assert "challenge" in result.options
    assert isinstance(result.state, str)


# ---------------------------------------------------------------------------
# authenticate_complete
# ---------------------------------------------------------------------------


def _issue_auth_state(
    monkeypatch: pytest.MonkeyPatch, *, challenge: str = "Y2hhbA"
) -> str:
    """Mint an authentication state JWT exactly as the service would."""
    _configure_settings(monkeypatch)
    return auth_service._issue_passkey_state(
        purpose="passkey_auth_challenge",
        claims={"challenge": challenge},
    )


def test_authenticate_complete_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """A valid assertion bumps the sign count and returns a signed JWT."""
    state = _issue_auth_state(monkeypatch)
    user = User(id=uuid.uuid4(), email="pat@example.test", is_active=True)
    stored = MagicMock(user=user, public_key="cHVi", sign_count=5)
    monkeypatch.setattr(
        auth_service.passkeys_repo, "get_by_credential_id", lambda *_a, **_k: stored
    )
    update_count = MagicMock()
    monkeypatch.setattr(auth_service.passkeys_repo, "update_sign_count", update_count)
    monkeypatch.setattr(auth_service.users_repo, "update_last_login", MagicMock())

    verification = MagicMock(new_sign_count=6)
    monkeypatch.setattr(
        auth_service.webauthn,
        "verify_authentication_response",
        lambda **_: verification,
    )

    credential = {"id": "cred-id", "response": {}}
    login = auth_service.passkey_authenticate_complete(
        MagicMock(), credential=credential, state=state
    )
    assert login.user is user
    assert isinstance(login.jwt, str) and login.jwt
    update_count.assert_called_once()
    assert update_count.call_args.kwargs["new_count"] == 6


def test_authenticate_complete_unknown_credential_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A credential id with no stored row raises PASSKEY_AUTH_FAILED."""
    state = _issue_auth_state(monkeypatch)
    monkeypatch.setattr(
        auth_service.passkeys_repo, "get_by_credential_id", lambda *_a, **_k: None
    )
    with pytest.raises(AppError) as exc:
        auth_service.passkey_authenticate_complete(
            MagicMock(), credential={"id": "nope"}, state=state
        )
    assert exc.value.code == PASSKEY_AUTH_FAILED


def test_authenticate_complete_deactivated_user_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deactivated user must never get a JWT via a passkey."""
    state = _issue_auth_state(monkeypatch)
    dead = User(id=uuid.uuid4(), email="dead@example.test", is_active=False)
    monkeypatch.setattr(
        auth_service.passkeys_repo,
        "get_by_credential_id",
        lambda *_a, **_k: MagicMock(user=dead),
    )
    with pytest.raises(AppError) as exc:
        auth_service.passkey_authenticate_complete(
            MagicMock(), credential={"id": "cred"}, state=state
        )
    assert exc.value.code == PASSKEY_AUTH_FAILED


def test_authenticate_complete_library_error_becomes_app_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Any exception from the webauthn lib maps to PASSKEY_AUTH_FAILED."""
    state = _issue_auth_state(monkeypatch)
    user = User(id=uuid.uuid4(), email="pat@example.test", is_active=True)
    stored = MagicMock(user=user, public_key="cHVi", sign_count=0)
    monkeypatch.setattr(
        auth_service.passkeys_repo, "get_by_credential_id", lambda *_a, **_k: stored
    )

    def boom(**_: Any) -> Any:
        raise ValueError("signature mismatch")

    monkeypatch.setattr(auth_service.webauthn, "verify_authentication_response", boom)

    with pytest.raises(AppError) as exc:
        auth_service.passkey_authenticate_complete(
            MagicMock(), credential={"id": "cred"}, state=state
        )
    assert exc.value.code == PASSKEY_AUTH_FAILED


def test_authenticate_complete_missing_credential_id_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A malformed assertion without an id must not reach the library."""
    state = _issue_auth_state(monkeypatch)
    with pytest.raises(AppError) as exc:
        auth_service.passkey_authenticate_complete(
            MagicMock(), credential={"response": {}}, state=state
        )
    assert exc.value.code == PASSKEY_AUTH_FAILED

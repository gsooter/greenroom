"""Unit tests for the magic-link sign-in flow in :mod:`backend.services.auth`.

These tests stub out the SendGrid client and the session so they can
run without a database.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.core.exceptions import (
    MAGIC_LINK_ALREADY_USED,
    MAGIC_LINK_EXPIRED,
    MAGIC_LINK_INVALID,
    AppError,
)
from backend.data.models.users import MagicLinkToken, User
from backend.services import auth as auth_service

# ---------------------------------------------------------------------------
# generate_magic_link
# ---------------------------------------------------------------------------


def test_generate_magic_link_returns_raw_token_and_stores_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The raw token is returned to the caller; only its hash is persisted."""
    session = MagicMock()

    captured: dict[str, Any] = {}

    def fake_create(_s: Any, **kwargs: Any) -> MagicLinkToken:
        """Capture what the repo saw so the test can inspect the hash."""
        captured.update(kwargs)
        row = MagicLinkToken(
            id=uuid.uuid4(),
            email=kwargs["email"],
            token_hash=kwargs["token_hash"],
            expires_at=kwargs["expires_at"],
        )
        return row

    monkeypatch.setattr(auth_service.magic_links_repo, "create", fake_create)
    sent = MagicMock()
    monkeypatch.setattr(auth_service.email_service, "send_email", sent)

    result = auth_service.generate_magic_link(session, email="Pat@Example.TEST")

    # The caller needs the raw token to have been emailed but must never
    # see it again — the repo gets the hash only.
    assert result.raw_token  # non-empty
    assert (
        captured["token_hash"]
        == hashlib.sha256(result.raw_token.encode("utf-8")).hexdigest()
    )
    # Email is lowercased so the lookup key is stable across casings.
    assert captured["email"] == "pat@example.test"
    # TTL lands in the future.
    assert captured["expires_at"] > datetime.now(UTC)
    # And SendGrid was called.
    sent.assert_called_once()


def test_generate_magic_link_includes_token_in_email_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The email HTML contains the verify URL with the raw token as query."""
    session = MagicMock()
    monkeypatch.setattr(
        auth_service.magic_links_repo,
        "create",
        lambda _s, **kwargs: MagicLinkToken(
            id=uuid.uuid4(),
            email=kwargs["email"],
            token_hash=kwargs["token_hash"],
            expires_at=kwargs["expires_at"],
        ),
    )
    calls: dict[str, Any] = {}

    def capture(**kwargs: Any) -> None:
        calls.update(kwargs)

    monkeypatch.setattr(auth_service.email_service, "send_email", capture)

    result = auth_service.generate_magic_link(session, email="pat@example.test")
    assert result.raw_token in calls["html_body"]
    assert "/auth/verify" in calls["html_body"]


def test_generate_magic_link_rejects_empty_email() -> None:
    """An empty/whitespace email is rejected before any token is minted."""
    session = MagicMock()
    with pytest.raises(AppError):
        auth_service.generate_magic_link(session, email="   ")


# ---------------------------------------------------------------------------
# verify_magic_link
# ---------------------------------------------------------------------------


def _mint_token_row(
    *,
    used_at: datetime | None = None,
    expires_at: datetime | None = None,
    email: str = "pat@example.test",
) -> MagicLinkToken:
    """Build a MagicLinkToken row for unit tests without touching the DB.

    Args:
        used_at: Pre-populate ``used_at`` to simulate already-redeemed.
        expires_at: Override the expiry; defaults to 10 minutes ahead.
        email: Email associated with the token.

    Returns:
        A :class:`MagicLinkToken` populated for the scenario.
    """
    return MagicLinkToken(
        id=uuid.uuid4(),
        email=email,
        token_hash="irrelevant",
        expires_at=expires_at or datetime.now(UTC) + timedelta(minutes=10),
        used_at=used_at,
    )


def test_verify_magic_link_unknown_token_raises_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A token that never existed raises MAGIC_LINK_INVALID."""
    monkeypatch.setattr(
        auth_service.magic_links_repo, "get_by_hash", lambda _s, _h: None
    )
    with pytest.raises(AppError) as exc:
        auth_service.verify_magic_link(MagicMock(), token="whatever")
    assert exc.value.code == MAGIC_LINK_INVALID


def test_verify_magic_link_expired_raises_expired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A past ``expires_at`` raises MAGIC_LINK_EXPIRED."""
    expired = _mint_token_row(expires_at=datetime.now(UTC) - timedelta(minutes=1))
    monkeypatch.setattr(
        auth_service.magic_links_repo, "get_by_hash", lambda _s, _h: expired
    )
    with pytest.raises(AppError) as exc:
        auth_service.verify_magic_link(MagicMock(), token="any")
    assert exc.value.code == MAGIC_LINK_EXPIRED


def test_verify_magic_link_already_used_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A token with used_at already set cannot be redeemed twice."""
    used = _mint_token_row(used_at=datetime.now(UTC))
    monkeypatch.setattr(
        auth_service.magic_links_repo, "get_by_hash", lambda _s, _h: used
    )
    with pytest.raises(AppError) as exc:
        auth_service.verify_magic_link(MagicMock(), token="any")
    assert exc.value.code == MAGIC_LINK_ALREADY_USED


def test_verify_magic_link_creates_user_on_first_visit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No user exists for the email → create one and issue a JWT."""
    token_row = _mint_token_row(email="new@example.test")
    monkeypatch.setattr(
        auth_service.magic_links_repo, "get_by_hash", lambda _s, _h: token_row
    )
    mark_used = MagicMock()
    monkeypatch.setattr(auth_service.magic_links_repo, "mark_used", mark_used)
    monkeypatch.setattr(
        auth_service.users_repo, "get_user_by_email", lambda _s, _e: None
    )
    created = User(
        id=uuid.uuid4(),
        email="new@example.test",
        is_active=True,
    )
    monkeypatch.setattr(
        auth_service.users_repo,
        "create_user",
        lambda _s, **_k: created,
    )
    monkeypatch.setattr(auth_service.users_repo, "update_last_login", MagicMock())

    result = auth_service.verify_magic_link(MagicMock(), token="abc")

    assert result.user is created
    assert isinstance(result.jwt, str) and result.jwt
    mark_used.assert_called_once()


def test_verify_magic_link_returns_existing_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A user already exists for the email → reuse them, don't create."""
    token_row = _mint_token_row(email="pat@example.test")
    existing = User(id=uuid.uuid4(), email="pat@example.test", is_active=True)

    monkeypatch.setattr(
        auth_service.magic_links_repo, "get_by_hash", lambda _s, _h: token_row
    )
    monkeypatch.setattr(auth_service.magic_links_repo, "mark_used", MagicMock())
    monkeypatch.setattr(
        auth_service.users_repo, "get_user_by_email", lambda _s, _e: existing
    )
    create_user = MagicMock()
    monkeypatch.setattr(auth_service.users_repo, "create_user", create_user)
    monkeypatch.setattr(auth_service.users_repo, "update_last_login", MagicMock())

    result = auth_service.verify_magic_link(MagicMock(), token="abc")

    assert result.user is existing
    create_user.assert_not_called()


def test_verify_magic_link_rejects_deactivated_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A user with is_active=False cannot complete a magic-link login."""
    token_row = _mint_token_row(email="pat@example.test")
    dead = User(id=uuid.uuid4(), email="pat@example.test", is_active=False)

    monkeypatch.setattr(
        auth_service.magic_links_repo, "get_by_hash", lambda _s, _h: token_row
    )
    monkeypatch.setattr(
        auth_service.users_repo, "get_user_by_email", lambda _s, _e: dead
    )

    with pytest.raises(AppError) as exc:
        auth_service.verify_magic_link(MagicMock(), token="abc")
    assert exc.value.code == MAGIC_LINK_INVALID

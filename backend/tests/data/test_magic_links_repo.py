"""Repository tests for :mod:`backend.data.repositories.magic_links`."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from backend.data.models.users import MagicLinkToken, User
from backend.data.repositories import magic_links as magic_links_repo


def _future(minutes: int = 15) -> datetime:
    """Return a tz-aware UTC timestamp ``minutes`` from now.

    Args:
        minutes: Minutes in the future. Defaults to 15 (default TTL).

    Returns:
        The target UTC datetime.
    """
    return datetime.now(UTC) + timedelta(minutes=minutes)


def _past(minutes: int = 5) -> datetime:
    """Return a tz-aware UTC timestamp ``minutes`` in the past.

    Args:
        minutes: Minutes in the past.

    Returns:
        The target UTC datetime.
    """
    return datetime.now(UTC) - timedelta(minutes=minutes)


def test_create_magic_link_persists_hash_and_metadata(
    session: Session,
) -> None:
    """create() writes the hash exactly as supplied and returns the row."""
    token = magic_links_repo.create(
        session,
        email="pat@example.test",
        token_hash="a" * 64,
        expires_at=_future(),
    )
    assert token.id is not None
    assert token.email == "pat@example.test"
    assert token.token_hash == "a" * 64
    assert token.used_at is None
    assert token.user_id is None


def test_get_by_hash_returns_matching_row(session: Session) -> None:
    """Lookup by hash returns the only row with that hash."""
    magic_links_repo.create(
        session,
        email="pat@example.test",
        token_hash="b" * 64,
        expires_at=_future(),
    )
    fetched = magic_links_repo.get_by_hash(session, "b" * 64)
    assert fetched is not None
    assert fetched.email == "pat@example.test"


def test_get_by_hash_returns_none_when_missing(session: Session) -> None:
    """An unknown hash yields None, not an exception."""
    assert magic_links_repo.get_by_hash(session, "z" * 64) is None


def test_mark_used_sets_used_at_and_links_user(
    session: Session, make_user: Callable[..., User]
) -> None:
    """mark_used() stamps used_at and optionally records the user."""
    user = make_user()
    token = magic_links_repo.create(
        session,
        email=user.email,
        token_hash="c" * 64,
        expires_at=_future(),
    )
    magic_links_repo.mark_used(session, token, user_id=user.id)
    assert token.used_at is not None
    assert token.user_id == user.id


def test_delete_expired_removes_rows_older_than_cutoff(
    session: Session,
) -> None:
    """delete_expired() removes only rows whose expires_at is past the cutoff."""
    magic_links_repo.create(
        session,
        email="a@example.test",
        token_hash="d" * 64,
        expires_at=_past(minutes=60),
    )
    fresh = magic_links_repo.create(
        session,
        email="b@example.test",
        token_hash="e" * 64,
        expires_at=_future(),
    )
    removed = magic_links_repo.delete_expired(session, older_than=_past(minutes=30))
    assert removed == 1
    assert magic_links_repo.get_by_hash(session, "e" * 64) is not None
    assert session.get(MagicLinkToken, fresh.id) is not None

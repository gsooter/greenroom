"""Repository tests for :mod:`backend.data.repositories.passkeys`."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.orm import Session

from backend.data.models.users import User
from backend.data.repositories import passkeys as passkeys_repo


def test_create_persists_credential_with_zero_sign_count(
    session: Session, make_user: Callable[..., User]
) -> None:
    """Newly registered credentials start at sign_count=0."""
    user = make_user()
    cred = passkeys_repo.create(
        session,
        user_id=user.id,
        credential_id="cred-abc",
        public_key="pk-xyz",
        transports="internal,hybrid",
        name="iPhone",
    )
    assert cred.user_id == user.id
    assert cred.credential_id == "cred-abc"
    assert cred.sign_count == 0
    assert cred.transports == "internal,hybrid"
    assert cred.name == "iPhone"


def test_get_by_credential_id_roundtrips(
    session: Session, make_user: Callable[..., User]
) -> None:
    """Lookup by credential_id returns the persisted row."""
    user = make_user()
    passkeys_repo.create(
        session,
        user_id=user.id,
        credential_id="cred-42",
        public_key="pk",
    )
    fetched = passkeys_repo.get_by_credential_id(session, "cred-42")
    assert fetched is not None
    assert fetched.user_id == user.id


def test_get_by_credential_id_missing_returns_none(
    session: Session,
) -> None:
    """An unknown credential_id yields None."""
    assert passkeys_repo.get_by_credential_id(session, "nope") is None


def test_list_by_user_orders_by_created(
    session: Session, make_user: Callable[..., User]
) -> None:
    """list_by_user() returns all creds for a user."""
    user = make_user()
    passkeys_repo.create(session, user_id=user.id, credential_id="c1", public_key="p")
    passkeys_repo.create(session, user_id=user.id, credential_id="c2", public_key="p")
    rows = passkeys_repo.list_by_user(session, user.id)
    assert {c.credential_id for c in rows} == {"c1", "c2"}


def test_update_sign_count_and_touch_last_used(
    session: Session, make_user: Callable[..., User]
) -> None:
    """update_sign_count() bumps the counter and stamps last_used_at."""
    user = make_user()
    cred = passkeys_repo.create(
        session, user_id=user.id, credential_id="c1", public_key="p"
    )
    assert cred.last_used_at is None
    passkeys_repo.update_sign_count(session, cred, new_count=5)
    assert cred.sign_count == 5
    assert cred.last_used_at is not None

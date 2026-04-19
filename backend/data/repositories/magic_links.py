"""Repository functions for magic-link sign-in tokens.

All DB access for :class:`backend.data.models.users.MagicLinkToken`
lives here. Services call these functions; no other module touches the
table directly (Decision 027).
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.data.models.users import MagicLinkToken


def create(
    session: Session,
    *,
    email: str,
    token_hash: str,
    expires_at: datetime,
) -> MagicLinkToken:
    """Insert a new magic-link token row.

    The caller is responsible for hashing the raw token before passing
    it here. Never store the raw token — the email is the only place
    the raw value should ever appear (Decision 027).

    Args:
        session: Active SQLAlchemy session.
        email: Email address the link is being issued to.
        token_hash: SHA-256 hex digest of the raw token.
        expires_at: Wall-clock UTC time after which this token is
            invalid regardless of redemption state.

    Returns:
        The newly persisted :class:`MagicLinkToken`.
    """
    row = MagicLinkToken(
        email=email,
        token_hash=token_hash,
        expires_at=expires_at,
    )
    session.add(row)
    session.flush()
    return row


def get_by_hash(session: Session, token_hash: str) -> MagicLinkToken | None:
    """Fetch a magic-link token by its hash column.

    Args:
        session: Active SQLAlchemy session.
        token_hash: SHA-256 hex digest to look up.

    Returns:
        The :class:`MagicLinkToken` row if one exists, else None.
    """
    stmt = select(MagicLinkToken).where(MagicLinkToken.token_hash == token_hash)
    return session.execute(stmt).scalar_one_or_none()


def mark_used(
    session: Session,
    token: MagicLinkToken,
    *,
    user_id: uuid.UUID | None = None,
) -> MagicLinkToken:
    """Stamp a token as redeemed and optionally record the user who redeemed it.

    Args:
        session: Active SQLAlchemy session.
        token: Token row to mark.
        user_id: The user the redemption authenticated; None when the
            caller wants to record consumption without binding a user
            (e.g. a test-only path).

    Returns:
        The updated :class:`MagicLinkToken`.
    """
    token.used_at = datetime.now(UTC)
    if user_id is not None:
        token.user_id = user_id
    session.flush()
    return token


def delete_expired(session: Session, *, older_than: datetime) -> int:
    """Delete rows whose ``expires_at`` is at or before ``older_than``.

    Called by a nightly cleanup task so the table stays small. Using a
    single ``DELETE ... WHERE`` keeps the operation O(1) round-trips.

    Args:
        session: Active SQLAlchemy session.
        older_than: Any row with ``expires_at <= older_than`` is removed.

    Returns:
        Number of rows deleted.
    """
    stmt = delete(MagicLinkToken).where(MagicLinkToken.expires_at <= older_than)
    result = session.execute(stmt)
    session.flush()
    return int(result.rowcount or 0)

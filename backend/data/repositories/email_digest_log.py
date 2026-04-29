"""Repository for the ``email_digest_log`` table.

Every successful digest send appends a row here. The two callers are:

* The send pipeline writes the row after Resend accepts the message
  (used by the weekly-cap and idempotency guards).
* The Resend webhook handler updates the row when an open/click
  callback fires for the matching ``provider_message_id``.

Read access is split between counting recent sends (cap enforcement)
and looking up the most recent log of a given digest type for a user
(idempotency).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from backend.data.models.notifications import EmailDigestLog

if TYPE_CHECKING:
    import uuid
    from datetime import datetime

    from sqlalchemy.orm import Session


def count_recent_for_user(
    session: Session,
    user_id: uuid.UUID,
    since: datetime,
) -> int:
    """Count digest log rows for a user with ``sent_at >= since``.

    Used by the weekly-cap guard to decide whether the user has
    already received their configured ``max_emails_per_week`` quota
    in the trailing window.

    Args:
        session: Active SQLAlchemy session.
        user_id: UUID of the user.
        since: Lower bound (inclusive) on ``sent_at``. Typically
            ``now - 7 days``.

    Returns:
        The number of matching log rows.
    """
    stmt = (
        select(func.count())
        .select_from(EmailDigestLog)
        .where(EmailDigestLog.user_id == user_id)
        .where(EmailDigestLog.sent_at >= since)
    )
    return int(session.execute(stmt).scalar_one())


def get_most_recent_for_type(
    session: Session,
    user_id: uuid.UUID,
    digest_type: str,
) -> EmailDigestLog | None:
    """Return the most recent digest log row of a given type for a user.

    Used by the per-type idempotency guard so a re-run of the
    dispatcher within the same hour doesn't double-send.

    Args:
        session: Active SQLAlchemy session.
        user_id: UUID of the user.
        digest_type: ``"weekly"``, ``"staff_picks"``, etc. — matched
            exactly against the ``digest_type`` column.

    Returns:
        The most recent matching row, or None when the user has never
        received this digest type.
    """
    stmt = (
        select(EmailDigestLog)
        .where(EmailDigestLog.user_id == user_id)
        .where(EmailDigestLog.digest_type == digest_type)
        .order_by(EmailDigestLog.sent_at.desc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def create_log(
    session: Session,
    *,
    user_id: uuid.UUID,
    digest_type: str,
    event_count: int,
    sent_at: datetime,
    provider_message_id: str | None = None,
    metadata_json: dict[str, Any] | None = None,
) -> EmailDigestLog:
    """Insert a new ``email_digest_log`` row and flush it.

    Args:
        session: Active SQLAlchemy session.
        user_id: Recipient UUID.
        digest_type: Short type label (``"weekly"``, ``"staff_picks"``,
            …) used by the weekly-cap and idempotency guards.
        event_count: How many show cards the email featured.
        sent_at: Wall-clock time the send was accepted by Resend.
        provider_message_id: Resend's message ID, used later to
            attach open/click webhook events to this row.
        metadata_json: Free-form metadata captured at send time
            (e.g., the digest's headlining show ids). Optional.

    Returns:
        The freshly-inserted log row.
    """
    row = EmailDigestLog(
        user_id=user_id,
        digest_type=digest_type,
        event_count=event_count,
        sent_at=sent_at,
        provider_message_id=provider_message_id,
        metadata_json=metadata_json,
    )
    session.add(row)
    session.flush()
    return row

"""Repository for ``push_subscriptions`` rows.

The service layer never touches SQL directly; every read or write
funnels through the helpers below. Each function takes a
``Session``, mutates only what the docstring says, and returns
either ORM rows or ``None``.

The two write helpers worth understanding:

* :func:`upsert` — used by the subscribe endpoint. Same browser
  re-subscribing (e.g. after a key rotation) must update the existing
  row rather than create a duplicate, so we ON CONFLICT DO UPDATE
  on the ``(user_id, endpoint)`` unique constraint.
* :func:`disable_subscription` — used by the dispatcher when a push
  service responds with HTTP 404 / 410 ("endpoint gone"). Stamps
  ``disabled_at`` so subsequent reads skip the row.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from backend.data.models.push import PushSubscription

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.orm import Session


def upsert(
    session: Session,
    *,
    user_id: uuid.UUID,
    endpoint: str,
    p256dh_key: str,
    auth_key: str,
    user_agent: str | None,
) -> PushSubscription:
    """Insert or refresh a push subscription for ``(user_id, endpoint)``.

    Args:
        session: Active SQLAlchemy session.
        user_id: UUID of the subscribing user.
        endpoint: Push-service URL the browser supplied.
        p256dh_key: Browser's P-256 public key (base64url).
        auth_key: Browser's auth secret (base64url).
        user_agent: Optional ``User-Agent`` snapshot at subscribe time.

    Returns:
        The persisted :class:`PushSubscription`. New rows have
        ``failure_count=0`` and ``disabled_at=NULL``; resubscribes
        also clear those fields so a previously-disabled endpoint
        comes back to life.
    """
    stmt = (
        pg_insert(PushSubscription)
        .values(
            user_id=user_id,
            endpoint=endpoint,
            p256dh_key=p256dh_key,
            auth_key=auth_key,
            user_agent=user_agent,
            failure_count=0,
            disabled_at=None,
        )
        .on_conflict_do_update(
            constraint="uq_push_sub_user_endpoint",
            set_={
                "p256dh_key": p256dh_key,
                "auth_key": auth_key,
                "user_agent": user_agent,
                "failure_count": 0,
                "disabled_at": None,
                "updated_at": datetime.now(UTC),
            },
        )
        .returning(PushSubscription.id)
    )
    row_id = session.execute(stmt).scalar_one()
    session.flush()
    return session.get(PushSubscription, row_id)  # type: ignore[return-value]


def list_active_for_user(
    session: Session, user_id: uuid.UUID
) -> list[PushSubscription]:
    """List every non-disabled subscription belonging to ``user_id``.

    Args:
        session: Active SQLAlchemy session.
        user_id: UUID of the user.

    Returns:
        All rows where ``disabled_at IS NULL``. Order is unspecified
        because the dispatcher fans out to each endpoint independently.
    """
    return list(
        session.execute(
            select(PushSubscription).where(
                PushSubscription.user_id == user_id,
                PushSubscription.disabled_at.is_(None),
            )
        )
        .scalars()
        .all()
    )


def delete_for_endpoint(session: Session, user_id: uuid.UUID, endpoint: str) -> bool:
    """Hard-delete a subscription row by ``(user_id, endpoint)``.

    Args:
        session: Active SQLAlchemy session.
        user_id: UUID of the user.
        endpoint: The push-service URL the row was inserted with.

    Returns:
        True if a row was deleted; False if no matching row existed.
    """
    row = session.execute(
        select(PushSubscription).where(
            PushSubscription.user_id == user_id,
            PushSubscription.endpoint == endpoint,
        )
    ).scalar_one_or_none()
    if row is None:
        return False
    session.delete(row)
    session.flush()
    return True


def record_success(session: Session, subscription: PushSubscription) -> None:
    """Stamp ``last_successful_send_at`` and reset ``failure_count``.

    Args:
        session: Active SQLAlchemy session.
        subscription: The row that just received a 2xx push response.
    """
    subscription.last_successful_send_at = datetime.now(UTC)
    subscription.failure_count = 0
    session.flush()


def record_failure(session: Session, subscription: PushSubscription) -> None:
    """Bump ``failure_count`` and stamp ``last_failure_at``.

    Args:
        session: Active SQLAlchemy session.
        subscription: The row that just received a non-2xx response.
    """
    subscription.failure_count = (subscription.failure_count or 0) + 1
    subscription.last_failure_at = datetime.now(UTC)
    session.flush()


def disable_subscription(session: Session, subscription: PushSubscription) -> None:
    """Mark a subscription as permanently disabled.

    Used when the push service signals the endpoint is gone (404/410)
    or after the failure ceiling is tripped.

    Args:
        session: Active SQLAlchemy session.
        subscription: The row to disable.
    """
    if subscription.disabled_at is None:
        subscription.disabled_at = datetime.now(UTC)
        session.flush()

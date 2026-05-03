"""Append-only log of every notification dispatched per channel.

Two responsibilities live on this table:

* **Deduplication** — the unique constraint on
  ``(user_id, notification_type, dedupe_key, channel)`` is the lock
  the dispatcher relies on to make sure a single trigger never fans
  out twice. The dispatcher tries to insert before sending; a
  ``UniqueViolation`` short-circuits the send.
* **Auditability** — ops needs to answer "did this user actually get
  the day-before push for this show?" without reading the worker log
  retention. The row carries enough payload (``notification_type``,
  ``channel``, ``payload``) to answer that without joining anywhere
  else.

The ``EmailDigestLog`` table predates this one and remains the
canonical record for digest delivery telemetry (open/click/bounce).
``NotificationLog`` is *additionally* written for digests so the
dispatcher's view of the world is uniform across channels — there is
no special-casing the digest as "the one type that doesn't appear in
the log."
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.database import Base


class NotificationLog(Base):
    """One row per (user x notification x channel) successful dispatch.

    Attributes:
        id: Primary key.
        user_id: UUID of the recipient. ``ON DELETE CASCADE`` removes
            log rows when an account is deleted.
        notification_type: Stable string identifier of the trigger
            (``tour_announcement``, ``show_reminder_24h``,
            ``weekly_digest``, etc.).
        dedupe_key: Trigger-specific key that, combined with
            ``user_id`` + ``notification_type`` + ``channel``, makes
            this dispatch unique. For event-anchored notifications it
            is the event UUID; for the weekly digest it is an
            ISO week string (``"2026-W18"``).
        channel: ``"push"`` or ``"email"``.
        sent_at: When the dispatcher persisted this row. Defaults to
            ``now()`` server-side so log writes never trip on a
            mismatched application clock.
        payload: Optional JSONB blob for diagnostics (top-level event
            ids, render counts, etc.). Never store anything users would
            consider sensitive.
    """

    __tablename__ = "notification_log"

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "notification_type",
            "dedupe_key",
            "channel",
            name="uq_notification_log_dedupe",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    notification_type: Mapped[str] = mapped_column(String(40), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(80), nullable=False)
    channel: Mapped[str] = mapped_column(String(10), nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    def __repr__(self) -> str:
        """Return a string representation for log lines.

        Returns:
            ``<NotificationLog user=… type=… channel=…>``.
        """
        return (
            f"<NotificationLog user={self.user_id} "
            f"type={self.notification_type} channel={self.channel}>"
        )

"""SQLAlchemy ORM models for email digest tracking.

Stores a log of sent digests with open/click tracking data
fed back from SendGrid webhooks (Decision 012).
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.database import Base, TimestampMixin


class EmailDigestLog(TimestampMixin, Base):
    """A log of an email digest sent to a user.

    Tracks delivery, opens, and clicks via SendGrid webhook data.

    Attributes:
        id: Unique identifier for the digest log entry.
        user_id: Foreign key to the recipient user.
        digest_type: Type of digest (e.g., "weekly", "daily").
        event_count: Number of events included in the digest.
        sent_at: When the email was sent.
        sendgrid_message_id: SendGrid message ID for tracking.
        opened_at: When the email was first opened, if tracked.
        clicked_at: When the email was first clicked, if tracked.
        metadata_json: Additional digest metadata as JSONB.
    """

    __tablename__ = "email_digest_log"

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
    digest_type: Mapped[str] = mapped_column(String(20), nullable=False)
    event_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sendgrid_message_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    opened_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    clicked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    def __repr__(self) -> str:
        """Return a string representation of the EmailDigestLog.

        Returns:
            String representation with user ID and digest type.
        """
        return f"<EmailDigestLog user={self.user_id} " f"type={self.digest_type}>"

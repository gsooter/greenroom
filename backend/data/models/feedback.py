"""SQLAlchemy ORM model for the in-app beta feedback widget.

Backs the persistent "leave feedback" pill that appears on every page.
A submission is intentionally lightweight — one freeform message plus a
``kind`` enum (bug / feature / general). Nothing here is a content
moderation system; it is a private channel from beta users to ops.

Submissions can come from logged-in or anonymous browsers. When the
user is logged in, ``user_id`` is filled in and the email is whatever
their account email is at submit time. Anonymous users may optionally
provide an email so we can reply.
"""

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from backend.core.database import Base

if TYPE_CHECKING:
    from backend.data.models.users import User


class FeedbackKind(enum.StrEnum):
    """High-level category the submitter picked from the widget toggle.

    The set is small on purpose — three buckets is enough to triage a
    weekly batch without forcing the submitter to think hard about
    classification. Adding a value here is a schema-visible change; the
    Postgres CHECK constraint must be expanded in a migration.
    """

    BUG = "bug"
    FEATURE = "feature"
    GENERAL = "general"


class Feedback(Base):
    """A single beta-feedback submission from the in-app widget.

    Attributes:
        id: Unique identifier for the submission.
        user_id: Foreign key to the submitter when signed in. Null for
            anonymous submissions. Set to NULL on user deletion so the
            submission survives the account being wiped.
        email: Reply-to email at submit time. Populated automatically
            from the user's account when ``user_id`` is set; supplied by
            the form for anonymous submitters; nullable so a fully
            anonymous bug report still gets stored.
        message: Freeform feedback body, up to 4000 chars enforced at
            the API layer.
        kind: ``bug`` / ``feature`` / ``general`` toggle the submitter
            picked. Stored as text for readability in the admin table.
        page_url: The URL the user was viewing when they opened the
            widget. Helps reproduce bug reports without a screenshot.
        user_agent: Browser UA string at submit time. Helps debug
            "only happens on Safari" reports.
        is_resolved: Whether ops has triaged this submission. Toggled
            from the admin dashboard.
        created_at: Submit timestamp.
        user: Back-relationship to the :class:`User` when set.
    """

    __tablename__ = "feedback"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('bug', 'feature', 'general')",
            name="ck_feedback_kind",
        ),
        Index("ix_feedback_created_at", "created_at"),
        Index("ix_feedback_kind_created_at", "kind", "created_at"),
        Index("ix_feedback_is_resolved_created_at", "is_resolved", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[FeedbackKind] = mapped_column(String(20), nullable=False)
    page_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    is_resolved: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="false",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    user: Mapped["User | None"] = relationship()

    def __repr__(self) -> str:
        """Return a short debug representation.

        Returns:
            String with id and kind.
        """
        return f"<Feedback {self.id} ({self.kind})>"

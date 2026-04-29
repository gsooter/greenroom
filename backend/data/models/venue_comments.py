"""SQLAlchemy ORM models for venue comments and their votes.

Comments are short, category-tagged notes users leave on a venue's
detail page (e.g. "bar runs out of cash change fast" under TICKETS).
Each comment can be voted up or down. Votes dedupe per (comment, user)
for logged-in users and per (comment, session_id) for guests, so a
signed-out visitor can still thumbs-up a tip without voting again from
the same browser.

The ``ip_hash`` column is written by the API layer (sha256 of IP + a
rotating salt) so the service layer can rate-limit noisy IPs without
storing the raw address. Only the hash is ever persisted.
"""

import enum
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.data.models.users import User
    from backend.data.models.venues import Venue


class VenueCommentCategory(enum.StrEnum):
    """Category a venue comment is filed under.

    Categories back the tab row on the venue detail page. The set is
    intentionally small — too many tabs and nobody reads any of them.
    Adding a value here is a schema-visible change; the Postgres enum
    must be expanded in a migration.
    """

    VIBES = "vibes"
    TICKETS = "tickets"
    SAFETY = "safety"
    ACCESS = "access"
    FOOD_DRINK = "food_drink"
    OTHER = "other"


class VenueComment(TimestampMixin, Base):
    """A user-submitted note attached to a venue under one category.

    Attributes:
        id: Unique identifier for the comment.
        venue_id: Foreign key to the commented-on venue.
        user_id: Foreign key to the commenting user. Nullable so the
            row survives if the user deletes their account; the UI
            falls back to "[deleted]" in that case.
        category: Which tab this comment shows up under.
        body: Plain-text comment body, 2000 chars max.
        ip_hash: sha256(IP + salt) recorded at submit time for rate
            limiting. Never displayed.
        venue: Relationship to the parent venue.
        user: Relationship to the author.
        votes: Relationship to the votes cast on this comment.
    """

    __tablename__ = "venue_comments"
    __table_args__ = (
        Index("ix_venue_comments_venue_id_created_at", "venue_id", "created_at"),
        Index("ix_venue_comments_venue_id_category", "venue_id", "category"),
        Index("ix_venue_comments_ip_hash_created_at", "ip_hash", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    venue_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("venues.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    category: Mapped[VenueCommentCategory] = mapped_column(
        String(20),
        nullable=False,
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    venue: Mapped["Venue"] = relationship()
    user: Mapped["User | None"] = relationship()
    votes: Mapped[list["VenueCommentVote"]] = relationship(
        back_populates="comment",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        """Return a string representation of the VenueComment.

        Returns:
            String representation with id and category.
        """
        return f"<VenueComment {self.id} ({self.category})>"


class VenueCommentVote(TimestampMixin, Base):
    """A +1 / -1 vote cast by a user or guest session on a comment.

    A single voter (logged-in user OR guest session) can only have one
    row per comment, enforced by two partial unique indexes — one keyed
    on user_id, one on session_id. Changing a vote is an update in
    place, not a second row.

    Attributes:
        id: Unique identifier for the vote.
        comment_id: Foreign key to the comment being voted on.
        user_id: Voter's user id, when logged in.
        session_id: Opaque browser session id, when voting as a guest.
            Exactly one of ``user_id`` / ``session_id`` must be set.
        value: +1 for upvote, -1 for downvote. Enforced by CHECK.
        comment: Relationship to the voted-on comment.
    """

    __tablename__ = "venue_comment_votes"
    __table_args__ = (
        CheckConstraint("value IN (-1, 1)", name="ck_venue_comment_votes_value"),
        CheckConstraint(
            "(user_id IS NOT NULL) <> (session_id IS NOT NULL)",
            name="ck_venue_comment_votes_one_voter",
        ),
        UniqueConstraint(
            "comment_id",
            "user_id",
            name="uq_venue_comment_votes_comment_user",
        ),
        UniqueConstraint(
            "comment_id",
            "session_id",
            name="uq_venue_comment_votes_comment_session",
        ),
        Index("ix_venue_comment_votes_comment_id", "comment_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    comment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("venue_comments.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
    )
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    value: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    comment: Mapped["VenueComment"] = relationship(back_populates="votes")

    def __repr__(self) -> str:
        """Return a string representation of the VenueCommentVote.

        Returns:
            String representation with id and value.
        """
        return f"<VenueCommentVote {self.id} value={self.value:+d}>"

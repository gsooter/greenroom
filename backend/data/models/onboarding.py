"""SQLAlchemy ORM models for the four-step ``/welcome`` onboarding flow.

Three relationships live here:

* :class:`UserOnboardingState` — per-user bookkeeping. Every user has
  exactly one row (back-filled on migration for pre-existing users),
  keyed by ``user_id``. The four per-step ``*_completed_at`` columns
  double as "step done" markers whether the user actually did the step
  or skipped it — the spec is explicit that skipping saves no data but
  still marks the step complete.

* :class:`FollowedArtist` — many-to-many edge for user-followed
  artists, populated by the Step 1 artist-search control.

* :class:`FollowedVenue` — many-to-many edge for user-followed venues,
  populated by the Step 2 venue grid.

Users arrive at this schema through Alembic migration
``20260421_add_onboarding_and_follows``.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from backend.core.database import Base

if TYPE_CHECKING:
    from backend.data.models.artists import Artist
    from backend.data.models.users import User
    from backend.data.models.venues import Venue


class UserOnboardingState(Base):
    """Per-user progress tracker for the ``/welcome`` flow.

    One row per user. The four step timestamps are independent — a
    user can complete steps out of order and the row reflects it.
    Onboarding is considered "complete" when all four step timestamps
    are non-null; the ``users.onboarding_completed_at`` mirror column
    is set at that point so other gates can read it cheaply.

    Attributes:
        user_id: Primary key and foreign key to ``users.id``.
        taste_completed_at: Timestamp when Step 1 finished (or was
            skipped). Null means the step still shows.
        venues_completed_at: Timestamp when Step 2 finished or was
            skipped.
        music_services_completed_at: Timestamp when Step 3 finished
            or was skipped.
        passkey_completed_at: Timestamp when Step 4 finished, was
            skipped, or was auto-marked because the user authed via
            passkey.
        skipped_entirely_at: Timestamp when the user bailed on the
            whole flow from the first step (clicked "Skip" on every
            step before hitting the end). Drives the persistent skip
            banner on browse pages.
        banner_dismissed_at: Timestamp when the user dismissed the
            skip banner. Null means it's still eligible to show.
        browse_sessions_since_skipped: Count of browse-page sessions
            observed since the user skipped. The banner vanishes once
            this reaches 7 (or on dismissal, whichever first).
        created_at: Row creation time.
        updated_at: Row last-update time.
        user: Back-relationship to the :class:`User`.
    """

    __tablename__ = "user_onboarding_state"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    taste_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    venues_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    music_services_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    passkey_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    skipped_entirely_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    banner_dismissed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    browse_sessions_since_skipped: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    user: Mapped["User"] = relationship()

    def __repr__(self) -> str:
        """Return a short debug representation.

        Returns:
            String with user id and a compact step-completion bitmask.
        """
        bits = "".join(
            "1" if getattr(self, f"{name}_completed_at") else "0"
            for name in ("taste", "venues", "music_services", "passkey")
        )
        return f"<UserOnboardingState user={self.user_id} steps={bits}>"


class FollowedArtist(Base):
    """Edge row marking a user as following an artist.

    Attributes:
        user_id: Primary key part and foreign key to ``users.id``.
        artist_id: Primary key part and foreign key to ``artists.id``.
        created_at: When the follow was created.
        user: Back-relationship to the :class:`User`.
        artist: Back-relationship to the :class:`Artist`.
    """

    __tablename__ = "followed_artists"
    __table_args__ = (
        Index(
            "ix_followed_artists_user_id_created_at",
            "user_id",
            "created_at",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    artist_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("artists.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    user: Mapped["User"] = relationship()
    artist: Mapped["Artist"] = relationship()

    def __repr__(self) -> str:
        """Return a short debug representation.

        Returns:
            String with user id and artist id.
        """
        return f"<FollowedArtist user={self.user_id} artist={self.artist_id}>"


class FollowedVenue(Base):
    """Edge row marking a user as following a venue.

    Attributes:
        user_id: Primary key part and foreign key to ``users.id``.
        venue_id: Primary key part and foreign key to ``venues.id``.
        created_at: When the follow was created.
        user: Back-relationship to the :class:`User`.
        venue: Back-relationship to the :class:`Venue`.
    """

    __tablename__ = "followed_venues"
    __table_args__ = (
        Index(
            "ix_followed_venues_user_id_created_at",
            "user_id",
            "created_at",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    venue_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("venues.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    user: Mapped["User"] = relationship()
    venue: Mapped["Venue"] = relationship()

    def __repr__(self) -> str:
        """Return a short debug representation.

        Returns:
            String with user id and venue id.
        """
        return f"<FollowedVenue user={self.user_id} venue={self.venue_id}>"

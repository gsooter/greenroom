"""SQLAlchemy ORM models for community map recommendations and votes.

A :class:`MapRecommendation` is a short, category-tagged note a user
leaves about a real-world place — a taco shop to hit before a 9:30
Club show, a divey bar near The Anthem — anchored to an Apple-verified
lat/lng. The verification step lives in
:mod:`backend.services.apple_maps`; by the time a row lands here the
``latitude`` / ``longitude`` / ``place_address`` columns reflect Apple's
canonical answer, and ``similarity_score`` records how confident the
verifier was (always >= 0.80 by policy).

Every recommendation can be voted +1 / -1. Votes dedupe per (rec, user)
for logged-in users and per (rec, session_id) for guests so a signed-out
visitor can upvote a tip once without piling duplicates.

The ``ip_hash`` column is written by the API layer (sha256 of IP + a
rotating salt) so the service layer can rate-limit noisy submitters
without storing the raw address. The ``suppressed_at`` column is the
tombstone used by auto-suppression (net votes drop below a floor) and
by admin moderation — a non-null value hides the row from map feeds
without losing the data.
"""

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
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


class MapRecommendationCategory(enum.StrEnum):
    """Category a map recommendation is filed under.

    Categories back the filter chips on Tonight's DC Map and Shows Near
    Me. The set is intentionally small — too many chips and nobody
    picks any of them. Adding a value here is a schema-visible change;
    the Postgres column is a plain string, but the filter UI and
    validation tables must both be updated.
    """

    FOOD = "food"
    DRINKS = "drinks"
    COFFEE = "coffee"
    LATE_NIGHT = "late_night"
    OTHER = "other"


class MapRecommendation(TimestampMixin, Base):
    """A user-submitted note attached to an Apple-verified place.

    Attributes:
        id: Unique identifier for the recommendation.
        submitter_user_id: Foreign key to the submitting user. Nullable
            so the row survives account deletion via SET NULL; guest
            submissions set this to None and identify by ``session_id``.
        session_id: Opaque browser session id for guest submissions.
            Exactly one of ``submitter_user_id`` / ``session_id`` must
            be set, enforced by CHECK.
        place_name: The verified place's canonical name, as returned
            by Apple Maps (e.g. "Black Cat" — not what the user typed).
        place_address: Apple's formatted address for the place, when
            available. Nullable because Apple can return a coordinate
            match without a street address.
        latitude: WGS-84 latitude of the verified place.
        longitude: WGS-84 longitude of the verified place.
        similarity_score: Verifier confidence in ``[0.80, 1.0]``. The
            service layer refuses to persist rows below the floor.
        category: Which filter chip this recommendation shows up under.
        body: Plain-text recommendation body, 2000 chars max.
        ip_hash: sha256(IP + salt) recorded at submit time for rate
            limiting. Never displayed.
        suppressed_at: Tombstone timestamp; non-null means the row is
            hidden from public map feeds. Set by auto-suppression
            (net votes < threshold) or by admin action.
        submitter: Relationship to the author, when logged in.
        votes: Relationship to the votes cast on this recommendation.
    """

    __tablename__ = "map_recommendations"
    __table_args__ = (
        CheckConstraint(
            "(submitter_user_id IS NOT NULL) OR (session_id IS NOT NULL)",
            name="ck_map_recommendations_has_submitter",
        ),
        Index("ix_map_recommendations_lat_lng", "latitude", "longitude"),
        Index("ix_map_recommendations_created_at", "created_at"),
        Index("ix_map_recommendations_ip_hash_created_at", "ip_hash", "created_at"),
        Index("ix_map_recommendations_suppressed_at", "suppressed_at"),
        Index("ix_map_recommendations_category", "category"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    submitter_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    place_name: Mapped[str] = mapped_column(String(200), nullable=False)
    place_address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    similarity_score: Mapped[float] = mapped_column(Float, nullable=False)
    category: Mapped[MapRecommendationCategory] = mapped_column(
        String(20),
        nullable=False,
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    suppressed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    submitter: Mapped["User | None"] = relationship()
    votes: Mapped[list["MapRecommendationVote"]] = relationship(
        back_populates="recommendation",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        """Return a string representation of the MapRecommendation.

        Returns:
            String representation with id and category.
        """
        return f"<MapRecommendation {self.id} ({self.category})>"


class MapRecommendationVote(TimestampMixin, Base):
    """A +1 / -1 vote cast by a user or guest session on a recommendation.

    A single voter (logged-in user OR guest session) can only have one
    row per recommendation, enforced by two partial unique indexes — one
    keyed on user_id, one on session_id. Changing a vote is an update
    in place, not a second row.

    Attributes:
        id: Unique identifier for the vote.
        recommendation_id: Foreign key to the recommendation being voted on.
        user_id: Voter's user id, when logged in.
        session_id: Opaque browser session id, when voting as a guest.
            Exactly one of ``user_id`` / ``session_id`` must be set.
        value: +1 for upvote, -1 for downvote. Enforced by CHECK.
        recommendation: Relationship to the voted-on recommendation.
    """

    __tablename__ = "map_recommendation_votes"
    __table_args__ = (
        CheckConstraint("value IN (-1, 1)", name="ck_map_recommendation_votes_value"),
        CheckConstraint(
            "(user_id IS NOT NULL) <> (session_id IS NOT NULL)",
            name="ck_map_recommendation_votes_one_voter",
        ),
        UniqueConstraint(
            "recommendation_id",
            "user_id",
            name="uq_map_recommendation_votes_rec_user",
        ),
        UniqueConstraint(
            "recommendation_id",
            "session_id",
            name="uq_map_recommendation_votes_rec_session",
        ),
        Index(
            "ix_map_recommendation_votes_recommendation_id",
            "recommendation_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    recommendation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("map_recommendations.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
    )
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    value: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    recommendation: Mapped["MapRecommendation"] = relationship(back_populates="votes")

    def __repr__(self) -> str:
        """Return a string representation of the MapRecommendationVote.

        Returns:
            String representation with id and value.
        """
        return f"<MapRecommendationVote {self.id} value={self.value:+d}>"

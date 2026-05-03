"""SQLAlchemy ORM model for the artist-hydration audit log.

The ``hydration_log`` table records every hydration attempt run via the
admin tool or CLI (Decision 067). One row per hydration call — even
when the daily cap blocked the additions or every candidate was already
present in the database. The audit trail answers two operational
questions cheaply: "who added this artist (and from which parent)?"
and "did we hit the daily cap recently?"

The table is intentionally append-only. Counts on the dashboard's
hydration leaderboard come from this table, not from
``artists.hydration_source``, so background hydrations from the
recommendation engine (if those ever exist) do not skew the cap.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.database import Base

if TYPE_CHECKING:
    from datetime import datetime


class HydrationLog(Base):
    """One audit-log row per artist hydration attempt.

    Attributes:
        id: Unique identifier for the log entry.
        source_artist_id: UUID of the artist whose similar-artists list
            seeded this hydration. Cascades on delete since the log is
            only meaningful while the parent exists.
        admin_email: Email address of the admin (or CLI operator) who
            triggered the hydration. Stored verbatim — the admin tool
            requires the operator to type their email when invoking.
        candidate_artists: JSONB snapshot of the candidate list shown
            to the operator at confirmation time. Each entry carries
            ``name``, ``similar_artist_mbid``, ``similarity_score``,
            and ``status`` (``eligible`` / ``already_exists`` /
            ``below_threshold`` / ``depth_exceeded``). Captures what
            the operator saw, even if database state has since shifted.
        added_artist_ids: UUIDs of the artist rows actually created
            during the hydration. Empty array when the daily cap was
            hit or every candidate was filtered out.
        skipped_count: Number of candidates skipped because they
            already existed in the database.
        filtered_count: Number of candidates filtered because their
            similarity score was below
            :data:`backend.services.artist_hydration.MIN_SIMILARITY_SCORE`.
        daily_cap_hit: True when the hydration was clipped because the
            daily cap (:data:`backend.services.artist_hydration.DAILY_HYDRATION_CAP`)
            was already reached or exceeded by this call.
        created_at: When the hydration ran. Indexed descending — the
            dashboard pulls "recent activity" from this column.
    """

    __tablename__ = "hydration_log"
    __table_args__ = (
        Index("idx_hydration_log_source", "source_artist_id"),
        Index(
            "idx_hydration_log_created_at",
            text("created_at DESC"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    source_artist_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("artists.id", ondelete="CASCADE"),
        nullable=False,
    )
    admin_email: Mapped[str] = mapped_column(String(320), nullable=False)
    candidate_artists: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False
    )
    added_artist_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)),
        nullable=False,
        default=list,
        server_default=text("ARRAY[]::uuid[]"),
    )
    skipped_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    filtered_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    daily_cap_hit: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    def __repr__(self) -> str:
        """Return a string representation of the HydrationLog row.

        Returns:
            String representation showing source artist id, admin email,
            and the number of artists actually added.
        """
        added = len(self.added_artist_ids or [])
        return (
            f"<HydrationLog source={self.source_artist_id} "
            f"admin={self.admin_email} added={added}>"
        )

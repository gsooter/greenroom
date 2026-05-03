"""SQLAlchemy ORM model for regions.

A region groups cities that users typically travel between for
shows. The DMV-aware ranking sprint introduces this table so the
actionability overlay can ask "is this event in the user's region"
without hardcoding a city slug list. When the app expands beyond
the DMV, adding a new market is a row-level INSERT plus a
``cities.region_id`` UPDATE — the recommendation engine picks up
the new region without any code change.

Today there is exactly one row, ``dmv``, which covers DC,
Baltimore, Richmond, and the surrounding NOVA cities.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.data.models.cities import City


class Region(TimestampMixin, Base):
    """A geographic grouping of cities for the actionability overlay.

    Used by the actionability overlay (see
    :mod:`backend.recommendations.overlays.actionability`) to decide
    whether a candidate event is in the user's preferred city, in a
    different city of the same region, or far enough away to warrant
    the strong "different region" downweight.

    Initial seed is the DMV region containing DC, Baltimore,
    Richmond, and the NOVA cities. Future markets ship as additional
    rows; no schema change is required.

    Attributes:
        id: Unique identifier for the region.
        slug: URL-safe identifier (e.g. ``"dmv"``). Unique across
            all regions; used by repository lookups and any future
            UI region picker.
        name: Long display name, e.g. ``"DC, Maryland & Virginia"``.
        display_name: Short label used in compact UI surfaces, e.g.
            ``"DMV"``.
        description: Optional human-readable description for SEO and
            future region landing pages.
        created_at: Timestamp the row was inserted.
        updated_at: Timestamp the row was last updated.
        cities: Cities that belong to this region. Populated lazily
            so callers don't pay the join cost when only the region
            metadata is needed.
    """

    __tablename__ = "regions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    cities: Mapped[list[City]] = relationship(
        back_populates="region_obj",
        lazy="select",
    )

    def __repr__(self) -> str:
        """Return a string representation of the Region.

        Returns:
            String including the region slug and display name.
        """
        return f"<Region {self.slug} ({self.display_name})>"

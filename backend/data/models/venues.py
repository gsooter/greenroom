"""SQLAlchemy ORM models for venues.

Venues are scoped to a city and have an external ID for scraper mapping.
Each venue maps to one or more scrapers via scraper/config/venues.py.
"""

import uuid

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base, TimestampMixin


class Venue(TimestampMixin, Base):
    """A music venue where events take place.

    Attributes:
        id: Unique identifier for the venue.
        city_id: Foreign key to the city this venue belongs to.
        name: Display name of the venue.
        slug: URL-safe identifier used in venue page URLs.
        address: Street address of the venue.
        latitude: GPS latitude coordinate.
        longitude: GPS longitude coordinate.
        capacity: Maximum capacity of the venue, if known.
        website_url: Official website URL.
        description: Venue description for SEO and display.
        image_url: Primary image URL for the venue.
        external_ids: JSONB mapping of platform name to external ID
            (e.g., {"ticketmaster": "KovZpa2ywe", "seatgeek": "123"}).
        tags: Array of descriptive tags (e.g., ["intimate", "standing-room"]).
        is_active: Whether this venue is currently active for scraping.
        city: Relationship to the parent city.
        events: Relationship to events at this venue.
    """

    __tablename__ = "venues"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    city_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cities.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(
        String(200), unique=True, nullable=False, index=True
    )
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    latitude: Mapped[float | None] = mapped_column(nullable=True)
    longitude: Mapped[float | None] = mapped_column(nullable=True)
    capacity: Mapped[int | None] = mapped_column(nullable=True)
    website_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    external_ids: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True, default=dict
    )
    tags: Mapped[list[str] | None] = mapped_column(
        ARRAY(String(50)), nullable=True, default=list
    )
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    # Relationships
    city: Mapped["City"] = relationship(  # noqa: F821
        back_populates="venues",
    )
    events: Mapped[list["Event"]] = relationship(  # noqa: F821
        back_populates="venue",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        """Return a string representation of the Venue.

        Returns:
            String representation with venue name and slug.
        """
        return f"<Venue {self.name} ({self.slug})>"

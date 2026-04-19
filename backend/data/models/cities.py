"""SQLAlchemy ORM model for cities.

All venues and events are scoped to a city from day one (Decision 014).
Adding a new city is a data operation, not a code change.
"""

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.data.models.venues import Venue


class City(TimestampMixin, Base):
    """A city in which venues and events are aggregated.

    Attributes:
        id: Unique identifier for the city.
        name: Display name of the city (e.g., "Washington").
        slug: URL-safe identifier (e.g., "washington-dc").
        state: US state abbreviation (e.g., "DC").
        region: Marketing region grouping (e.g., "DMV" for DC/MD/VA).
            Multiple cities can share a region. Used to organize
            scraper configs and group cities in the UI.
        timezone: IANA timezone string (e.g., "America/New_York").
        description: Optional description for SEO and display.
        is_active: Whether this city is live on the platform.
        venues: Relationship to venues in this city.
    """

    __tablename__ = "cities"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    slug: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False, index=True
    )
    state: Mapped[str] = mapped_column(String(2), nullable=False)
    region: Mapped[str] = mapped_column(
        String(20), nullable=False, default="DMV", index=True
    )
    timezone: Mapped[str] = mapped_column(
        String(50), nullable=False, default="America/New_York"
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    # Relationships
    venues: Mapped[list["Venue"]] = relationship(
        back_populates="city",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        """Return a string representation of the City.

        Returns:
            String representation with city name and slug.
        """
        return f"<City {self.name} ({self.slug})>"

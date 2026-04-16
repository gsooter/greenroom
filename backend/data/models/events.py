"""SQLAlchemy ORM models for events and ticket pricing.

Events are scoped to a venue (and transitively to a city). The event_type
enum is defined with future categories but only 'concert' is active at
launch (Decision 015). Ticket pricing snapshots are stored for price
history and trend analysis (Decision 010).
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base, TimestampMixin


class EventType(str, enum.Enum):
    """Enum of supported event categories.

    Only 'concert' is active at launch. Others are defined so expanding
    to new categories requires no schema migration (Decision 015).
    """

    CONCERT = "concert"
    COMEDY = "comedy"
    THEATER = "theater"
    SPORTS = "sports"
    OTHER = "other"


class EventStatus(str, enum.Enum):
    """Lifecycle status of an event.

    Tracks whether an event is confirmed, cancelled, postponed, or
    has already occurred.
    """

    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    POSTPONED = "postponed"
    SOLD_OUT = "sold_out"
    PAST = "past"


class Event(TimestampMixin, Base):
    """A live music event at a venue.

    Attributes:
        id: Unique identifier for the event.
        venue_id: Foreign key to the venue hosting this event.
        title: Event title or headline act name.
        slug: URL-safe identifier for event page URLs.
        description: Event description text.
        event_type: Category of event (concert, comedy, etc.).
        status: Lifecycle status (confirmed, cancelled, etc.).
        starts_at: Event start datetime in UTC.
        ends_at: Event end datetime in UTC, if known.
        doors_at: Doors open datetime in UTC, if known.
        on_sale_at: Ticket on-sale datetime in UTC, if known.
        artists: Array of artist/performer names.
        spotify_artist_ids: Array of Spotify artist IDs for recommendation matching.
        genres: Array of genre tags for filtering.
        image_url: Primary event or artist image URL.
        ticket_url: Direct ticket purchase URL.
        min_price: Minimum known ticket price in USD.
        max_price: Maximum known ticket price in USD.
        source_url: URL of the original event listing.
        raw_data: Full original payload from the scraper source.
        external_id: External identifier from the source platform.
        source_platform: Name of the platform this was scraped from.
        venue: Relationship to the parent venue.
        ticket_snapshots: Relationship to ticket pricing snapshots.
    """

    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_starts_at", "starts_at"),
        Index("ix_events_venue_id_starts_at", "venue_id", "starts_at"),
        Index(
            "ix_events_spotify_artist_ids_gin",
            "spotify_artist_ids",
            postgresql_using="gin",
        ),
        Index(
            "ix_events_genres_gin",
            "genres",
            postgresql_using="gin",
        ),
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
        index=True,
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    slug: Mapped[str] = mapped_column(
        String(500), unique=True, nullable=False, index=True
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_type: Mapped[EventType] = mapped_column(
        Enum(EventType, name="event_type", native_enum=True),
        nullable=False,
        default=EventType.CONCERT,
        index=True,
    )
    status: Mapped[EventStatus] = mapped_column(
        Enum(EventStatus, name="event_status", native_enum=True),
        nullable=False,
        default=EventStatus.CONFIRMED,
        index=True,
    )
    starts_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    doors_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    on_sale_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    artists: Mapped[list[str]] = mapped_column(
        ARRAY(String(200)), nullable=False, default=list
    )
    spotify_artist_ids: Mapped[list[str] | None] = mapped_column(
        ARRAY(String(50)), nullable=True
    )
    genres: Mapped[list[str] | None] = mapped_column(
        ARRAY(String(50)), nullable=True
    )
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    ticket_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    min_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    raw_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    external_id: Mapped[str | None] = mapped_column(
        String(200), nullable=True, index=True
    )
    source_platform: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )

    # Relationships
    venue: Mapped["Venue"] = relationship(  # noqa: F821
        back_populates="events",
    )
    ticket_snapshots: Mapped[list["TicketPricingSnapshot"]] = relationship(
        back_populates="event",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        """Return a string representation of the Event.

        Returns:
            String representation with event title and date.
        """
        return f"<Event {self.title} @ {self.starts_at}>"


class TicketPricingSnapshot(TimestampMixin, Base):
    """A point-in-time snapshot of ticket pricing for an event.

    Stored for price history and trend analysis (Decision 010).
    SeatGeek is the primary source, StubHub is secondary.

    Attributes:
        id: Unique identifier for the snapshot.
        event_id: Foreign key to the event this pricing is for.
        source: Platform the pricing came from (e.g., "seatgeek").
        min_price: Minimum ticket price at snapshot time.
        max_price: Maximum ticket price at snapshot time.
        average_price: Average ticket price at snapshot time.
        listing_count: Number of active listings at snapshot time.
        currency: Currency code (default USD).
        raw_data: Full pricing payload from the source.
        event: Relationship to the parent event.
    """

    __tablename__ = "ticket_pricing_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    min_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    average_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    listing_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="USD"
    )
    raw_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Relationships
    event: Mapped["Event"] = relationship(
        back_populates="ticket_snapshots",
    )

    def __repr__(self) -> str:
        """Return a string representation of the TicketPricingSnapshot.

        Returns:
            String representation with source and price range.
        """
        return (
            f"<TicketPricingSnapshot {self.source} "
            f"${self.min_price}-${self.max_price}>"
        )

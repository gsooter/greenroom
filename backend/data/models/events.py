"""SQLAlchemy ORM models for events and ticket pricing.

Events are scoped to a venue (and transitively to a city). The event_type
enum is defined with future categories but only 'concert' is active at
launch (Decision 015). Ticket pricing snapshots are stored for price
history and trend analysis (Decision 010).
"""

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.data.models.venues import Venue


class EventType(enum.StrEnum):
    """Enum of supported event categories.

    Only 'concert' is active at launch. Others are defined so expanding
    to new categories requires no schema migration (Decision 015).
    """

    CONCERT = "concert"
    COMEDY = "comedy"
    THEATER = "theater"
    SPORTS = "sports"
    OTHER = "other"


class EventStatus(enum.StrEnum):
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
        prices_refreshed_at: Last successful pricing-sweep timestamp;
            powers the manual-refresh cooldown gate and the "Updated X
            ago" UI label.
        ticket_snapshots: Relationship to ticket pricing snapshots.
        pricing_links: Relationship to per-source buy-URL records.
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
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
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
    genres: Mapped[list[str] | None] = mapped_column(ARRAY(String(50)), nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    ticket_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    min_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    raw_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    external_id: Mapped[str | None] = mapped_column(
        String(200), nullable=True, index=True
    )
    source_platform: Mapped[str | None] = mapped_column(String(50), nullable=True)
    prices_refreshed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    venue: Mapped["Venue"] = relationship(
        back_populates="events",
    )
    ticket_snapshots: Mapped[list["TicketPricingSnapshot"]] = relationship(
        back_populates="event",
        lazy="selectin",
    )
    pricing_links: Mapped[list["EventPricingLink"]] = relationship(
        back_populates="event",
        lazy="selectin",
        cascade="all, delete-orphan",
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
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    raw_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

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
            f"<TicketPricingSnapshot {self.source} ${self.min_price}-${self.max_price}>"
        )


class EventPricingLink(TimestampMixin, Base):
    """A per-source buy URL for an event.

    Decoupled from :class:`TicketPricingSnapshot` so a "no live listings
    right now" state preserves the buy URL — the next refresh that finds
    inventory just bumps ``last_active_at`` and flips ``is_active`` back
    on, instead of having to re-derive the URL from scratch. Pricing
    snapshots are append-only history; pricing links are the latest
    known buy surface.

    Attributes:
        id: Unique identifier for the link.
        event_id: Foreign key to the event this link points at.
        source: Provider identifier (e.g., ``"seatgeek"``,
            ``"ticketmaster"``, ``"tickpick"``). Matches the ``source``
            on the corresponding pricing snapshot.
        url: Canonical buy URL.
        affiliate_url: Affiliate-tagged buy URL when the provider has an
            affiliate program; rendered preferentially in the UI when
            present.
        last_active_at: Most recent refresh that found live listings at
            this URL. ``None`` if no refresh has ever found listings —
            the URL came from the scraper, not a live pricing pass.
        last_seen_at: Most recent refresh that confirmed the URL still
            resolves at all (even if zero listings). Used to retire
            broken links after a long absence.
        is_active: Convenience flag mirroring ``last_active_at`` — set
            ``True`` when the most recent refresh found listings, set
            ``False`` otherwise.
        currency: Currency code the source quotes prices in.
        event: Relationship to the parent event.
    """

    __tablename__ = "event_pricing_links"
    __table_args__ = (
        UniqueConstraint(
            "event_id", "source", name="uq_event_pricing_links_event_id_source"
        ),
        Index("ix_event_pricing_links_event_id", "event_id"),
        Index("ix_event_pricing_links_source", "source"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("events.id", ondelete="CASCADE"),
        nullable=False,
    )
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    affiliate_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    last_active_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now()
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")

    # Relationships
    event: Mapped["Event"] = relationship(
        back_populates="pricing_links",
    )

    def __repr__(self) -> str:
        """Return a string representation of the EventPricingLink.

        Returns:
            String including the source and the (possibly affiliate)
            URL it points at.
        """
        return f"<EventPricingLink {self.source} -> {self.affiliate_url or self.url}>"

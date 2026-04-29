"""SQLAlchemy ORM model for artists.

Artists are a normalized projection of the names scraped onto
:class:`backend.data.models.events.Event` rows. Each row carries a
normalized lookup key so duplicate spellings collapse, and a set of
genre tags pulled from Spotify during nightly enrichment
(:mod:`backend.services.artist_enrichment`). The genres feed the
genre-overlap branch of the artist-match recommendation scorer when no
direct artist match exists.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, String
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.database import Base, TimestampMixin


class Artist(TimestampMixin, Base):
    """A music artist known to the ingestion + recommendation pipeline.

    Upserted by the scraper runner keyed on ``normalized_name`` so that
    "Beyoncé" and "BEYONCE" collapse to the same row. ``spotify_id`` and
    ``genres`` are populated lazily by the nightly enrichment task —
    ``spotify_enriched_at`` gates whether that task re-checks this row.

    Attributes:
        id: Unique identifier for the artist.
        name: Canonical display-cased name as first seen by the scraper.
        normalized_name: Lowercase, diacritic-stripped, whitespace-
            collapsed lookup key. Unique — the dedup primitive.
        spotify_id: Spotify artist ID when enrichment found a
            high-confidence match, else None.
        genres: Canonical genre tags from Spotify, defaulting to an
            empty array so scoring code never needs a None check.
        spotify_enriched_at: UTC timestamp of the most recent enrichment
            attempt. None means the row has never been considered.
    """

    __tablename__ = "artists"
    __table_args__ = (
        Index("ix_artists_genres_gin", "genres", postgresql_using="gin"),
        Index("ix_artists_spotify_enriched_at", "spotify_enriched_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    normalized_name: Mapped[str] = mapped_column(
        String(300), unique=True, nullable=False, index=True
    )
    spotify_id: Mapped[str | None] = mapped_column(
        String(50), nullable=True, index=True
    )
    genres: Mapped[list[str]] = mapped_column(
        ARRAY(String(50)), nullable=False, default=list
    )
    spotify_enriched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        """Return a string representation of the Artist.

        Returns:
            String representation with artist name and normalized key.
        """
        return f"<Artist {self.name} ({self.normalized_name})>"

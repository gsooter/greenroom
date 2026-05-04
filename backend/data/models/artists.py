"""SQLAlchemy ORM model for artists.

Artists are a normalized projection of the names scraped onto
:class:`backend.data.models.events.Event` rows. Each row carries a
normalized lookup key so duplicate spellings collapse, a set of
genre tags pulled from Spotify during nightly enrichment
(:mod:`backend.services.artist_enrichment`), the raw MusicBrainz
genre and tag payloads pulled by
:mod:`backend.services.musicbrainz_tasks`, and the raw Last.fm tags
pulled by :mod:`backend.services.lastfm_tasks`. Together they feed
the genre-overlap branch of the artist-match recommendation scorer
when no direct artist match exists.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.database import Base, TimestampMixin


class Artist(TimestampMixin, Base):
    """A music artist known to the ingestion + recommendation pipeline.

    Upserted by the scraper runner keyed on ``normalized_name`` so that
    "Beyoncé" and "BEYONCE" collapse to the same row. ``spotify_id`` and
    ``genres`` are populated lazily by the nightly Spotify enrichment
    task — ``spotify_enriched_at`` gates whether that task re-checks
    this row. The ``musicbrainz_*`` columns are populated by an
    independent MusicBrainz enrichment task; ``musicbrainz_enriched_at``
    gates that task in the same way.

    Attributes:
        id: Unique identifier for the artist.
        name: Canonical display-cased name as first seen by the scraper.
        normalized_name: Lowercase, diacritic-stripped, whitespace-
            collapsed lookup key. Unique — the dedup primitive.
        spotify_id: Spotify artist ID when enrichment found a
            high-confidence match, else None.
        genres: Canonical genre tags from Spotify, defaulting to an
            empty array so scoring code never needs a None check.
        spotify_enriched_at: UTC timestamp of the most recent Spotify
            enrichment attempt. None means the row has never been
            considered.
        musicbrainz_id: MusicBrainz artist MBID when enrichment found a
            high-confidence match, else None.
        musicbrainz_genres: Raw MusicBrainz ``genres`` array preserving
            ``name`` and ``count`` for each entry. None when the row
            has never been enriched, ``[]`` when enriched but no match.
        musicbrainz_tags: Raw MusicBrainz ``tags`` array (free-form
            user tags with vote counts). None when never enriched,
            ``[]`` when enriched but no match.
        musicbrainz_enriched_at: UTC timestamp of the most recent
            MusicBrainz enrichment attempt. None means the row has
            never been considered. Set on every attempt — including
            no-match outcomes — so we don't repeatedly re-search.
        musicbrainz_match_confidence: Confidence score 0.00-1.00 of the
            chosen MusicBrainz candidate. None when no candidate cleared
            the threshold.
        lastfm_tags: Raw Last.fm ``tag`` array (user-applied tags ordered
            by popularity). Each entry preserves ``name`` and ``url``.
            None when never enriched, ``[]`` when enriched but no match.
        lastfm_listener_count: Last.fm listener count, captured as a
            popularity signal for future scoring. None when no match.
        lastfm_url: Canonical Last.fm artist page URL. Useful for
            debugging matches and powering "more on Last.fm" links.
        lastfm_bio_summary: Short biography blurb returned by Last.fm's
            ``artist.getInfo``. Stored eagerly because the call returns
            it for free; lets us surface bios later without re-enriching.
        lastfm_enriched_at: UTC timestamp of the most recent Last.fm
            enrichment attempt. None means the row has never been
            considered. Set on every attempt — including no-match
            outcomes — so we don't repeatedly re-search.
        lastfm_match_confidence: Confidence score 0.00-1.00 of the
            chosen Last.fm candidate. ``1.00`` for MBID-based lookups
            (exact match), blended name/listener score otherwise.
        canonical_genres: Ordered list of GREENROOM canonical genre
            labels (e.g. ``["Indie Rock", "Folk"]``) produced by the
            nightly normalization pass. None when the artist has never
            been normalized; an empty list when no canonical mapping
            could be derived from the available MusicBrainz/Last.fm
            data.
        genre_confidence: Per-genre confidence score in 0.0-1.0 relative
            to the strongest genre signal for this artist. Keys mirror
            :attr:`canonical_genres`.
        genres_normalized_at: UTC timestamp of the most recent
            normalization attempt. None means the row has never been
            considered. Set on every attempt — including no-match
            outcomes — so the nightly task can skip already-normalized
            rows whose source enrichment hasn't changed.
        lastfm_similar_enriched_at: UTC timestamp of the most recent
            Last.fm similar-artists enrichment attempt. None means the
            row has never been considered. Set on every attempt —
            including no-match outcomes — so the nightly task can skip
            already-enriched rows.
        granular_tags: Deduplicated, filtered, frequency-trimmed list of
            discriminative tags consolidated from MusicBrainz and
            Last.fm sources (Decision 060). Drives the tag-overlap
            similarity query that complements the Last.fm collaborative
            similarity signal for artists with thin Last.fm coverage.
            Defaults to an empty array so similarity queries never need
            a None check.
        granular_tags_consolidated_at: UTC timestamp of the most recent
            tag consolidation pass. None means the row has never been
            consolidated. Set on every attempt so the nightly task can
            skip already-consolidated rows whose source data has not
            changed.
        hydration_source: Lineage tag (Decision 067) — ``None`` for
            original rows seeded by the scraper, ``"similar_artist"``
            for rows added via the admin hydration tool. Other values
            are reserved for future hydration sources.
        hydrated_from_artist_id: When this row was created via
            hydration, the parent artist whose similar-artists list
            contributed it. Nullable for original rows. ``ON DELETE
            SET NULL`` so deleting a parent does not cascade away
            hydrated descendants.
        hydration_depth: 0 for originals, 1 for first-generation
            hydrations, 2 for second-generation, etc. Hard-capped at
            :data:`backend.services.artist_hydration.MAX_HYDRATION_DEPTH`
            to keep the catalog within a couple of hops of a real
            DMV-scraped seed artist.
        hydrated_at: When this row was created via hydration. ``None``
            on original rows.
    """

    __tablename__ = "artists"
    __table_args__ = (
        Index("ix_artists_genres_gin", "genres", postgresql_using="gin"),
        Index("ix_artists_spotify_enriched_at", "spotify_enriched_at"),
        Index("ix_artists_musicbrainz_enriched_at", "musicbrainz_enriched_at"),
        Index("ix_artists_lastfm_enriched_at", "lastfm_enriched_at"),
        Index(
            "idx_artists_lastfm_similar_enriched_at",
            "lastfm_similar_enriched_at",
        ),
        Index(
            "ix_artists_canonical_genres_gin",
            "canonical_genres",
            postgresql_using="gin",
        ),
        Index("ix_artists_genres_normalized_at", "genres_normalized_at"),
        Index(
            "idx_artists_granular_tags",
            "granular_tags",
            postgresql_using="gin",
        ),
        Index(
            "idx_artists_granular_tags_consolidated_at",
            "granular_tags_consolidated_at",
        ),
        Index("idx_artists_hydration_depth", "hydration_depth"),
        Index(
            "idx_artists_hydrated_from",
            "hydrated_from_artist_id",
            postgresql_where=text("hydrated_from_artist_id IS NOT NULL"),
        ),
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
    musicbrainz_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    musicbrainz_genres: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSONB, nullable=True
    )
    musicbrainz_tags: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSONB, nullable=True
    )
    musicbrainz_enriched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    musicbrainz_match_confidence: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=3, scale=2), nullable=True
    )
    lastfm_tags: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSONB, nullable=True
    )
    lastfm_listener_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lastfm_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    lastfm_bio_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    lastfm_enriched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lastfm_match_confidence: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=3, scale=2), nullable=True
    )
    canonical_genres: Mapped[list[str] | None] = mapped_column(
        ARRAY(Text), nullable=True
    )
    genre_confidence: Mapped[dict[str, float] | None] = mapped_column(
        JSONB, nullable=True
    )
    genres_normalized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lastfm_similar_enriched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    granular_tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        nullable=False,
        default=list,
        server_default="{}",
    )
    granular_tags_consolidated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    hydration_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    hydrated_from_artist_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("artists.id", ondelete="SET NULL"),
        nullable=True,
    )
    hydration_depth: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    hydrated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        """Return a string representation of the Artist.

        Returns:
            String representation with artist name and normalized key.
        """
        return f"<Artist {self.name} ({self.normalized_name})>"

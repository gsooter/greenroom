"""SQLAlchemy ORM model for artist similarity edges.

The ``artist_similarity`` table is a join from a "source" artist to a
"similar" artist, populated by the Last.fm enrichment task and
consumed by the recommendation engine's similar-artist scorer
(Decision 059). Each row represents one similarity edge produced by a
single source (today: Last.fm). The schema supports additional sources
(Spotify Related Artists, MusicBrainz relationships) without a
migration — the ``source`` column tags every row with its origin so
the scorer can blend or filter by provider.

Two pointers per row:

* ``source_artist_id`` is always a real :class:`Artist` row (the
  artist we asked Last.fm about).
* ``similar_artist_id`` is nullable. Last.fm returns similar artists
  by name; many of them won't have a row in ``artists`` because they
  aren't performing in the DMV. The resolution task fills this column
  in when a name (or MBID) match exists, which is the cheap join key
  the recommendation engine uses to find "similar artists with
  upcoming DMV shows."
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.database import Base


class ArtistSimilarity(Base):
    """One similarity edge between a source artist and a similar artist.

    Idempotent on ``(source_artist_id, similar_artist_name, source)`` —
    re-running enrichment for the same source artist updates rows in
    place rather than duplicating.

    Attributes:
        id: Unique identifier for the edge.
        source_artist_id: UUID of the artist we requested similarity
            for. Always a real :class:`Artist` row.
        similar_artist_name: The similar artist's display name as
            returned by the upstream source. Used as the lookup key
            when resolution can't find a matching :class:`Artist` row.
        similar_artist_mbid: The similar artist's MusicBrainz ID when
            the upstream source carried one, else None. Preferred over
            name for resolution.
        similar_artist_id: UUID of the matching :class:`Artist` row in
            our database, when one exists. Nullable — most similar
            artists won't have rows because they aren't playing the
            DMV. Populated by :func:`resolve_similarity_links`.
        similarity_score: 0.000-1.000 confidence reported by the source.
            Higher is more similar; the recommendation scorer applies a
            minimum threshold before considering a match.
        source: Provider that produced this edge, e.g. ``"lastfm"``.
            Future sources (``"spotify"``, ``"musicbrainz"``) coexist
            in the same table.
        created_at: When the row was first inserted.
        updated_at: When the row was last upserted by the enrichment
            task.
    """

    __tablename__ = "artist_similarity"
    __table_args__ = (
        Index(
            "idx_artist_similarity_unique",
            "source_artist_id",
            "similar_artist_name",
            "source",
            unique=True,
        ),
        Index(
            "idx_artist_similarity_source_score",
            "source_artist_id",
            text("similarity_score DESC"),
        ),
        Index(
            "idx_artist_similarity_similar_id",
            "similar_artist_id",
            postgresql_where=text("similar_artist_id IS NOT NULL"),
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
    similar_artist_name: Mapped[str] = mapped_column(String(300), nullable=False)
    similar_artist_mbid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    similar_artist_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("artists.id", ondelete="SET NULL"),
        nullable=True,
    )
    similarity_score: Mapped[Decimal] = mapped_column(
        Numeric(precision=4, scale=3), nullable=False
    )
    source: Mapped[str] = mapped_column(
        String(32), nullable=False, default="lastfm", server_default="lastfm"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    def __repr__(self) -> str:
        """Return a string representation of the ArtistSimilarity row.

        Returns:
            String representation showing the source artist id, the
            similar artist's name, and the similarity score.
        """
        return (
            f"<ArtistSimilarity {self.source_artist_id} -> "
            f"{self.similar_artist_name} ({self.similarity_score})>"
        )

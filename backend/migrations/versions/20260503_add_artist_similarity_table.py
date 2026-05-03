"""add_artist_similarity_table

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-05-03 12:00:00.000000

Introduces the ``artist_similarity`` join table that the Last.fm
similar-artists enrichment writes to (Decision 059) and adds a gating
timestamp column on ``artists`` so the nightly backfill can skip already-
enriched rows.

Schema choices worth flagging:

* ``similar_artist_id`` is nullable. Last.fm returns similar artists by
  name; many of them won't have a row in ``artists`` because they're not
  performing in the DMV. When they DO exist in our database (added later
  by a scraper run), the resolution task fills in this column. The
  partial index on the column powers the "similar artists with upcoming
  shows" query without bloating the index with ~70-95% null rows.

* ``source`` is a string column (default ``'lastfm'``) so the same join
  table can later carry Spotify Related Artists or MusicBrainz
  relationships. Each source produces independent rows; the
  recommendation engine combines them at query time.

* The unique constraint on ``(source_artist_id, similar_artist_name,
  source)`` makes the storage step idempotent — re-running enrichment
  for the same artist updates rows in place rather than duplicating.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``artist_similarity`` and add the enrichment-gating column."""
    op.create_table(
        "artist_similarity",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "source_artist_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("artists.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("similar_artist_name", sa.String(length=300), nullable=False),
        sa.Column("similar_artist_mbid", sa.String(length=64), nullable=True),
        sa.Column(
            "similar_artist_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("artists.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "similarity_score",
            sa.Numeric(precision=4, scale=3),
            nullable=False,
        ),
        sa.Column(
            "source",
            sa.String(length=32),
            nullable=False,
            server_default="lastfm",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "idx_artist_similarity_unique",
        "artist_similarity",
        ["source_artist_id", "similar_artist_name", "source"],
        unique=True,
    )
    op.create_index(
        "idx_artist_similarity_source_score",
        "artist_similarity",
        ["source_artist_id", sa.text("similarity_score DESC")],
    )
    op.create_index(
        "idx_artist_similarity_similar_id",
        "artist_similarity",
        ["similar_artist_id"],
        postgresql_where=sa.text("similar_artist_id IS NOT NULL"),
    )

    op.add_column(
        "artists",
        sa.Column(
            "lastfm_similar_enriched_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "idx_artists_lastfm_similar_enriched_at",
        "artists",
        ["lastfm_similar_enriched_at"],
    )


def downgrade() -> None:
    """Drop the similarity table and the enrichment-gating column."""
    op.drop_index(
        "idx_artists_lastfm_similar_enriched_at",
        table_name="artists",
    )
    op.drop_column("artists", "lastfm_similar_enriched_at")
    op.drop_index(
        "idx_artist_similarity_similar_id",
        table_name="artist_similarity",
    )
    op.drop_index(
        "idx_artist_similarity_source_score",
        table_name="artist_similarity",
    )
    op.drop_index(
        "idx_artist_similarity_unique",
        table_name="artist_similarity",
    )
    op.drop_table("artist_similarity")

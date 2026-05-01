"""add_musicbrainz_columns_to_artists

Revision ID: a1b2c3d4e5f7
Revises: f3a4b5c6d7e8
Create Date: 2026-05-01 12:00:00.000000

Adds MusicBrainz enrichment columns to the ``artists`` table. The raw
``musicbrainz_genres`` and ``musicbrainz_tags`` JSONB blobs preserve
the upstream vote counts and casing so a future normalization pass can
weight them; ``musicbrainz_match_confidence`` records how sure we were
about the match so low-confidence rows can be re-evaluated later. The
``musicbrainz_enriched_at`` timestamp gates the nightly backfill so
already-enriched artists are skipped.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a1b2c3d4e5f7"
down_revision: str | None = "f3a4b5c6d7e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add MusicBrainz columns and supporting index to ``artists``."""
    op.add_column(
        "artists",
        sa.Column("musicbrainz_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "artists",
        sa.Column(
            "musicbrainz_genres",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "artists",
        sa.Column(
            "musicbrainz_tags",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "artists",
        sa.Column(
            "musicbrainz_enriched_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "artists",
        sa.Column(
            "musicbrainz_match_confidence",
            sa.Numeric(precision=3, scale=2),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_artists_musicbrainz_enriched_at",
        "artists",
        ["musicbrainz_enriched_at"],
    )


def downgrade() -> None:
    """Drop MusicBrainz columns and the supporting index."""
    op.drop_index(
        "ix_artists_musicbrainz_enriched_at",
        table_name="artists",
    )
    op.drop_column("artists", "musicbrainz_match_confidence")
    op.drop_column("artists", "musicbrainz_enriched_at")
    op.drop_column("artists", "musicbrainz_tags")
    op.drop_column("artists", "musicbrainz_genres")
    op.drop_column("artists", "musicbrainz_id")

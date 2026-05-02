"""add_lastfm_columns_to_artists

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f7
Create Date: 2026-05-02 12:00:00.000000

Adds Last.fm enrichment columns to the ``artists`` table. The raw
``lastfm_tags`` JSONB blob preserves Last.fm's user-applied tag list
verbatim — names, URLs, and ordering — so a future normalization sprint
can merge them with MusicBrainz data without re-enriching. The
listener count is stored as a popularity signal for future scoring
work, the canonical Last.fm artist URL is kept for debugging matches,
and ``lastfm_bio_summary`` is captured because Last.fm returns it for
free on every ``getInfo`` call (no point re-enriching to add bios
later). ``lastfm_match_confidence`` records how sure we were about the
match, and ``lastfm_enriched_at`` gates the nightly backfill so
already-enriched artists are skipped.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "a1b2c3d4e5f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add Last.fm columns and supporting index to ``artists``."""
    op.add_column(
        "artists",
        sa.Column(
            "lastfm_tags",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "artists",
        sa.Column("lastfm_listener_count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "artists",
        sa.Column("lastfm_url", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "artists",
        sa.Column("lastfm_bio_summary", sa.Text(), nullable=True),
    )
    op.add_column(
        "artists",
        sa.Column(
            "lastfm_enriched_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "artists",
        sa.Column(
            "lastfm_match_confidence",
            sa.Numeric(precision=3, scale=2),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_artists_lastfm_enriched_at",
        "artists",
        ["lastfm_enriched_at"],
    )


def downgrade() -> None:
    """Drop Last.fm columns and the supporting index."""
    op.drop_index(
        "ix_artists_lastfm_enriched_at",
        table_name="artists",
    )
    op.drop_column("artists", "lastfm_match_confidence")
    op.drop_column("artists", "lastfm_enriched_at")
    op.drop_column("artists", "lastfm_bio_summary")
    op.drop_column("artists", "lastfm_url")
    op.drop_column("artists", "lastfm_listener_count")
    op.drop_column("artists", "lastfm_tags")

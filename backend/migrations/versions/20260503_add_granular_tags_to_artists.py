"""add_granular_tags_to_artists

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-05-03 18:00:00.000000

Adds the consolidated granular-tag projection that the tag-overlap
similarity sprint (Decision 060) writes to. ``granular_tags`` is the
deduplicated, filtered, frequency-trimmed list of discriminative tags
extracted from the artist's MusicBrainz and Last.fm payloads;
``granular_tags_consolidated_at`` is the gating timestamp the nightly
backfill uses to skip artists whose source data has not changed.

The GIN index is the load-bearing piece. The tag-overlap similarity
query filters via the ``&&`` array overlap operator, which only stays
sub-linear when the GIN index is in place — without it every similarity
query becomes a sequential scan over the full artists table.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e5f6a7b8c9d0"
down_revision: str | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add ``granular_tags`` columns and supporting indexes to ``artists``."""
    op.add_column(
        "artists",
        sa.Column(
            "granular_tags",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("ARRAY[]::TEXT[]"),
        ),
    )
    op.add_column(
        "artists",
        sa.Column(
            "granular_tags_consolidated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "idx_artists_granular_tags",
        "artists",
        ["granular_tags"],
        postgresql_using="gin",
    )
    op.create_index(
        "idx_artists_granular_tags_consolidated_at",
        "artists",
        ["granular_tags_consolidated_at"],
    )


def downgrade() -> None:
    """Drop ``granular_tags`` columns and supporting indexes."""
    op.drop_index(
        "idx_artists_granular_tags_consolidated_at",
        table_name="artists",
    )
    op.drop_index(
        "idx_artists_granular_tags",
        table_name="artists",
    )
    op.drop_column("artists", "granular_tags_consolidated_at")
    op.drop_column("artists", "granular_tags")

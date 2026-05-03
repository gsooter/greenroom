"""add_canonical_genres_to_artists

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-05-02 18:00:00.000000

Adds canonical-genre columns to the ``artists`` table so the genre
normalization sprint (Decision 058) has somewhere to write its output.
``canonical_genres`` holds the ordered short-list of GREENROOM canonical
labels assigned to the artist; ``genre_confidence`` carries the per-
genre score in 0.0-1.0 (relative to the artist's strongest signal) so
downstream consumers can weight or filter; ``genres_normalized_at``
gates the nightly normalization pass and lets re-runs detect rows whose
upstream MusicBrainz/Last.fm payloads have moved on. The GIN index on
``canonical_genres`` is critical — the events filter and recommendation
queries do array overlap on it and a sequential scan would dominate the
request budget at scale.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add canonical-genre columns and supporting indexes to ``artists``."""
    op.add_column(
        "artists",
        sa.Column(
            "canonical_genres",
            postgresql.ARRAY(sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "artists",
        sa.Column(
            "genre_confidence",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "artists",
        sa.Column(
            "genres_normalized_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_artists_canonical_genres_gin",
        "artists",
        ["canonical_genres"],
        postgresql_using="gin",
    )
    op.create_index(
        "ix_artists_genres_normalized_at",
        "artists",
        ["genres_normalized_at"],
    )


def downgrade() -> None:
    """Drop canonical-genre columns and supporting indexes."""
    op.drop_index(
        "ix_artists_genres_normalized_at",
        table_name="artists",
    )
    op.drop_index(
        "ix_artists_canonical_genres_gin",
        table_name="artists",
    )
    op.drop_column("artists", "genres_normalized_at")
    op.drop_column("artists", "genre_confidence")
    op.drop_column("artists", "canonical_genres")

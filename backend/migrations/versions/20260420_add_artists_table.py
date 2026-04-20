"""add_artists_table

Revision ID: b7c8d9e0f1a2
Revises: a1b2c3d4e5f6
Create Date: 2026-04-20 14:00:00.000000

Introduces the ``artists`` table that the recommendation engine reads
for genre-overlap scoring when no direct artist match exists between
a user's music-service top artists and an event's performers. Each row
carries a normalized lookup key so duplicate scraper spellings collapse
into one row; ``spotify_enriched_at`` gates the nightly enrichment
sweep that populates ``spotify_id`` and ``genres`` from the Spotify
search API.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "b7c8d9e0f1a2"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the ``artists`` table and its supporting indexes."""
    op.create_table(
        "artists",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column(
            "normalized_name", sa.String(length=300), nullable=False, unique=True
        ),
        sa.Column("spotify_id", sa.String(length=50), nullable=True),
        sa.Column(
            "genres",
            postgresql.ARRAY(sa.String(length=50)),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "spotify_enriched_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_artists_normalized_name",
        "artists",
        ["normalized_name"],
    )
    op.create_index(
        "ix_artists_spotify_id",
        "artists",
        ["spotify_id"],
    )
    op.create_index(
        "ix_artists_genres_gin",
        "artists",
        ["genres"],
        postgresql_using="gin",
    )
    op.create_index(
        "ix_artists_spotify_enriched_at",
        "artists",
        ["spotify_enriched_at"],
    )


def downgrade() -> None:
    """Drop the ``artists`` table and its indexes."""
    op.drop_index("ix_artists_spotify_enriched_at", table_name="artists")
    op.drop_index("ix_artists_genres_gin", table_name="artists")
    op.drop_index("ix_artists_spotify_id", table_name="artists")
    op.drop_index("ix_artists_normalized_name", table_name="artists")
    op.drop_table("artists")

"""add_recent_artists_to_users

Revision ID: 4a9f2c5b8e31
Revises: 8c1d3a7e0a11
Create Date: 2026-04-18 14:00:00.000000

Adds per-user caches for Spotify recently-played artists, parallel to
the existing top-artist caches. The recommendation engine consumes
both lists so a user who's been listening to someone new this week
still gets matched even if that artist isn't in their 6-month top 200.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "4a9f2c5b8e31"
down_revision: Union[str, None] = "8c1d3a7e0a11"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add spotify_recent_artist_ids + spotify_recent_artists columns."""
    op.add_column(
        "users",
        sa.Column(
            "spotify_recent_artist_ids",
            postgresql.ARRAY(sa.String(length=100)),
            nullable=True,
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "spotify_recent_artists",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_users_spotify_recent_artist_ids_gin",
        "users",
        ["spotify_recent_artist_ids"],
        unique=False,
        postgresql_using="gin",
    )


def downgrade() -> None:
    """Drop the recent-artist columns."""
    op.drop_index(
        "ix_users_spotify_recent_artist_ids_gin",
        table_name="users",
    )
    op.drop_column("users", "spotify_recent_artists")
    op.drop_column("users", "spotify_recent_artist_ids")

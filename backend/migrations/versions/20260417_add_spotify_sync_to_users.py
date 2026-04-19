"""add_spotify_sync_to_users

Revision ID: 8c1d3a7e0a11
Revises: bde6f81e7ff5
Create Date: 2026-04-17 16:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "8c1d3a7e0a11"
down_revision: Union[str, None] = "bde6f81e7ff5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "spotify_top_artist_ids",
            postgresql.ARRAY(sa.String(length=100)),
            nullable=True,
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "spotify_top_artists",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "spotify_synced_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_users_spotify_top_artist_ids_gin",
        "users",
        ["spotify_top_artist_ids"],
        unique=False,
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_users_spotify_top_artist_ids_gin",
        table_name="users",
    )
    op.drop_column("users", "spotify_synced_at")
    op.drop_column("users", "spotify_top_artists")
    op.drop_column("users", "spotify_top_artist_ids")

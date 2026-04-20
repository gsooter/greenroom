"""add_tidal_and_apple_music_top_artist_caches_to_users

Revision ID: f4d92a0c1e51
Revises: e3c4f1b2a813
Create Date: 2026-04-20 12:00:00.000000

Two fixes shipped in one migration because they land together:

1. The Postgres ``oauth_provider`` enum is missing the uppercase
   ``APPLE_MUSIC`` / ``TIDAL`` values. SQLAlchemy binds the Python enum
   *name* rather than value for these columns, so connect flows fail
   with ``invalid input value for enum oauth_provider: "APPLE_MUSIC"``
   at the first repository lookup.

2. Tidal and Apple Music syncs currently overwrite the ``spotify_top_*``
   columns — a user with multiple connected services gets last-run-wins
   and the other services' artists vanish. Give each provider its own
   dedicated cache columns so the recommender can union them.

Downgrade removes the new columns but intentionally leaves the enum
values in place: Postgres can't drop enum values without rewriting the
type, and any rows that reference the new values would break.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "f4d92a0c1e51"
down_revision: Union[str, None] = "e3c4f1b2a813"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NEW_PROVIDER_VALUES: tuple[str, ...] = ("APPLE_MUSIC", "TIDAL")


def upgrade() -> None:
    """Add uppercase enum values and per-provider cache columns."""
    for value in _NEW_PROVIDER_VALUES:
        op.execute(
            sa.text(f"ALTER TYPE oauth_provider ADD VALUE IF NOT EXISTS '{value}'")
        )

    op.add_column(
        "users",
        sa.Column(
            "tidal_top_artist_ids",
            postgresql.ARRAY(sa.String(length=100)),
            nullable=True,
        ),
    )
    op.add_column(
        "users",
        sa.Column("tidal_top_artists", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column(
            "tidal_synced_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "apple_top_artist_ids",
            postgresql.ARRAY(sa.String(length=100)),
            nullable=True,
        ),
    )
    op.add_column(
        "users",
        sa.Column("apple_top_artists", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column(
            "apple_synced_at", sa.DateTime(timezone=True), nullable=True
        ),
    )

    op.create_index(
        "ix_users_tidal_top_artist_ids",
        "users",
        ["tidal_top_artist_ids"],
        postgresql_using="gin",
    )
    op.create_index(
        "ix_users_apple_top_artist_ids",
        "users",
        ["apple_top_artist_ids"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    """Drop the new columns and indexes. Enum values are left in place."""
    op.drop_index("ix_users_apple_top_artist_ids", table_name="users")
    op.drop_index("ix_users_tidal_top_artist_ids", table_name="users")
    op.drop_column("users", "apple_synced_at")
    op.drop_column("users", "apple_top_artists")
    op.drop_column("users", "apple_top_artist_ids")
    op.drop_column("users", "tidal_synced_at")
    op.drop_column("users", "tidal_top_artists")
    op.drop_column("users", "tidal_top_artist_ids")

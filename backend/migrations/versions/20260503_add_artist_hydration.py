"""add_artist_hydration_metadata_and_log

Revision ID: 9aa4bb5cc6dd
Revises: 9aa3bb4cc5dd
Create Date: 2026-05-03 23:00:00.000000

Adds the lineage and audit scaffolding for admin-triggered artist
hydration (Decision 067). Two changes:

* Four new columns on ``artists`` track where each row came from and
  how far it sits from a real DMV-scraped seed artist:

    - ``hydration_source`` — NULL for original (scraper-seeded) rows;
      ``'similar_artist'`` for rows added via the admin hydration tool.
    - ``hydrated_from_artist_id`` — the parent whose similar-artists
      list contributed this row. ``ON DELETE SET NULL`` so deleting
      a parent does not cascade and lose hydrated descendants.
    - ``hydration_depth`` — 0 for originals, 1 for first-generation
      hydrations, 2 for second-generation, etc. The hydration service
      hard-caps this at 2 to keep the catalog within two hops of a
      real DMV-scraped artist.
    - ``hydrated_at`` — when the row was created via hydration.

* A new ``hydration_log`` audit table records every hydration attempt
  (including those that hit the daily cap), so an operator can answer
  "who added this artist and from which parent?" without spelunking
  through application logs.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "9aa4bb5cc6dd"
down_revision: str | None = "9aa3bb4cc5dd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add hydration columns to ``artists`` and create ``hydration_log``."""
    op.add_column(
        "artists",
        sa.Column("hydration_source", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "artists",
        sa.Column(
            "hydrated_from_artist_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("artists.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "artists",
        sa.Column(
            "hydration_depth",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "artists",
        sa.Column(
            "hydrated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    op.create_index(
        "idx_artists_hydration_depth", "artists", ["hydration_depth"]
    )
    op.create_index(
        "idx_artists_hydrated_from",
        "artists",
        ["hydrated_from_artist_id"],
        postgresql_where=sa.text("hydrated_from_artist_id IS NOT NULL"),
    )

    op.create_table(
        "hydration_log",
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
        sa.Column("admin_email", sa.String(length=320), nullable=False),
        sa.Column("candidate_artists", postgresql.JSONB(), nullable=False),
        sa.Column(
            "added_artist_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
            server_default=sa.text("ARRAY[]::uuid[]"),
        ),
        sa.Column(
            "skipped_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "filtered_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "daily_cap_hit",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "idx_hydration_log_source", "hydration_log", ["source_artist_id"]
    )
    op.create_index(
        "idx_hydration_log_created_at",
        "hydration_log",
        [sa.text("created_at DESC")],
    )


def downgrade() -> None:
    """Drop ``hydration_log`` and remove the hydration columns from ``artists``."""
    op.drop_index("idx_hydration_log_created_at", table_name="hydration_log")
    op.drop_index("idx_hydration_log_source", table_name="hydration_log")
    op.drop_table("hydration_log")

    op.drop_index("idx_artists_hydrated_from", table_name="artists")
    op.drop_index("idx_artists_hydration_depth", table_name="artists")
    op.drop_column("artists", "hydrated_at")
    op.drop_column("artists", "hydration_depth")
    op.drop_column("artists", "hydrated_from_artist_id")
    op.drop_column("artists", "hydration_source")

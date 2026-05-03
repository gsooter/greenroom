"""add_region_id_to_cities

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-05-03 19:05:00.000000

Adds ``cities.region_id`` and backfills every existing city to the
DMV region created in the prior migration. The column is left
nullable for the duration of this migration to keep the ALTER cheap;
after the backfill it is flipped to NOT NULL. Future markets can be
introduced by inserting a region row and updating the cities to
point at it — no schema change required.

This sprint requires every existing city to be assigned to the DMV
region. The migration performs the assignment in SQL so that even
cities created outside the seed set (e.g. ``alexandria``,
``arlington``, custom additions) end up wired up automatically.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a7b8c9d0e1f2"
down_revision: str | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add ``region_id`` to ``cities``, backfill DMV, then enforce NOT NULL."""
    op.add_column(
        "cities",
        sa.Column(
            "region_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("regions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.create_index(
        "idx_cities_region_id",
        "cities",
        ["region_id"],
    )

    # Backfill: every existing city is in the DMV today (Decision 014
    # plus the implicit "DMV-only" footprint at launch). Future markets
    # land via row inserts, not migrations, so this single UPDATE is
    # safe even after multi-market expansion.
    op.execute(
        """
        UPDATE cities
        SET region_id = (SELECT id FROM regions WHERE slug = 'dmv')
        WHERE region_id IS NULL
        """
    )

    # Enforce NOT NULL after the backfill — every city must belong to a
    # region so the actionability overlay never has to handle "no
    # region recorded" as a special case in the hot path.
    op.alter_column("cities", "region_id", nullable=False)


def downgrade() -> None:
    """Drop the ``region_id`` column and its index."""
    op.drop_index("idx_cities_region_id", table_name="cities")
    op.drop_column("cities", "region_id")

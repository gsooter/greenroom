"""add_regions_table

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-05-03 19:00:00.000000

Introduces the ``regions`` table that the actionability overlay
(Sprint: DMV-aware ranking) consumes. A region groups cities that
users typically travel between for shows. Today there is one row —
DMV — covering DC, Baltimore, Richmond, and the surrounding NOVA
cities. The schema supports multi-market expansion (NYC, LA, etc.)
without code changes — adding a region is a row-level INSERT and a
``cities.region_id`` UPDATE.

This migration only creates the table and seeds the DMV row. The
follow-up migration (``add_region_id_to_cities``) wires cities to
regions and backfills ``region_id`` for every existing city.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f6a7b8c9d0e1"
down_revision: str | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``regions`` and seed the DMV row."""
    op.create_table(
        "regions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("slug", name="uq_regions_slug"),
    )

    op.execute(
        """
        INSERT INTO regions (slug, name, display_name, description)
        VALUES (
            'dmv',
            'DC, Maryland & Virginia',
            'DMV',
            'Washington DC and the surrounding metropolitan area '
            'including Baltimore, Northern Virginia, and Richmond.'
        )
        """
    )


def downgrade() -> None:
    """Drop the ``regions`` table."""
    op.drop_table("regions")

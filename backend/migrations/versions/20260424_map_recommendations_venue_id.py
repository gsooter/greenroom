"""map_recommendations_venue_id

Revision ID: a8b9c0d1e2f3
Revises: f2a3b4c5d6e7
Create Date: 2026-04-24 09:00:00.000000

Adds a nullable ``venue_id`` foreign key to ``map_recommendations`` so a
submission can be anchored to a specific venue (e.g. "the taco spot a
block from the 9:30 Club"). The column is nullable because the
standalone Tonight map still supports free-roaming pins that aren't
tied to a venue.

The upgrade backfills ``venue_id`` for existing rows: each
recommendation whose Apple-verified place sits within 1000 metres of a
venue is attached to the *closest* such venue. Recommendations with no
venue inside the 1000 m radius stay null.

Distance is computed with plain trigonometry (not PostGIS). The DMV
sits well inside the range where a spherical approximation is good to
a few metres, which is more than adequate for a 1000 m guardrail.
``LEAST(1.0, GREATEST(-1.0, ...))`` clamps the cosine argument so
rounding noise on near-identical points doesn't push ``acos`` outside
its domain.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a8b9c0d1e2f3"
down_revision: str | None = "f2a3b4c5d6e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add nullable venue_id FK + index, then backfill from nearest venue."""
    op.add_column(
        "map_recommendations",
        sa.Column(
            "venue_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("venues.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_map_recommendations_venue_id",
        "map_recommendations",
        ["venue_id"],
    )

    op.execute(
        """
        WITH nearest AS (
            SELECT DISTINCT ON (r.id)
                r.id AS rec_id,
                v.id AS venue_id
            FROM map_recommendations r
            JOIN venues v
              ON v.latitude IS NOT NULL
             AND v.longitude IS NOT NULL
            WHERE 6371000 * acos(LEAST(1.0, GREATEST(-1.0,
                cos(radians(v.latitude)) * cos(radians(r.latitude))
                * cos(radians(r.longitude) - radians(v.longitude))
                + sin(radians(v.latitude)) * sin(radians(r.latitude))
            ))) <= 1000
            ORDER BY
                r.id,
                6371000 * acos(LEAST(1.0, GREATEST(-1.0,
                    cos(radians(v.latitude)) * cos(radians(r.latitude))
                    * cos(radians(r.longitude) - radians(v.longitude))
                    + sin(radians(v.latitude)) * sin(radians(r.latitude))
                ))) ASC
        )
        UPDATE map_recommendations r
        SET venue_id = nearest.venue_id
        FROM nearest
        WHERE r.id = nearest.rec_id
        """
    )


def downgrade() -> None:
    """Drop the index and column. Backfilled associations are lost."""
    op.drop_index(
        "ix_map_recommendations_venue_id",
        table_name="map_recommendations",
    )
    op.drop_column("map_recommendations", "venue_id")

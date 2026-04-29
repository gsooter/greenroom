"""add_map_recommendations_and_votes

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-04-21 12:00:00.000000

Introduces the ``map_recommendations`` and ``map_recommendation_votes``
tables that back the community-sourced layer on Tonight's DC Map and
Shows Near Me. Every submission is anchored to an Apple-verified place
(lat/lng + address + similarity score), so the map cannot surface
unverifiable free-text locations.

Votes dedupe either by (recommendation, user_id) or
(recommendation, session_id) so a signed-out browser can still thumbs-up
a recommendation once without piling duplicate rows. ``suppressed_at``
is the admin / auto-suppression tombstone: set it to hide a submission
from the map feed without losing the row.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e1f2a3b4c5d6"
down_revision: str | None = "d0e1f2a3b4c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create recommendations + votes tables with indexes and constraints."""
    op.create_table(
        "map_recommendations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "submitter_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("session_id", sa.String(length=64), nullable=True),
        sa.Column("place_name", sa.String(length=200), nullable=False),
        sa.Column("place_address", sa.String(length=500), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("similarity_score", sa.Float(), nullable=False),
        sa.Column("category", sa.String(length=20), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("ip_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "suppressed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
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
        sa.CheckConstraint(
            "(submitter_user_id IS NOT NULL) OR (session_id IS NOT NULL)",
            name="ck_map_recommendations_has_submitter",
        ),
    )
    op.create_index(
        "ix_map_recommendations_lat_lng",
        "map_recommendations",
        ["latitude", "longitude"],
    )
    op.create_index(
        "ix_map_recommendations_created_at",
        "map_recommendations",
        ["created_at"],
    )
    op.create_index(
        "ix_map_recommendations_ip_hash_created_at",
        "map_recommendations",
        ["ip_hash", "created_at"],
    )
    op.create_index(
        "ix_map_recommendations_suppressed_at",
        "map_recommendations",
        ["suppressed_at"],
    )
    op.create_index(
        "ix_map_recommendations_category",
        "map_recommendations",
        ["category"],
    )

    op.create_table(
        "map_recommendation_votes",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "recommendation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("map_recommendations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("session_id", sa.String(length=64), nullable=True),
        sa.Column("value", sa.SmallInteger(), nullable=False),
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
        sa.CheckConstraint(
            "value IN (-1, 1)",
            name="ck_map_recommendation_votes_value",
        ),
        sa.CheckConstraint(
            "(user_id IS NOT NULL) <> (session_id IS NOT NULL)",
            name="ck_map_recommendation_votes_one_voter",
        ),
        sa.UniqueConstraint(
            "recommendation_id",
            "user_id",
            name="uq_map_recommendation_votes_rec_user",
        ),
        sa.UniqueConstraint(
            "recommendation_id",
            "session_id",
            name="uq_map_recommendation_votes_rec_session",
        ),
    )
    op.create_index(
        "ix_map_recommendation_votes_recommendation_id",
        "map_recommendation_votes",
        ["recommendation_id"],
    )


def downgrade() -> None:
    """Drop votes first (FK), then the recommendations table."""
    op.drop_index(
        "ix_map_recommendation_votes_recommendation_id",
        table_name="map_recommendation_votes",
    )
    op.drop_table("map_recommendation_votes")
    op.drop_index(
        "ix_map_recommendations_category", table_name="map_recommendations"
    )
    op.drop_index(
        "ix_map_recommendations_suppressed_at", table_name="map_recommendations"
    )
    op.drop_index(
        "ix_map_recommendations_ip_hash_created_at", table_name="map_recommendations"
    )
    op.drop_index(
        "ix_map_recommendations_created_at", table_name="map_recommendations"
    )
    op.drop_index(
        "ix_map_recommendations_lat_lng", table_name="map_recommendations"
    )
    op.drop_table("map_recommendations")

"""add_venue_comments_and_votes

Revision ID: c9d0e1f2a3b4
Revises: b7c8d9e0f1a2
Create Date: 2026-04-20 18:00:00.000000

Introduces the ``venue_comments`` and ``venue_comment_votes`` tables
that back the per-venue community notes feature. Comments are
category-tagged and rate-limited by ip_hash at the API layer; votes
dedupe either by (comment, user_id) or (comment, session_id) so a
signed-out browser can still upvote without piling on duplicate rows.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, None] = "b7c8d9e0f1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create comments + votes tables with their indexes and constraints."""
    op.create_table(
        "venue_comments",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "venue_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("venues.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("category", sa.String(length=20), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("ip_hash", sa.String(length=64), nullable=True),
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
        "ix_venue_comments_venue_id_created_at",
        "venue_comments",
        ["venue_id", "created_at"],
    )
    op.create_index(
        "ix_venue_comments_venue_id_category",
        "venue_comments",
        ["venue_id", "category"],
    )
    op.create_index(
        "ix_venue_comments_ip_hash_created_at",
        "venue_comments",
        ["ip_hash", "created_at"],
    )

    op.create_table(
        "venue_comment_votes",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "comment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("venue_comments.id", ondelete="CASCADE"),
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
        sa.CheckConstraint(
            "value IN (-1, 1)",
            name="ck_venue_comment_votes_value",
        ),
        sa.CheckConstraint(
            "(user_id IS NOT NULL) <> (session_id IS NOT NULL)",
            name="ck_venue_comment_votes_one_voter",
        ),
        sa.UniqueConstraint(
            "comment_id",
            "user_id",
            name="uq_venue_comment_votes_comment_user",
        ),
        sa.UniqueConstraint(
            "comment_id",
            "session_id",
            name="uq_venue_comment_votes_comment_session",
        ),
    )
    op.create_index(
        "ix_venue_comment_votes_comment_id",
        "venue_comment_votes",
        ["comment_id"],
    )


def downgrade() -> None:
    """Drop votes first (FK), then the comments table."""
    op.drop_index(
        "ix_venue_comment_votes_comment_id", table_name="venue_comment_votes"
    )
    op.drop_table("venue_comment_votes")
    op.drop_index(
        "ix_venue_comments_ip_hash_created_at", table_name="venue_comments"
    )
    op.drop_index(
        "ix_venue_comments_venue_id_category", table_name="venue_comments"
    )
    op.drop_index(
        "ix_venue_comments_venue_id_created_at", table_name="venue_comments"
    )
    op.drop_table("venue_comments")

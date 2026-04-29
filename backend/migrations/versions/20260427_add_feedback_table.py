"""add_feedback_table

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-04-27 12:00:00.000000

Introduces the ``feedback`` table that backs the in-app beta feedback
widget. Submissions are very lightweight — one freeform message plus a
``kind`` toggle — and may come from logged-in or anonymous browsers.

The table is intentionally simple: there is no parent/child relation,
no votes, no soft delete. ``is_resolved`` is a single boolean that ops
flips from the admin dashboard once a submission has been triaged.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f3a4b5c6d7e8"
down_revision: str | None = "e2f3a4b5c6d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the ``feedback`` table and its supporting indexes."""
    op.create_table(
        "feedback",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("page_url", sa.String(length=2048), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column(
            "is_resolved",
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
        sa.CheckConstraint(
            "kind IN ('bug', 'feature', 'general')",
            name="ck_feedback_kind",
        ),
    )
    op.create_index(
        "ix_feedback_created_at",
        "feedback",
        ["created_at"],
    )
    op.create_index(
        "ix_feedback_kind_created_at",
        "feedback",
        ["kind", "created_at"],
    )
    op.create_index(
        "ix_feedback_is_resolved_created_at",
        "feedback",
        ["is_resolved", "created_at"],
    )


def downgrade() -> None:
    """Drop the feedback table and its indexes."""
    op.drop_index("ix_feedback_is_resolved_created_at", table_name="feedback")
    op.drop_index("ix_feedback_kind_created_at", table_name="feedback")
    op.drop_index("ix_feedback_created_at", table_name="feedback")
    op.drop_table("feedback")

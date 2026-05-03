"""add_last_home_visit_at_to_users

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-05-03 19:30:00.000000

Adds ``users.last_home_visit_at`` to power the home page's "New since
your last visit" section. Each home-page load reads the current value
to compute the new-since window, then asynchronously updates it to
``now()`` so the next visit's window starts where this one ended.

The column is nullable: a first-time signed-in user has no recorded
prior visit, in which case the home page service falls back to a
30-day window so the section isn't empty for accounts that never
landed on the new home page before this change shipped.

An index is included because the column will be queried frequently
(every home-page render) and tends to be selective on a per-user
basis even though we always look it up by primary key today — the
index protects future ad-hoc analytics ("how many users opened the
home page this week") from a full scan.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8c9d0e1f2a3"
down_revision: str | None = "a7b8c9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add ``last_home_visit_at`` and its index to ``users``."""
    op.add_column(
        "users",
        sa.Column(
            "last_home_visit_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "idx_users_last_home_visit_at",
        "users",
        ["last_home_visit_at"],
    )


def downgrade() -> None:
    """Drop the column and its index."""
    op.drop_index("idx_users_last_home_visit_at", table_name="users")
    op.drop_column("users", "last_home_visit_at")

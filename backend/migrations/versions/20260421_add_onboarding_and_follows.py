"""add_onboarding_state_and_follow_tables

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-04-21 09:00:00.000000

Adds three tables that back the four-step ``/welcome`` onboarding flow:

1. ``user_onboarding_state`` — per-user bookkeeping. Each of the four
   steps carries its own nullable ``*_completed_at`` timestamp (skipping
   a step *also* sets the timestamp — skip semantics are "mark done,
   save no data"). ``banner_dismissed_at`` and
   ``browse_sessions_since_skipped`` back the persistent skip-banner
   shown to users who skipped the whole flow.
2. ``followed_artists`` — user → artist follow edges created by the
   artist search in Step 1.
3. ``followed_venues`` — user → venue follow edges created by the
   venue grid in Step 2.

Every existing ``users`` row gets a null-filled
``user_onboarding_state`` row on upgrade so existing users are also
funneled through the flow on their next login.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d0e1f2a3b4c5"
down_revision: str | None = "c9d0e1f2a3b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the three tables and back-fill one row per existing user."""
    op.create_table(
        "user_onboarding_state",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "taste_completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "venues_completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "music_services_completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "passkey_completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "skipped_entirely_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "banner_dismissed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "browse_sessions_since_skipped",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
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
    )

    op.create_table(
        "followed_artists",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "artist_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("artists.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_followed_artists_user_id_created_at",
        "followed_artists",
        ["user_id", "created_at"],
    )

    op.create_table(
        "followed_venues",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "venue_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("venues.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_followed_venues_user_id_created_at",
        "followed_venues",
        ["user_id", "created_at"],
    )

    # Back-fill: every existing user needs an onboarding-state row so
    # the /welcome gate funnels them through on next login. The row is
    # all-null by default — same as a brand-new signup.
    op.execute(
        sa.text(
            "INSERT INTO user_onboarding_state (user_id) "
            "SELECT id FROM users "
            "ON CONFLICT (user_id) DO NOTHING"
        )
    )


def downgrade() -> None:
    """Drop follow tables first, then onboarding state."""
    op.drop_index("ix_followed_venues_user_id_created_at", table_name="followed_venues")
    op.drop_table("followed_venues")
    op.drop_index(
        "ix_followed_artists_user_id_created_at", table_name="followed_artists"
    )
    op.drop_table("followed_artists")
    op.drop_table("user_onboarding_state")

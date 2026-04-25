"""add_pricing_links_and_refreshed_at

Revision ID: d1e2f3a4b5c6
Revises: c0d1e2f3a4b5
Create Date: 2026-04-25 22:00:00.000000

Introduces the multi-source pricing pipeline (Decision 023 superseded by
the new pricing-pipeline decision):

* ``event_pricing_links`` — per-(event, source) buy URL, affiliate URL,
  active flag, and last-seen-active timestamp. Decoupled from the
  ``ticket_pricing_snapshots`` history so a "no listings right now"
  state preserves the link for re-use the next time inventory comes
  back, without leaving a stale snapshot behind.
* ``events.prices_refreshed_at`` — denormalized "last successful price
  sweep" timestamp used by the manual-refresh cooldown gate and the
  "Updated 14 min ago" UI label. Snapshots have their own per-row
  timestamps; this column is the gate's single source of truth.
* ``ix_ticket_pricing_snapshots_event_id_created_at_desc`` — supports
  the latest-per-source query the UI runs to render the provider list
  on the event page in 1ms instead of 50ms.
* ``ix_ticket_pricing_snapshots_source_created_at`` — supports
  cross-event time-series queries the future ML pipeline will run.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d1e2f3a4b5c6"
down_revision: str | None = "c0d1e2f3a4b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create pricing-link table, freshness column, and snapshot indexes."""
    op.create_table(
        "event_pricing_links",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("url", sa.String(length=1000), nullable=False),
        sa.Column("affiliate_url", sa.String(length=1000), nullable=True),
        sa.Column(
            "last_active_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="USD"),
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
        sa.UniqueConstraint(
            "event_id",
            "source",
            name="uq_event_pricing_links_event_id_source",
        ),
    )
    op.create_index(
        "ix_event_pricing_links_event_id",
        "event_pricing_links",
        ["event_id"],
    )
    op.create_index(
        "ix_event_pricing_links_source",
        "event_pricing_links",
        ["source"],
    )

    op.add_column(
        "events",
        sa.Column(
            "prices_refreshed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_events_prices_refreshed_at",
        "events",
        ["prices_refreshed_at"],
    )

    op.create_index(
        "ix_snapshots_event_id_created_at_desc",
        "ticket_pricing_snapshots",
        ["event_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_snapshots_source_created_at",
        "ticket_pricing_snapshots",
        ["source", "created_at"],
    )


def downgrade() -> None:
    """Reverse the pricing-pipeline schema changes."""
    op.drop_index(
        "ix_snapshots_source_created_at",
        table_name="ticket_pricing_snapshots",
    )
    op.drop_index(
        "ix_snapshots_event_id_created_at_desc",
        table_name="ticket_pricing_snapshots",
    )
    op.drop_index("ix_events_prices_refreshed_at", table_name="events")
    op.drop_column("events", "prices_refreshed_at")
    op.drop_index(
        "ix_event_pricing_links_source",
        table_name="event_pricing_links",
    )
    op.drop_index(
        "ix_event_pricing_links_event_id",
        table_name="event_pricing_links",
    )
    op.drop_table("event_pricing_links")

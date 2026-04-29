"""backfill_event_timezones

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-04-22 08:50:00.000000

Corrects existing events whose ``starts_at`` / ``ends_at`` / ``on_sale_at``
values were stored with the wrong timezone.

Every scraper in the project yielded naive, venue-local wall-clock
datetimes (e.g. ``datetime(2026, 4, 22, 19, 0)`` for a 7 pm ET show).
Those naive values were inserted into a ``timestamptz`` column, which
Postgres silently interprets as UTC — shifting every DMV concert four
or five hours earlier than it actually plays.

The scraper code has been corrected to localize times via
``venue.city.timezone`` before writing, but existing rows are still
wrong. This migration re-interprets each stored ``timestamptz`` as if
its wall clock were in the venue's city timezone, then converts back to
true UTC. The downgrade reverses the operation so the revision is fully
reversible.

Safe to run on an empty table (``UPDATE`` with no matching rows is a
no-op). Not idempotent if applied twice without the revision tracking —
relying on Alembic's single-apply guarantee.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f2a3b4c5d6e7"
down_revision: str | None = "e1f2a3b4c5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Re-localize each event's datetimes from naive-as-UTC to true UTC.

    For every event, read the stored ``timestamptz`` as a naive wall
    clock (``AT TIME ZONE 'UTC'``), then reinterpret that wall clock as
    the venue's local timezone (``AT TIME ZONE c.timezone``). The result
    is a corrected ``timestamptz`` with the right UTC offset applied.
    """
    op.execute(
        """
        UPDATE events
        SET
            starts_at = (events.starts_at AT TIME ZONE 'UTC')
                AT TIME ZONE c.timezone,
            ends_at = CASE
                WHEN events.ends_at IS NOT NULL
                THEN (events.ends_at AT TIME ZONE 'UTC') AT TIME ZONE c.timezone
            END,
            on_sale_at = CASE
                WHEN events.on_sale_at IS NOT NULL
                THEN (events.on_sale_at AT TIME ZONE 'UTC') AT TIME ZONE c.timezone
            END
        FROM venues v
        JOIN cities c ON v.city_id = c.id
        WHERE events.venue_id = v.id
        """
    )


def downgrade() -> None:
    """Reverse the backfill — push datetimes back to naive-as-UTC.

    Reads each stored ``timestamptz`` as a wall clock in the venue's
    local timezone, then re-inserts that wall clock as if it were UTC.
    After downgrade, rows look exactly as they did before ``upgrade()``.
    """
    op.execute(
        """
        UPDATE events
        SET
            starts_at = (events.starts_at AT TIME ZONE c.timezone)
                AT TIME ZONE 'UTC',
            ends_at = CASE
                WHEN events.ends_at IS NOT NULL
                THEN (events.ends_at AT TIME ZONE c.timezone) AT TIME ZONE 'UTC'
            END,
            on_sale_at = CASE
                WHEN events.on_sale_at IS NOT NULL
                THEN (events.on_sale_at AT TIME ZONE c.timezone) AT TIME ZONE 'UTC'
            END
        FROM venues v
        JOIN cities c ON v.city_id = c.id
        WHERE events.venue_id = v.id
        """
    )

"""SQLAlchemy ORM models for scraper run tracking.

The validator uses scraper run history to detect anomalies — zero results
or >60% drop from the 30-run average triggers an alert (Decision 006).
The ``scraper_alerts`` table layered on top of that history records the
last delivery time for each alert key so the notifier can suppress
duplicates inside an operator-defined cooldown window.
"""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, Float, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.database import Base, TimestampMixin


class ScraperRunStatus(enum.StrEnum):
    """Status of a scraper run."""

    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class ScraperRun(TimestampMixin, Base):
    """A log of a single scraper execution.

    Used by the validator to track event counts over time and detect
    anomalies that trigger alerts.

    Attributes:
        id: Unique identifier for the scraper run.
        venue_slug: Slug of the venue that was scraped.
        scraper_class: Fully qualified class name of the scraper used.
        status: Outcome of the scraper run.
        event_count: Number of events returned by the scraper.
        started_at: When the scraper run started.
        finished_at: When the scraper run completed.
        duration_seconds: Total run duration in seconds.
        error_message: Error message if the run failed.
        metadata_json: Additional run metadata as JSONB.
    """

    __tablename__ = "scraper_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    venue_slug: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    scraper_class: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[ScraperRunStatus] = mapped_column(
        Enum(ScraperRunStatus, name="scraper_run_status", native_enum=True),
        nullable=False,
    )
    event_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    def __repr__(self) -> str:
        """Return a string representation of the ScraperRun.

        Returns:
            String representation with venue, status, and event count.
        """
        return (
            f"<ScraperRun {self.venue_slug} "
            f"{self.status.value} ({self.event_count} events)>"
        )


class ScraperAlert(TimestampMixin, Base):
    """Last-sent record per alert key, used to dedup notifier deliveries.

    Without this table a single broken scraper would post to Slack on
    every nightly run (and on every manual re-trigger from /admin),
    drowning the channel and training the operator to ignore it. Each
    alert the notifier emits carries an ``alert_key`` — typically
    ``"<reason>:<venue-slug>"`` — and a ``cooldown_hours`` window. The
    notifier checks ``last_sent_at`` before delivery and short-circuits
    when the previous send falls inside the window.

    ``sent_count`` increments on every (non-suppressed) send so the
    admin UI can surface "how often is this firing past the cooldown."

    Attributes:
        id: Unique identifier for the alert record.
        alert_key: Stable, human-readable key for the alert. Same key =
            same logical alert. Unique across the table.
        last_sent_at: Timestamp of the most recent (non-suppressed)
            delivery attempt.
        severity: Severity recorded on the most recent send
            (``"info"``, ``"warning"``, ``"error"``).
        title: Title from the most recent send.
        message: Message body from the most recent send.
        details: Optional structured detail payload from the most recent
            send, kept for the daily fleet-health digest.
        sent_count: Number of non-suppressed deliveries since the row
            was created.
    """

    __tablename__ = "scraper_alerts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    alert_key: Mapped[str] = mapped_column(
        String(200), nullable=False, unique=True, index=True
    )
    last_sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    sent_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    def __repr__(self) -> str:
        """Return a string representation of the ScraperAlert.

        Returns:
            String representation with key, severity, and last sent time.
        """
        return (
            f"<ScraperAlert {self.alert_key} "
            f"{self.severity} last_sent={self.last_sent_at.isoformat()}>"
        )

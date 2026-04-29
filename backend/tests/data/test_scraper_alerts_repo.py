"""Repository tests for :mod:`backend.data.repositories.scraper_alerts`.

Exercise the dedup-cooldown semantics that gate notifier delivery —
repeat sends inside the window are suppressed, sends past the window
are allowed and bump ``sent_count``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from backend.data.repositories import scraper_alerts as alerts_repo


def _now() -> datetime:
    """Return current UTC timestamp.

    Returns:
        Current datetime in UTC.
    """
    return datetime.now(UTC)


def test_should_suppress_returns_false_when_no_record(session: Session) -> None:
    """First-ever call for a key always allows delivery."""
    assert (
        alerts_repo.should_suppress(session, "zero_results:bc", cooldown_hours=6)
        is False
    )


def test_record_alert_creates_row_with_initial_state(session: Session) -> None:
    """A first record_alert call inserts a row with sent_count=1."""
    record = alerts_repo.record_alert(
        session,
        alert_key="zero_results:bc",
        severity="error",
        title="Zero results",
        message="Black Cat returned zero events.",
        details={"venue": "bc"},
    )
    assert record.id is not None
    assert record.sent_count == 1
    assert record.severity == "error"
    assert record.details == {"venue": "bc"}


def test_should_suppress_inside_cooldown_window(session: Session) -> None:
    """A send 1h ago is suppressed by a 6h cooldown."""
    one_hour_ago = _now() - timedelta(hours=1)
    alerts_repo.record_alert(
        session,
        alert_key="zero_results:bc",
        severity="error",
        title="t",
        message="m",
        now=one_hour_ago,
    )
    assert (
        alerts_repo.should_suppress(session, "zero_results:bc", cooldown_hours=6)
        is True
    )


def test_should_suppress_past_cooldown_window(session: Session) -> None:
    """A send 7h ago does not suppress a fresh 6h-cooldown send."""
    seven_hours_ago = _now() - timedelta(hours=7)
    alerts_repo.record_alert(
        session,
        alert_key="zero_results:bc",
        severity="error",
        title="t",
        message="m",
        now=seven_hours_ago,
    )
    assert (
        alerts_repo.should_suppress(session, "zero_results:bc", cooldown_hours=6)
        is False
    )


def test_zero_or_negative_cooldown_disables_suppression(session: Session) -> None:
    """A non-positive cooldown is opt-out — every send gets through."""
    alerts_repo.record_alert(
        session,
        alert_key="zero_results:bc",
        severity="error",
        title="t",
        message="m",
    )
    assert (
        alerts_repo.should_suppress(session, "zero_results:bc", cooldown_hours=0)
        is False
    )
    assert (
        alerts_repo.should_suppress(session, "zero_results:bc", cooldown_hours=-1)
        is False
    )


def test_record_alert_updates_existing_row_and_increments_count(
    session: Session,
) -> None:
    """A second record_alert call for the same key updates in place."""
    earlier = _now() - timedelta(hours=8)
    alerts_repo.record_alert(
        session,
        alert_key="event_drop:dc9",
        severity="warning",
        title="old",
        message="old body",
        details={"drop": "70%"},
        now=earlier,
    )
    later = _now()
    record = alerts_repo.record_alert(
        session,
        alert_key="event_drop:dc9",
        severity="error",
        title="new",
        message="new body",
        details={"drop": "85%"},
        now=later,
    )
    assert record.sent_count == 2
    assert record.severity == "error"
    assert record.title == "new"
    assert record.details == {"drop": "85%"}
    # Only one row exists for this key.
    assert alerts_repo.get_alert(session, "event_drop:dc9") is record


def test_list_recent_alerts_filters_and_orders(session: Session) -> None:
    """list_recent_alerts returns recent rows newest-first; older are excluded."""
    base = _now()
    alerts_repo.record_alert(
        session,
        alert_key="a",
        severity="error",
        title="t",
        message="m",
        now=base - timedelta(hours=2),
    )
    alerts_repo.record_alert(
        session,
        alert_key="b",
        severity="warning",
        title="t",
        message="m",
        now=base - timedelta(minutes=30),
    )
    alerts_repo.record_alert(
        session,
        alert_key="c",
        severity="info",
        title="t",
        message="m",
        now=base - timedelta(days=2),
    )

    recent = alerts_repo.list_recent_alerts(session, since=base - timedelta(hours=4))
    keys = [r.alert_key for r in recent]
    assert keys == ["b", "a"]


def test_count_active_alerts_in_window(session: Session) -> None:
    """count_active_alerts honors the same window as list_recent_alerts."""
    base = _now()
    alerts_repo.record_alert(
        session,
        alert_key="a",
        severity="error",
        title="t",
        message="m",
        now=base - timedelta(hours=1),
    )
    alerts_repo.record_alert(
        session,
        alert_key="b",
        severity="warning",
        title="t",
        message="m",
        now=base - timedelta(days=3),
    )
    assert (
        alerts_repo.count_active_alerts(session, since=base - timedelta(hours=24)) == 1
    )


def test_should_suppress_handles_naive_last_sent_at(session: Session) -> None:
    """Tolerant of naive datetimes when reading last_sent_at."""
    aware_recent = _now() - timedelta(minutes=5)
    naive_recent = aware_recent.replace(tzinfo=None)
    record = alerts_repo.record_alert(
        session,
        alert_key="naive:bc",
        severity="error",
        title="t",
        message="m",
        now=aware_recent,
    )
    # Force the persisted column to look naive — the read path must
    # still treat it as UTC.
    record.last_sent_at = naive_recent
    session.flush()

    assert alerts_repo.should_suppress(session, "naive:bc", cooldown_hours=6) is True

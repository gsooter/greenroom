"""Repository tests for :mod:`backend.data.repositories.scraper_runs`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from backend.data.models.scraper import ScraperRunStatus
from backend.data.repositories import scraper_runs as runs_repo


def _now() -> datetime:
    """Return current UTC timestamp.

    Returns:
        Current datetime in UTC.
    """
    return datetime.now(UTC)


def _seed(
    session: Session,
    *,
    venue_slug: str,
    status: ScraperRunStatus = ScraperRunStatus.SUCCESS,
    event_count: int = 10,
    started_at: datetime | None = None,
) -> None:
    runs_repo.create_scraper_run(
        session,
        venue_slug=venue_slug,
        scraper_class="test.Scraper",
        status=status,
        event_count=event_count,
        started_at=started_at or _now(),
    )


def test_create_scraper_run_persists_all_fields(session: Session) -> None:
    run = runs_repo.create_scraper_run(
        session,
        venue_slug="bc",
        scraper_class="test.Scraper",
        status=ScraperRunStatus.FAILED,
        event_count=0,
        started_at=_now(),
        finished_at=_now(),
        duration_seconds=1.5,
        error_message="boom",
        metadata_json={"trace": "x"},
    )
    assert run.id is not None
    assert run.status is ScraperRunStatus.FAILED
    assert run.metadata_json == {"trace": "x"}


def test_get_recent_runs_limit_and_order(session: Session) -> None:
    base = _now()
    for offset in range(5):
        _seed(
            session,
            venue_slug="bc",
            started_at=base - timedelta(minutes=offset),
        )
    _seed(session, venue_slug="other")  # filtered out by venue

    rows = runs_repo.get_recent_runs(session, "bc", limit=3)
    assert len(rows) == 3
    # Newest first.
    assert rows[0].started_at >= rows[1].started_at >= rows[2].started_at


def test_get_average_event_count_only_counts_success(session: Session) -> None:
    # None when no data.
    assert runs_repo.get_average_event_count(session, "empty") is None

    _seed(session, venue_slug="bc", event_count=10)
    _seed(session, venue_slug="bc", event_count=20)
    _seed(
        session,
        venue_slug="bc",
        event_count=999,
        status=ScraperRunStatus.FAILED,
    )
    avg = runs_repo.get_average_event_count(session, "bc")
    assert avg == 15.0


def test_get_average_event_count_respects_last_n(session: Session) -> None:
    base = _now()
    # Oldest run has an outlier; last_n_runs=2 should exclude it.
    _seed(
        session,
        venue_slug="bc",
        event_count=1000,
        started_at=base - timedelta(days=10),
    )
    _seed(session, venue_slug="bc", event_count=10, started_at=base)
    _seed(
        session,
        venue_slug="bc",
        event_count=20,
        started_at=base - timedelta(minutes=1),
    )
    avg = runs_repo.get_average_event_count(session, "bc", last_n_runs=2)
    assert avg == 15.0


def test_get_last_successful_run_filters(session: Session) -> None:
    assert runs_repo.get_last_successful_run(session, "bc") is None

    base = _now()
    _seed(
        session,
        venue_slug="bc",
        started_at=base - timedelta(minutes=5),
    )
    _seed(
        session,
        venue_slug="bc",
        status=ScraperRunStatus.FAILED,
        started_at=base,
    )  # more recent, but failed
    latest_success = runs_repo.get_last_successful_run(session, "bc")
    assert latest_success is not None
    assert latest_success.status is ScraperRunStatus.SUCCESS


def test_list_scraper_runs_filters_and_pagination(session: Session) -> None:
    base = _now()
    for i in range(4):
        _seed(
            session,
            venue_slug="bc",
            event_count=i,
            started_at=base - timedelta(seconds=i),
        )
    _seed(
        session,
        venue_slug="bc",
        status=ScraperRunStatus.FAILED,
        started_at=base - timedelta(seconds=10),
    )
    _seed(session, venue_slug="other")

    # No filter — total across all venues.
    _, total = runs_repo.list_scraper_runs(session)
    assert total >= 6

    rows, total = runs_repo.list_scraper_runs(session, venue_slug="bc")
    assert total == 5

    rows, total = runs_repo.list_scraper_runs(
        session, venue_slug="bc", status=ScraperRunStatus.FAILED
    )
    assert total == 1
    assert rows[0].status is ScraperRunStatus.FAILED

    # Pagination.
    page_1, _ = runs_repo.list_scraper_runs(
        session, venue_slug="bc", page=1, per_page=2
    )
    page_2, _ = runs_repo.list_scraper_runs(
        session, venue_slug="bc", page=2, per_page=2
    )
    assert len(page_1) == 2 and len(page_2) == 2
    # Ordering: page 1 items should be strictly newer than page 2.
    assert page_1[-1].started_at >= page_2[0].started_at


def test_count_failed_runs_since(session: Session) -> None:
    base = _now()
    _seed(
        session,
        venue_slug="bc",
        status=ScraperRunStatus.FAILED,
        started_at=base - timedelta(hours=1),
    )
    _seed(
        session,
        venue_slug="bc",
        status=ScraperRunStatus.FAILED,
        started_at=base - timedelta(minutes=30),
    )
    _seed(
        session,
        venue_slug="bc",
        status=ScraperRunStatus.SUCCESS,
        started_at=base - timedelta(minutes=10),
    )
    _seed(
        session,
        venue_slug="bc",
        status=ScraperRunStatus.FAILED,
        started_at=base - timedelta(days=2),
    )

    cutoff = base - timedelta(hours=2)
    assert runs_repo.count_failed_runs_since(session, "bc", cutoff) == 2

    recent_cutoff = base - timedelta(minutes=5)
    assert runs_repo.count_failed_runs_since(session, "bc", recent_cutoff) == 0

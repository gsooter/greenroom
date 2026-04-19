"""Post-scrape validation and alerting.

Checks scraper results against historical averages and fires alerts
when anomalies are detected. Zero results triggers an immediate alert.
A >60% drop from the 30-run average triggers a warning (Decision 006).
"""

from sqlalchemy.orm import Session

from backend.core.logging import get_logger
from backend.data.repositories import scraper_runs as runs_repo
from backend.scraper.notifier import send_alert

logger = get_logger(__name__)

# A drop greater than this fraction of the 30-run average triggers a warning
DROP_THRESHOLD = 0.6


def validate_scraper_result(
    session: Session,
    *,
    venue_slug: str,
    event_count: int,
) -> bool:
    """Validate a scraper run's results against historical data.

    Checks for two anomaly conditions:
    1. Zero results — always fires an error-level alert.
    2. Event count dropped >60% from the 30-run average — fires a warning.

    Args:
        session: Active SQLAlchemy session.
        venue_slug: Slug of the venue that was scraped.
        event_count: Number of events returned by the scraper.

    Returns:
        True if the result is valid (no anomalies), False otherwise.
    """
    if event_count == 0:
        send_alert(
            title=f"Zero results: {venue_slug}",
            message=(
                f"Scraper for '{venue_slug}' returned zero events. "
                f"The venue page may be down or the scraper may be broken."
            ),
            severity="error",
            details={"venue": venue_slug, "event_count": 0},
        )
        return False

    average = runs_repo.get_average_event_count(session, venue_slug)

    if average is None:
        logger.info("No historical data for '%s', skipping drop check.", venue_slug)
        return True

    if average == 0:
        return True

    drop_fraction = 1.0 - (event_count / average)

    if drop_fraction > DROP_THRESHOLD:
        send_alert(
            title=f"Event count drop: {venue_slug}",
            message=(
                f"Scraper for '{venue_slug}' returned {event_count} events, "
                f"which is a {drop_fraction:.0%} drop from the 30-run average "
                f"of {average:.0f}."
            ),
            severity="warning",
            details={
                "venue": venue_slug,
                "event_count": event_count,
                "average": f"{average:.0f}",
                "drop": f"{drop_fraction:.0%}",
            },
        )
        return False

    logger.info(
        "Validation passed for '%s': %d events (avg %.0f).",
        venue_slug,
        event_count,
        average,
    )
    return True

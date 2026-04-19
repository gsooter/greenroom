"""Watchdog that pings when DC9 re-enables its DICE events widget.

DC9 publishes its calendar through a DICE embed widget on
``dc9.club/events``. At time of writing (2026-04-17) the widget block is
HTML-commented out, so our ``DiceScraper`` has nothing to scrape and DC9
sits in the venue config with ``enabled=False`` (see Decision 019).

Rather than manually checking the venue every week, this task does it
for us: it fetches the page, strips HTML comments, and checks whether
the widget's DOM id remains. When it does, we Slack an alert so
somebody can flip DC9 back on (and, ideally, finish the DICE scraper).

Deliberately *not* a full scraper — it doesn't yield ``RawEvent``, it
doesn't write the database. It's a one-off status check that lives with
the scraper package because it watches the same surface the scraper
eventually will.
"""

from __future__ import annotations

import re

from celery import shared_task

from backend.core.logging import get_logger
from backend.scraper.base.http import HttpFetchError, fetch_html
from backend.scraper.notifier import send_alert

logger = get_logger(__name__)

DC9_EVENTS_URL = "https://dc9.club/events/"
WIDGET_ID = "dice-event-list-widget"

_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def is_widget_live(html: str) -> bool:
    """Return True when the DICE widget div is present outside of HTML comments.

    The venue toggles the widget on and off by wrapping it in
    ``<!-- ... -->``, so a raw substring check isn't enough — we need to
    look at what the browser would actually render. The simplest correct
    approach is to strip every HTML comment from the document first and
    then test for the widget's stable DOM id.

    Args:
        html: Raw HTML body of the DC9 events page.

    Returns:
        True when the widget is un-commented and therefore active.
    """
    stripped = _HTML_COMMENT_RE.sub("", html)
    return WIDGET_ID in stripped


@shared_task(  # type: ignore[untyped-decorator]
    name="backend.scraper.watchdogs.dc9_dice_widget.check_dc9_dice_widget"
)
def check_dc9_dice_widget() -> dict[str, object]:
    """Check whether DC9's DICE widget is live and alert if it flipped on.

    Runs weekly from beat. Fetches ``dc9.club/events``, decides whether
    the widget is commented out, and sends a single warning-level Slack
    alert when the widget is live (so the on-call engineer knows it's
    time to unblock the scraper).

    Fetch errors are logged and reported back in the return value so
    Celery result storage shows why a run didn't emit an alert; the
    task does not raise, because transient site flakiness shouldn't
    retry-storm the DC9 site or spam Slack.

    Returns:
        Dict with ``url``, ``live`` (bool | None), and ``error`` (str |
        None) for introspection via Celery result backends.
    """
    logger.info("DC9 DICE widget watchdog: fetching %s", DC9_EVENTS_URL)
    try:
        html = fetch_html(DC9_EVENTS_URL)
    except HttpFetchError as exc:
        logger.warning("DC9 watchdog fetch failed: %s", exc)
        return {"url": DC9_EVENTS_URL, "live": None, "error": str(exc)}

    live = is_widget_live(html)
    if live:
        send_alert(
            title="DC9 DICE widget is live",
            message=(
                "The DICE event-list widget on dc9.club/events is no longer "
                "HTML-commented out. Re-enable the DC9 venue scraper in "
                "backend/scraper/config/venues.py and confirm events start "
                "flowing on the next nightly run."
            ),
            severity="warning",
            details={"url": DC9_EVENTS_URL, "widget_id": WIDGET_ID},
        )
        logger.info("DC9 watchdog: widget is LIVE; alert dispatched.")
    else:
        logger.info("DC9 watchdog: widget still commented out; no action.")

    return {"url": DC9_EVENTS_URL, "live": live, "error": None}

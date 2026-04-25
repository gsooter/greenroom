"""Celery application and periodic-task schedule.

Single source of truth for the Celery app, its broker/backend
configuration, and the beat schedule for recurring jobs. Workers and
the scheduler both boot from this module:

    celery -A backend.celery_app worker -l info
    celery -A backend.celery_app beat   -l info

Task implementations live in their respective packages
(``backend.scraper.runner`` today; more to come) and are registered on
this app via the ``@celery_app.task`` decorator.
"""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from backend.core.config import get_settings


def _build_app() -> Celery:
    """Construct the Celery application instance.

    Returns:
        A configured :class:`Celery` app bound to the project's Redis
        broker and ready to register tasks against.
    """
    settings = get_settings()

    app = Celery(
        "greenroom",
        broker=settings.redis_url,
        backend=settings.redis_url,
        include=[
            "backend.scraper.runner",
            "backend.scraper.watchdogs.dc9_dice_widget",
            "backend.services.apple_music_tasks",
            "backend.services.artist_enrichment_tasks",
            "backend.services.pricing_tasks",
            "backend.services.scraper_digest",
            "backend.services.spotify_tasks",
        ],
    )

    app.conf.update(
        # Task behavior
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        task_time_limit=60 * 30,
        task_soft_time_limit=60 * 25,
        # Serialization — JSON only, no pickle.
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        # Keep the beat schedule local to this module so the schedule
        # is version-controlled alongside the task definitions.
        beat_schedule=_beat_schedule(),
        timezone="America/New_York",
        enable_utc=False,
    )

    return app


def _beat_schedule() -> dict[str, dict[str, object]]:
    """Return the recurring task schedule.

    Runs the full scraper fleet nightly at 04:00 America/New_York per
    Decision 005. Add new periodic jobs here as they come online.

    Returns:
        A mapping from schedule-entry name to Celery beat config dict.
    """
    return {
        "scrape-all-venues-nightly": {
            "task": "backend.scraper.runner.scrape_all_venues",
            "schedule": crontab(hour=4, minute=0),
            "options": {"expires": 60 * 60 * 3},
        },
        "watch-dc9-dice-widget-weekly": {
            "task": ("backend.scraper.watchdogs.dc9_dice_widget.check_dc9_dice_widget"),
            "schedule": crontab(hour=5, minute=0, day_of_week=1),
            "options": {"expires": 60 * 60},
        },
        # Drains the artist enrichment backlog. Runs at 05:00 ET so it
        # slots in after the 04:00 scraper pass has landed fresh rows
        # but well before the morning traffic peak.
        "enrich-unenriched-artists-nightly": {
            "task": (
                "backend.services.artist_enrichment_tasks.enrich_unenriched_artists"
            ),
            "schedule": crontab(hour=5, minute=30),
            "options": {"expires": 60 * 60 * 3},
        },
        # Posts the daily fleet-health digest at 07:30 ET, after the
        # nightly scrape and artist enrichment have settled. Acts as a
        # heartbeat so a silent on-call channel still confirms the job
        # actually ran.
        "send-scraper-digest-daily": {
            "task": "backend.services.scraper_digest.send_daily_digest",
            "schedule": crontab(hour=7, minute=30),
            "options": {"expires": 60 * 60 * 6},
        },
        # Sweeps every Tier A and Tier B pricing provider at 05:00 ET,
        # one hour after the nightly scrape so the latest event rows are
        # already settled but well before any morning traffic. Runs with
        # ``force=True`` inside the task so the manual-refresh cooldown
        # can't short-circuit the cron.
        "refresh-all-event-pricing-daily": {
            "task": "backend.services.pricing_tasks.refresh_all_event_pricing",
            "schedule": crontab(hour=5, minute=0),
            "options": {"expires": 60 * 60 * 4},
        },
    }


celery_app = _build_app()

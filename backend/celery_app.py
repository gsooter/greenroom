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
            "task": (
                "backend.scraper.watchdogs.dc9_dice_widget" ".check_dc9_dice_widget"
            ),
            "schedule": crontab(hour=5, minute=0, day_of_week=1),
            "options": {"expires": 60 * 60},
        },
    }


celery_app = _build_app()

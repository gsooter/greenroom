"""Admin business logic — scraper audit, manual triggers, user management.

Admin endpoints are gated by a shared secret (``ADMIN_SECRET_KEY``)
rather than a user JWT so operational tasks can be run from a CI job
or an on-call terminal without a real user session.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.core.config import get_settings
from backend.core.exceptions import (
    USER_NOT_FOUND,
    NotFoundError,
    ValidationError,
)
from backend.data.models.scraper import ScraperRun, ScraperRunStatus
from backend.data.repositories import scraper_runs as runs_repo
from backend.data.repositories import users as users_repo
from backend.scraper.config.venues import (
    VenueScraperConfig,
    get_enabled_configs,
    get_venue_config,
)
from backend.scraper.notifier import send_alert
from backend.scraper.runner import run_scraper_for_venue

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.orm import Session

    from backend.data.models.users import User


def list_scraper_runs(
    session: Session,
    *,
    venue_slug: str | None = None,
    status: str | None = None,
    page: int = 1,
    per_page: int = 50,
) -> tuple[list[ScraperRun], int]:
    """List scraper runs, newest first, with optional filters.

    Args:
        session: Active SQLAlchemy session.
        venue_slug: Optional venue slug to scope the listing.
        status: Optional status string (``success``, ``partial``, ``failed``).
        page: Page number, 1-indexed.
        per_page: Results per page. Maximum 100.

    Returns:
        Tuple of (runs list, total count).

    Raises:
        ValidationError: If ``per_page`` exceeds 100 or ``status`` is not
            a valid :class:`ScraperRunStatus` value.
    """
    if per_page > 100:
        raise ValidationError("per_page cannot exceed 100.")

    status_enum: ScraperRunStatus | None = None
    if status is not None:
        try:
            status_enum = ScraperRunStatus(status)
        except ValueError as exc:
            allowed = ", ".join(s.value for s in ScraperRunStatus)
            raise ValidationError(f"status must be one of: {allowed}") from exc

    return runs_repo.list_scraper_runs(
        session,
        venue_slug=venue_slug,
        status=status_enum,
        page=page,
        per_page=per_page,
    )


def trigger_scraper_run(session: Session, venue_slug: str) -> dict[str, Any]:
    """Synchronously run the scraper for a single venue.

    Meant for manual ops (backfills, debugging, post-fix verification).
    Nightly production runs still go through the Celery task; this is
    the same code path — it just bypasses the scheduler.

    Args:
        session: Active SQLAlchemy session.
        venue_slug: Slug of the venue to scrape.

    Returns:
        The dict returned by :func:`run_scraper_for_venue`, containing
        status, event counts, and duration.

    Raises:
        NotFoundError: If no venue config exists for the slug, or the
            venue is present but disabled.
    """
    config = _require_enabled_venue(venue_slug)
    return run_scraper_for_venue(session, config)


def summarize_fleet() -> dict[str, Any]:
    """Return a static summary of the configured scraper fleet.

    Includes per-region venue counts and total enabled/disabled venues.
    Does not touch the database — it's a pure read of the in-code
    config, suitable for a health dashboard.

    Returns:
        Dictionary with ``total``, ``enabled``, and per-region counts.
    """
    enabled = get_enabled_configs()
    by_region: dict[str, int] = {}
    for config in enabled:
        by_region[config.region] = by_region.get(config.region, 0) + 1

    return {
        "enabled": len(enabled),
        "by_region": by_region,
        "venues": [
            {
                "slug": c.venue_slug,
                "display_name": c.display_name,
                "region": c.region,
                "city_slug": c.city_slug,
                "scraper_class": c.scraper_class,
            }
            for c in enabled
        ],
    }


def serialize_scraper_run(run: ScraperRun) -> dict[str, Any]:
    """Serialize a :class:`ScraperRun` for the admin API response.

    Args:
        run: The scraper run to serialize.

    Returns:
        Dictionary representation of the run.
    """
    return {
        "id": str(run.id),
        "venue_slug": run.venue_slug,
        "scraper_class": run.scraper_class,
        "status": run.status.value,
        "event_count": run.event_count,
        "started_at": run.started_at.isoformat(),
        "finished_at": (run.finished_at.isoformat() if run.finished_at else None),
        "duration_seconds": run.duration_seconds,
        "error_message": run.error_message,
        "metadata": run.metadata_json or {},
    }


def list_users(
    session: Session,
    *,
    search: str | None = None,
    is_active: str | None = None,
    page: int = 1,
    per_page: int = 50,
) -> tuple[list[User], int]:
    """List users for the admin user-management table.

    Args:
        session: Active SQLAlchemy session.
        search: Optional case-insensitive substring of email or display_name.
        is_active: Optional ``"true"``/``"false"`` filter; anything else is
            rejected so query strings don't silently mismatch.
        page: Page number, 1-indexed.
        per_page: Results per page. Maximum 100.

    Returns:
        Tuple of (users list, total count).

    Raises:
        ValidationError: If ``per_page`` exceeds 100 or ``is_active`` is
            not one of the accepted strings.
    """
    if per_page > 100:
        raise ValidationError("per_page cannot exceed 100.")

    active_filter: bool | None = None
    if is_active is not None:
        if is_active not in {"true", "false"}:
            raise ValidationError("is_active must be 'true' or 'false'.")
        active_filter = is_active == "true"

    return users_repo.list_users(
        session,
        search=search,
        is_active=active_filter,
        page=page,
        per_page=per_page,
    )


def deactivate_user(session: Session, user_id: uuid.UUID) -> User:
    """Soft-delete a user by flipping ``is_active`` to False.

    Mirrors :func:`backend.services.users.deactivate_user` but is
    callable by an admin against any user, not only the authenticated
    one. Soft delete preserves saved events and recommendation history
    for analytics; the user can be reactivated later.

    Args:
        session: Active SQLAlchemy session.
        user_id: UUID of the user to deactivate.

    Returns:
        The updated :class:`User` instance.

    Raises:
        NotFoundError: If no user with that ID exists.
    """
    user = _require_user(session, user_id)
    return users_repo.update_user(session, user, is_active=False)


def reactivate_user(session: Session, user_id: uuid.UUID) -> User:
    """Restore a previously deactivated user.

    Args:
        session: Active SQLAlchemy session.
        user_id: UUID of the user to reactivate.

    Returns:
        The updated :class:`User` instance with ``is_active=True``.

    Raises:
        NotFoundError: If no user with that ID exists.
    """
    user = _require_user(session, user_id)
    return users_repo.update_user(session, user, is_active=True)


def delete_user(session: Session, user_id: uuid.UUID) -> None:
    """Hard-delete a user's local Greenroom profile.

    Removes the ``users`` row and cascades to ``music_service_connections``,
    ``saved_events``, and ``recommendations``. The Knuckles identity
    record is *not* touched — for a full erase, the operator must also
    delete the identity in Knuckles.

    Args:
        session: Active SQLAlchemy session.
        user_id: UUID of the user to delete.

    Raises:
        NotFoundError: If no user with that ID exists.
    """
    user = _require_user(session, user_id)
    users_repo.delete_user(session, user)


def serialize_user_summary(user: User) -> dict[str, Any]:
    """Serialize a user for the admin user-management table.

    Wider than :func:`backend.services.users.serialize_user`: includes
    ``is_active`` and connection counts so an admin can triage accounts
    without fetching each row.

    Args:
        user: The user to serialize.

    Returns:
        Dictionary representation of the user.
    """
    return {
        "id": str(user.id),
        "email": user.email,
        "display_name": user.display_name,
        "is_active": user.is_active,
        "city_id": str(user.city_id) if user.city_id else None,
        "music_connections": [c.provider.value for c in user.music_connections],
        "last_login_at": (
            user.last_login_at.isoformat() if user.last_login_at else None
        ),
        "onboarding_completed_at": (
            user.onboarding_completed_at.isoformat()
            if user.onboarding_completed_at
            else None
        ),
        "created_at": user.created_at.isoformat(),
    }


def send_test_alert(session: Session) -> dict[str, Any]:
    """Fire a non-suppressed info alert into every Slack channel.

    One alert is sent per category (``ops``, ``digest``, ``feedback``)
    so an operator can confirm each webhook lands in the right Slack
    channel. The notifier is invoked with ``alert_key=None`` so the
    test alerts bypass the cooldown table — operators can hit the
    button as often as they want.

    Args:
        session: Active SQLAlchemy session, forwarded to the notifier so
            the caller's transaction owns any incidental writes (the
            test alert itself does not write to ``scraper_alerts``).

    Returns:
        Dictionary with ``delivered`` (bool — True when *every* category
        send returned True from the notifier), ``categories`` (per-category
        delivery results plus whether the webhook is configured), the
        ``email_configured`` flag, and the ``title`` / ``severity`` of
        the test message. ``slack_configured`` is preserved as the OR
        across categories so older callers / dashboards still see a
        single boolean.
    """
    settings = get_settings()
    ops_configured = bool(
        settings.slack_webhook_ops_url and settings.slack_webhook_ops_url != "x"
    )
    digest_configured = bool(
        (settings.slack_webhook_digest_url or settings.slack_webhook_ops_url)
        and (
            settings.slack_webhook_digest_url != "x"
            and settings.slack_webhook_ops_url != "x"
        )
    )
    feedback_configured = bool(
        (settings.slack_webhook_feedback_url or settings.slack_webhook_ops_url)
        and (
            settings.slack_webhook_feedback_url != "x"
            and settings.slack_webhook_ops_url != "x"
        )
    )
    email_configured = bool(
        settings.alert_email
        and settings.alert_email != "x@x.com"
        and settings.resend_api_key
        and settings.resend_api_key != "x"
    )

    title = "Greenroom alert pipeline test"
    base_message = (
        "This is a test alert fired from the admin dashboard. If you can "
        "read this in Slack or your alerts inbox, the proactive alerting "
        "pipeline is wired up correctly. No action required."
    )

    category_results: dict[str, dict[str, Any]] = {}
    for category, configured in (
        ("ops", ops_configured),
        ("digest", digest_configured),
        ("feedback", feedback_configured),
    ):
        delivered = send_alert(
            title=f"{title} ({category})",
            message=base_message,
            severity="info",
            details={"source": "admin_test_button", "category": category},
            alert_key=None,
            session=session,
            category=category,  # type: ignore[arg-type]
        )
        category_results[category] = {
            "delivered": delivered,
            "configured": configured,
        }

    all_delivered = all(r["delivered"] for r in category_results.values())
    any_slack_configured = ops_configured or digest_configured or feedback_configured

    return {
        "delivered": all_delivered,
        "slack_configured": any_slack_configured,
        "categories": category_results,
        "email_configured": email_configured,
        "title": title,
        "severity": "info",
    }


def _require_user(session: Session, user_id: uuid.UUID) -> User:
    """Fetch a user or raise a structured 404.

    Args:
        session: Active SQLAlchemy session.
        user_id: UUID of the user to look up.

    Returns:
        The :class:`User` row.

    Raises:
        NotFoundError: If no user with that ID exists.
    """
    user = users_repo.get_user_by_id(session, user_id)
    if user is None:
        raise NotFoundError(
            code=USER_NOT_FOUND,
            message=f"No user found with id {user_id}",
        )
    return user


def _require_enabled_venue(venue_slug: str) -> VenueScraperConfig:
    """Look up a venue config by slug and require it to be enabled.

    Args:
        venue_slug: Slug of the venue.

    Returns:
        The :class:`VenueScraperConfig`.

    Raises:
        NotFoundError: If no config exists for the slug, or the venue is
            present but disabled.
    """
    config = get_venue_config(venue_slug)
    if config is None:
        raise NotFoundError(
            code="VENUE_CONFIG_NOT_FOUND",
            message=f"No scraper config for venue '{venue_slug}'",
        )
    if not config.enabled:
        raise NotFoundError(
            code="VENUE_CONFIG_DISABLED",
            message=f"Scraper for venue '{venue_slug}' is disabled.",
        )
    return config

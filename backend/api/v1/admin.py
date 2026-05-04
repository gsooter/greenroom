"""Admin route handlers — protected by a shared secret key.

All endpoints in this module require an ``X-Admin-Key`` header matching
``settings.admin_secret_key``. They are intentionally gated separately
from the user JWT so ops tasks can be driven by CI jobs or an on-call
terminal without a real user session.
"""

from __future__ import annotations

import hmac
import uuid
from collections.abc import Callable
from functools import wraps
from typing import Any, cast

from flask import request

from backend.api.v1 import api_v1
from backend.core.config import get_settings
from backend.core.database import get_db
from backend.core.exceptions import (
    USER_NOT_FOUND,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
    ValidationError,
)
from backend.services import admin as admin_service
from backend.services.admin_dashboard import (
    build_dashboard_snapshot,
    serialize_dashboard_snapshot,
)
from backend.services.artist_hydration import (
    execute_hydration,
    preview_hydration,
)


def require_admin[F: Callable[..., Any]](func: F) -> F:
    """Gate a route behind the shared admin secret.

    Uses :func:`hmac.compare_digest` to avoid timing attacks when the
    supplied key is wrong. The header name ``X-Admin-Key`` is picked to
    stay clear of the ``Authorization`` header used by user JWTs, so
    both schemes can coexist on the same deployment.

    Args:
        func: The Flask view function to wrap.

    Returns:
        The wrapped view function.

    Raises:
        UnauthorizedError: If the header is missing.
        ForbiddenError: If the header value does not match.
    """

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        """Verify the admin key before delegating to the view.

        Args:
            *args: Positional args forwarded to the wrapped view.
            **kwargs: Keyword args forwarded to the wrapped view.

        Returns:
            Whatever the wrapped view returns.
        """
        provided = request.headers.get("X-Admin-Key", "")
        if not provided:
            raise UnauthorizedError(message="Missing X-Admin-Key header.")
        expected = get_settings().admin_secret_key
        if not hmac.compare_digest(provided, expected):
            raise ForbiddenError(message="Invalid admin key.")
        return func(*args, **kwargs)

    return cast("F", wrapper)


@api_v1.route("/admin/scrapers", methods=["GET"])
@require_admin
def list_scrapers() -> tuple[dict[str, Any], int]:
    """Return a static summary of the configured scraper fleet.

    Does not touch the database. Useful for an ops dashboard header
    showing how many venues are wired up per region.

    Returns:
        Tuple of JSON response body and HTTP 200 status code.
    """
    return {"data": admin_service.summarize_fleet()}, 200


@api_v1.route("/admin/scraper-runs", methods=["GET"])
@require_admin
def list_scraper_runs() -> tuple[dict[str, Any], int]:
    """List scraper runs, newest first, with optional filters.

    Query parameters:
        venue_slug: string — scope to a single venue.
        status: string — filter by ``success``, ``partial``, or ``failed``.
        page: int — page number (default 1).
        per_page: int — results per page (default 50, max 100).

    Returns:
        Tuple of JSON response body and HTTP 200 status code.
    """
    session = get_db()
    venue_slug = request.args.get("venue_slug")
    status = request.args.get("status")
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 50, type=int)

    runs, total = admin_service.list_scraper_runs(
        session,
        venue_slug=venue_slug,
        status=status,
        page=page,
        per_page=per_page,
    )

    return {
        "data": [admin_service.serialize_scraper_run(r) for r in runs],
        "meta": {
            "total": total,
            "page": page,
            "per_page": per_page,
            "has_next": (page * per_page) < total,
        },
    }, 200


@api_v1.route("/admin/scrapers/<venue_slug>/run", methods=["POST"])
@require_admin
def trigger_scraper_run(venue_slug: str) -> tuple[dict[str, Any], int]:
    """Synchronously run the scraper for a single venue.

    Blocks until the scraper finishes and returns the same dict shape
    as the CLI runner. Intended for manual ops — nightly production
    runs go through the Celery task.

    Args:
        venue_slug: Slug of the venue to scrape.

    Returns:
        Tuple of JSON response body and HTTP 200 status code.

    Raises:
        NotFoundError: If the venue is unknown or disabled.
    """
    session = get_db()
    result = admin_service.trigger_scraper_run(session, venue_slug)
    return {"data": result}, 200


@api_v1.route("/admin/alerts/test", methods=["POST"])
@require_admin
def send_test_alert() -> tuple[dict[str, Any], int]:
    """Fire a non-suppressed info alert so the operator can verify delivery.

    Surfaces both the boolean ``delivered`` flag from the notifier and
    which channels are currently configured, so a misconfigured Slack
    webhook or unset Resend recipient is obvious in the response.

    Returns:
        Tuple of JSON response body and HTTP 200 status code.
    """
    session = get_db()
    return {"data": admin_service.send_test_alert(session)}, 200


@api_v1.route("/admin/hydrate-mass", methods=["POST"])
@require_admin
def trigger_mass_hydration() -> tuple[dict[str, Any], int]:
    """Enqueue the mass-hydrate Celery task and return immediately.

    Expects a JSON body of shape ``{"admin_email": "ops@..."}``. The
    body is required even though the email is also captured in the
    audit log — the gate alone (X-Admin-Key) does not identify the
    operator. The task itself is fire-and-forget; results land in the
    Celery result backend and the dashboard's "Most hydrated" leader-
    board on the next page load.

    Returns:
        Tuple of JSON response body and HTTP 202 status code.

    Raises:
        ValidationError: If the JSON body is missing or malformed.
    """
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ValidationError("Request body must be a JSON object.")
    admin_email = body.get("admin_email")
    if not isinstance(admin_email, str) or not admin_email.strip():
        raise ValidationError("admin_email is required.")

    # send_task by name keeps this route module free of a Celery import.
    from backend.celery_app import celery_app

    async_result = celery_app.send_task(
        "backend.services.artist_hydration_tasks.mass_hydrate_task",
        args=[admin_email.strip()],
    )
    return {
        "data": {
            "task_id": async_result.id,
            "status": "queued",
            "admin_email": admin_email.strip(),
        }
    }, 202


@api_v1.route("/admin/users", methods=["GET"])
@require_admin
def list_users() -> tuple[dict[str, Any], int]:
    """List Greenroom user profiles for the admin user-management table.

    Query parameters:
        search: string — case-insensitive substring of email or display_name.
        is_active: ``true``/``false`` — filter by active state.
        page: int — page number (default 1).
        per_page: int — results per page (default 50, max 100).

    Returns:
        Tuple of JSON response body and HTTP 200 status code.
    """
    session = get_db()
    search = request.args.get("search")
    is_active = request.args.get("is_active")
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 50, type=int)

    users, total = admin_service.list_users(
        session,
        search=search,
        is_active=is_active,
        page=page,
        per_page=per_page,
    )

    return {
        "data": [admin_service.serialize_user_summary(u) for u in users],
        "meta": {
            "total": total,
            "page": page,
            "per_page": per_page,
            "has_next": (page * per_page) < total,
        },
    }, 200


@api_v1.route("/admin/users/<user_id>/deactivate", methods=["POST"])
@require_admin
def deactivate_user(user_id: str) -> tuple[dict[str, Any], int]:
    """Soft-delete a user by flipping ``is_active`` to False.

    Args:
        user_id: UUID of the user to deactivate.

    Returns:
        Tuple of JSON response body and HTTP 200 status code.

    Raises:
        NotFoundError: If ``user_id`` is malformed or no user exists.
    """
    session = get_db()
    parsed = _parse_user_uuid(user_id)
    user = admin_service.deactivate_user(session, parsed)
    return {"data": admin_service.serialize_user_summary(user)}, 200


@api_v1.route("/admin/users/<user_id>/reactivate", methods=["POST"])
@require_admin
def reactivate_user(user_id: str) -> tuple[dict[str, Any], int]:
    """Restore a previously deactivated user.

    Args:
        user_id: UUID of the user to reactivate.

    Returns:
        Tuple of JSON response body and HTTP 200 status code.

    Raises:
        NotFoundError: If ``user_id`` is malformed or no user exists.
    """
    session = get_db()
    parsed = _parse_user_uuid(user_id)
    user = admin_service.reactivate_user(session, parsed)
    return {"data": admin_service.serialize_user_summary(user)}, 200


@api_v1.route("/admin/users/<user_id>", methods=["DELETE"])
@require_admin
def delete_user(user_id: str) -> tuple[dict[str, Any], int]:
    """Hard-delete a user's local Greenroom profile and cascaded children.

    Does *not* delete the upstream Knuckles identity — operators must
    erase that separately if a full account wipe is required.

    Args:
        user_id: UUID of the user to delete.

    Returns:
        Tuple of JSON response body and HTTP 200 status code.

    Raises:
        NotFoundError: If ``user_id`` is malformed or no user exists.
    """
    session = get_db()
    parsed = _parse_user_uuid(user_id)
    admin_service.delete_user(session, parsed)
    return {"data": {"id": str(parsed), "deleted": True}}, 200


@api_v1.route("/admin/dashboard", methods=["GET"])
@require_admin
def get_dashboard() -> tuple[dict[str, Any], int]:
    """Return the assembled admin dashboard snapshot.

    Returns:
        Tuple of JSON response body and HTTP 200 status code.
    """
    session = get_db()
    snapshot = build_dashboard_snapshot(session)
    return {"data": serialize_dashboard_snapshot(snapshot)}, 200


@api_v1.route("/admin/artists", methods=["GET"])
@require_admin
def list_artists() -> tuple[dict[str, Any], int]:
    """Search artists by name for the admin hydration UI.

    Query parameters:
        search: required string — case-insensitive name substring.
        limit: int — maximum results to return (default 20, max 50).

    Returns:
        Tuple of JSON response body and HTTP 200 status code.
    """
    session = get_db()
    search = request.args.get("search", "").strip()
    limit = min(int(request.args.get("limit", 20)), 50)
    artists = admin_service.search_artists_for_admin(
        session, search=search, limit=limit
    )
    return {"data": [admin_service.serialize_artist_summary(a) for a in artists]}, 200


@api_v1.route("/admin/artists/<artist_id>/hydration-preview", methods=["GET"])
@require_admin
def get_hydration_preview(artist_id: str) -> tuple[dict[str, Any], int]:
    """Return what hydrating ``artist_id`` would do, without modifying state.

    Args:
        artist_id: UUID of the seed artist whose similar-artists list
            would be hydrated.

    Returns:
        Tuple of JSON response body and HTTP 200 status code.

    Raises:
        NotFoundError: If ``artist_id`` is malformed or no artist exists.
    """
    parsed = _parse_artist_uuid(artist_id)
    session = get_db()
    preview = preview_hydration(session, parsed)
    if preview is None:
        raise NotFoundError(
            code="ARTIST_NOT_FOUND",
            message=f"No artist found with id {artist_id}",
        )
    return {"data": admin_service.serialize_hydration_preview(preview)}, 200


@api_v1.route("/admin/artists/<artist_id>/hydrate", methods=["POST"])
@require_admin
def trigger_hydration(artist_id: str) -> tuple[dict[str, Any], int]:
    """Execute a confirmed hydration for ``artist_id``.

    Expects a JSON body of shape::

        {
          "admin_email": "ops@greenroom.test",
          "confirmed_candidates": ["Mt. Joy", "Wild Rivers"],
          "immediate": false
        }

    ``immediate`` is optional; when True the per-artist enrichment
    tasks are queued for immediate execution instead of waiting for
    the nightly schedule.

    Args:
        artist_id: UUID of the seed artist.

    Returns:
        Tuple of JSON response body and HTTP 200 status code. Note that
        a 200 with ``added_count == 0`` and ``blocking_reason`` set is
        the contract for "no-op" cases (depth exhausted, daily cap hit
        between modal open and confirm); the request itself succeeded
        even though no artists were added.

    Raises:
        NotFoundError: If ``artist_id`` is malformed.
        ValidationError: If the JSON body is missing or malformed.
    """
    parsed = _parse_artist_uuid(artist_id)
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ValidationError("Request body must be a JSON object.")
    admin_email = body.get("admin_email")
    if not isinstance(admin_email, str) or not admin_email.strip():
        raise ValidationError("admin_email is required.")
    confirmed = body.get("confirmed_candidates", [])
    if not isinstance(confirmed, list) or any(
        not isinstance(c, str) for c in confirmed
    ):
        raise ValidationError("confirmed_candidates must be a list of strings.")
    immediate = bool(body.get("immediate", False))

    session = get_db()
    result = execute_hydration(
        session,
        parsed,
        admin_email=admin_email.strip(),
        confirmed_candidates=confirmed,
        immediate=immediate,
    )
    return {"data": admin_service.serialize_hydration_result(result)}, 200


def _parse_artist_uuid(value: str) -> uuid.UUID:
    """Parse a path-segment UUID or raise a 404 for malformed input.

    Symmetric with :func:`_parse_user_uuid`. Hydration endpoints prefer
    the same "treat malformed as not-found" contract so the dashboard
    only has to handle one error path.

    Args:
        value: Raw path segment from the URL.

    Returns:
        Parsed UUID.

    Raises:
        NotFoundError: If ``value`` is not a valid UUID.
    """
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise NotFoundError(
            code="ARTIST_NOT_FOUND",
            message=f"No artist found with id {value}",
        ) from exc


def _parse_user_uuid(value: str) -> uuid.UUID:
    """Parse a path-segment UUID or raise a 404 for malformed input.

    A malformed UUID in this admin path is treated as "no such user"
    rather than a 422 ValidationError to keep the contract simple for
    the dashboard — it always handles a single 404 path either way.

    Args:
        value: Raw path segment from the URL.

    Returns:
        Parsed UUID.

    Raises:
        NotFoundError: If ``value`` is not a valid UUID.
    """
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise NotFoundError(
            code=USER_NOT_FOUND,
            message=f"No user found with id {value}",
        ) from exc

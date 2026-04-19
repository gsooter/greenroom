"""Admin route handlers — protected by a shared secret key.

All endpoints in this module require an ``X-Admin-Key`` header matching
``settings.admin_secret_key``. They are intentionally gated separately
from the user JWT so ops tasks can be driven by CI jobs or an on-call
terminal without a real user session.
"""

from __future__ import annotations

import hmac
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar, cast

from flask import request

from backend.api.v1 import api_v1
from backend.core.config import get_settings
from backend.core.database import get_db
from backend.core.exceptions import ForbiddenError, UnauthorizedError
from backend.services import admin as admin_service

F = TypeVar("F", bound=Callable[..., Any])


def require_admin(func: F) -> F:
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

    return cast(F, wrapper)


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

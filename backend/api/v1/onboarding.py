"""Onboarding state, follows, and genre route handlers.

Endpoints:

* ``GET  /me/onboarding`` — current state + banner eligibility.
* ``POST /me/onboarding/steps/<step>/complete`` — mark a step done.
* ``POST /me/onboarding/skip-all`` — bail out of the whole flow.
* ``POST /me/onboarding/banner/dismiss`` — stop showing the skip banner.
* ``POST /me/onboarding/sessions/increment`` — bump browse counter.
* ``GET  /artists?query=...`` — artist search with follow-state flag.
* ``POST /me/followed-artists/<id>``         — follow an artist.
* ``DELETE /me/followed-artists/<id>``       — unfollow.
* ``GET  /me/followed-artists``              — paginated list.
* ``POST /me/followed-venues`` (batch body)  — follow many venues.
* ``DELETE /me/followed-venues/<id>``        — unfollow a venue.
* ``GET  /me/followed-venues``               — paginated list.
* ``GET  /genres`` — canonical genre catalog for the taste step.

The route layer does request validation only; every decision — which
step just completed, the banner-eligibility rule, limit clamping —
lives in the services it calls.
"""

from __future__ import annotations

import uuid
from typing import Any

from flask import request

from backend.api.v1 import api_v1
from backend.core.auth import get_current_user, require_auth
from backend.core.database import get_db
from backend.core.exceptions import ValidationError
from backend.core.genres import GENRES
from backend.services import follows as follows_service
from backend.services import onboarding as onboarding_service

# ---------------------------------------------------------------------------
# Onboarding state
# ---------------------------------------------------------------------------


@api_v1.route("/me/onboarding", methods=["GET"])
@require_auth
def get_onboarding_state() -> tuple[dict[str, Any], int]:
    """Return the authenticated user's onboarding progress.

    Returns:
        Tuple of JSON body and HTTP 200. The body's ``data`` shape is
        documented on :func:`backend.services.onboarding.serialize_state`.
    """
    session = get_db()
    state = onboarding_service.get_state(session, get_current_user())
    return {"data": onboarding_service.serialize_state(state)}, 200


@api_v1.route("/me/onboarding/steps/<step>/complete", methods=["POST"])
@require_auth
def complete_step(step: str) -> tuple[dict[str, Any], int]:
    """Mark one onboarding step as complete (finished or skipped).

    Args:
        step: The step identifier. Must be one of ``taste``, ``venues``,
            ``music_services``, ``passkey``.

    Returns:
        Tuple of JSON body (updated state) and HTTP 200.

    Raises:
        ValidationError: If ``step`` is not a recognized identifier.
    """
    session = get_db()
    state = onboarding_service.mark_step_complete(session, get_current_user(), step)
    return {"data": onboarding_service.serialize_state(state)}, 200


@api_v1.route("/me/onboarding/skip-all", methods=["POST"])
@require_auth
def skip_entire_flow() -> tuple[dict[str, Any], int]:
    """Flag that the user bailed out of the whole ``/welcome`` flow.

    All four per-step timestamps are stamped too so the gate does not
    re-trap the caller on the next login.

    Returns:
        Tuple of JSON body (updated state) and HTTP 200.
    """
    session = get_db()
    state = onboarding_service.mark_skipped_entirely(session, get_current_user())
    return {"data": onboarding_service.serialize_state(state)}, 200


@api_v1.route("/me/onboarding/banner/dismiss", methods=["POST"])
@require_auth
def dismiss_skip_banner() -> tuple[dict[str, Any], int]:
    """Stop showing the browse-pages skip banner for this user.

    Returns:
        Tuple of JSON body (updated state) and HTTP 200.
    """
    session = get_db()
    state = onboarding_service.dismiss_banner(session, get_current_user())
    return {"data": onboarding_service.serialize_state(state)}, 200


@api_v1.route("/me/onboarding/sessions/increment", methods=["POST"])
@require_auth
def increment_browse_sessions() -> tuple[dict[str, Any], int]:
    """Bump the browse-session counter that auto-hides the skip banner.

    The browse frontend calls this once per tab session, guarded by a
    ``sessionStorage`` flag. No-op server-side for users who never
    skipped — the counter only matters while the banner is eligible.

    Returns:
        Tuple of JSON body (updated state) and HTTP 200.
    """
    session = get_db()
    state = onboarding_service.increment_browse_sessions(session, get_current_user())
    return {"data": onboarding_service.serialize_state(state)}, 200


# ---------------------------------------------------------------------------
# Genres
# ---------------------------------------------------------------------------


@api_v1.route("/genres", methods=["GET"])
def list_genres() -> tuple[dict[str, Any], int]:
    """Return the canonical genre catalog for the taste step.

    Public — the onboarding flow is the primary consumer but the list
    is not user-specific so it is cacheable at the CDN.

    Returns:
        Tuple of JSON body ``{data: {genres: [...]}}`` and HTTP 200.
    """
    return {"data": {"genres": [dict(g) for g in GENRES]}}, 200


# ---------------------------------------------------------------------------
# Artists (search + follow)
# ---------------------------------------------------------------------------


@api_v1.route("/artists", methods=["GET"])
@require_auth
def search_artists() -> tuple[dict[str, Any], int]:
    """Search artists by name, tagged with the caller's follow state.

    Query parameters:
        query: Raw search string. Empty/whitespace returns an empty list.
        limit: Max rows to return (default 10, service-side clamped).

    Returns:
        Tuple of JSON body ``{data: {artists: [...]}}`` and HTTP 200.
    """
    session = get_db()
    query = request.args.get("query", "", type=str)
    limit = request.args.get("limit", 10, type=int)
    results = follows_service.search_artists_for_user(
        session, get_current_user(), query=query, limit=limit
    )
    return {"data": {"artists": results}}, 200


@api_v1.route("/me/followed-artists/<artist_id>", methods=["POST"])
@require_auth
def follow_artist(artist_id: str) -> tuple[dict[str, Any], int]:
    """Follow an artist.

    Idempotent — calling this again for an artist the caller already
    follows is a no-op but still returns 201 for a consistent UI
    contract (the follow state is what the button wants).

    Args:
        artist_id: UUID string of the artist.

    Returns:
        Tuple of empty body and HTTP 201.

    Raises:
        ValidationError: If ``artist_id`` is not a UUID.
        NotFoundError: If the artist does not exist.
    """
    session = get_db()
    parsed = _parse_uuid(artist_id, label="artist_id")
    follows_service.follow_artist(session, get_current_user(), parsed)
    return {}, 201


@api_v1.route("/me/followed-artists/<artist_id>", methods=["DELETE"])
@require_auth
def unfollow_artist(artist_id: str) -> tuple[dict[str, Any], int]:
    """Unfollow an artist.

    Idempotent — returns 204 even if the follow edge was never there.

    Args:
        artist_id: UUID string of the artist.

    Returns:
        Tuple of empty body and HTTP 204.

    Raises:
        ValidationError: If ``artist_id`` is not a UUID.
    """
    session = get_db()
    parsed = _parse_uuid(artist_id, label="artist_id")
    follows_service.unfollow_artist(session, get_current_user(), parsed)
    return {}, 204


@api_v1.route("/me/followed-artists", methods=["GET"])
@require_auth
def list_followed_artists() -> tuple[dict[str, Any], int]:
    """List the authenticated user's followed artists.

    Query parameters:
        page: 1-indexed page number (default 1).
        per_page: Page size cap (default 50, max 100).

    Returns:
        Tuple of JSON body and HTTP 200.
    """
    session = get_db()
    page, per_page = _parse_pagination()
    artists, total = follows_service.list_followed_artists(
        session, get_current_user(), page=page, per_page=per_page
    )
    return _paginated_response(artists, total=total, page=page, per_page=per_page)


# ---------------------------------------------------------------------------
# Venues (follow)
# ---------------------------------------------------------------------------


@api_v1.route("/me/followed-venues", methods=["POST"])
@require_auth
def follow_venues_bulk() -> tuple[dict[str, Any], int]:
    """Follow many venues in one round-trip.

    Request body: ``{"venue_ids": ["<uuid>", "<uuid>", ...]}``. Any
    missing or malformed id aborts the whole batch (all-or-nothing).

    Returns:
        Tuple of JSON body ``{data: {written: N}}`` and HTTP 201.

    Raises:
        ValidationError: If the body is missing, not an object, or
            ``venue_ids`` is not a list of UUID strings.
        NotFoundError: If any id does not resolve to a venue.
    """
    session = get_db()
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ValidationError("Request body must be a JSON object.")
    raw_ids = payload.get("venue_ids")
    if not isinstance(raw_ids, list):
        raise ValidationError("venue_ids must be an array of UUID strings.")
    parsed_ids = [_parse_uuid(v, label="venue_ids[]") for v in raw_ids]
    written = follows_service.follow_venues_bulk(
        session, get_current_user(), parsed_ids
    )
    return {"data": {"written": written}}, 201


@api_v1.route("/me/followed-venues/<venue_id>", methods=["DELETE"])
@require_auth
def unfollow_venue(venue_id: str) -> tuple[dict[str, Any], int]:
    """Unfollow a venue.

    Args:
        venue_id: UUID string of the venue.

    Returns:
        Tuple of empty body and HTTP 204.

    Raises:
        ValidationError: If ``venue_id`` is not a UUID.
    """
    session = get_db()
    parsed = _parse_uuid(venue_id, label="venue_id")
    follows_service.unfollow_venue(session, get_current_user(), parsed)
    return {}, 204


@api_v1.route("/me/followed-venues", methods=["GET"])
@require_auth
def list_followed_venues() -> tuple[dict[str, Any], int]:
    """List the authenticated user's followed venues.

    Query parameters:
        page: 1-indexed page number (default 1).
        per_page: Page size cap (default 50, max 100).

    Returns:
        Tuple of JSON body and HTTP 200.
    """
    session = get_db()
    page, per_page = _parse_pagination()
    venues, total = follows_service.list_followed_venues(
        session, get_current_user(), page=page, per_page=per_page
    )
    return _paginated_response(venues, total=total, page=page, per_page=per_page)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_uuid(value: Any, *, label: str) -> uuid.UUID:
    """Parse a string as a UUID or raise a labeled ValidationError.

    Args:
        value: Raw value from the URL or request body.
        label: Field name to include in the error message.

    Returns:
        Parsed UUID.

    Raises:
        ValidationError: If ``value`` is not a valid UUID string.
    """
    if not isinstance(value, str):
        raise ValidationError(f"{label} must be a UUID string.")
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise ValidationError(f"{label} is not a valid UUID: '{value}'") from exc


def _parse_pagination() -> tuple[int, int]:
    """Parse ``page``/``per_page`` from the query string with clamping.

    Returns:
        Tuple of (page, per_page). ``per_page`` is clamped at 100.

    Raises:
        ValidationError: If ``per_page`` exceeds 100.
    """
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 50, type=int)
    if per_page > 100:
        raise ValidationError("per_page cannot exceed 100.")
    return page, per_page


def _paginated_response(
    items: list[dict[str, Any]],
    *,
    total: int,
    page: int,
    per_page: int,
) -> tuple[dict[str, Any], int]:
    """Build the standard paginated response envelope.

    Args:
        items: Serialized item list.
        total: Total row count across all pages.
        page: 1-indexed page number.
        per_page: Page size.

    Returns:
        Tuple of JSON body and HTTP 200.
    """
    return {
        "data": items,
        "meta": {
            "total": total,
            "page": page,
            "per_page": per_page,
            "has_next": (page * per_page) < total,
        },
    }, 200

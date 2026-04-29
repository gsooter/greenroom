"""Community map recommendation route handlers.

Thin endpoints that validate path and body input and hand off to
:mod:`backend.services.map_recommendations`. The module owns the
concerns the service layer cannot see: the Flask request/response
bridge, the per-IP rate-limit decorator on the submit endpoint, and
the "optional auth" dance so signed-out callers can still load
recommendations and vote / submit as guests.

All routes return the standard ``{"data": ..., "meta": ...}`` / error
envelope defined in ``CLAUDE.md``.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from flask import g, request

from backend.api.v1 import api_v1
from backend.core.auth import get_current_user, require_auth
from backend.core.database import get_db
from backend.core.exceptions import ValidationError
from backend.core.knuckles import verify_knuckles_token
from backend.core.logging import get_logger
from backend.core.rate_limit import get_request_ip, rate_limit
from backend.data.repositories import users as users_repo
from backend.services import map_recommendations as service

if TYPE_CHECKING:
    from backend.data.models.users import User

logger = get_logger(__name__)

_MAX_SESSION_ID_LEN = 64
_DEFAULT_LIST_LIMIT = 100
_MAX_LIST_LIMIT = 200


@api_v1.route("/maps/recommendations", methods=["GET"])
def list_recommendations() -> tuple[dict[str, Any], int]:
    """List community recommendations inside a lat/lng bounding box.

    Query parameters:
        sw_lat, sw_lng, ne_lat, ne_lng: WGS-84 bounding box corners.
            All four are required.
        category: Optional :class:`MapRecommendationCategory` filter.
        sort: ``"top"`` (default) or ``"new"``.
        limit: Max rows, clamped to :data:`_MAX_LIST_LIMIT`.
        session_id: Guest session id used to populate ``viewer_vote``
            when the caller isn't signed in.

    Returns:
        Tuple of JSON response body and HTTP 200 status code.

    Raises:
        ValidationError: If any bbox corner is missing or unparseable,
            or if ``category`` / ``limit`` is malformed.
    """
    session = get_db()
    viewer = _maybe_current_user()
    viewer_session_id = _sanitized_session_id(request.args.get("session_id"))
    limit = _parse_limit(request.args.get("limit"))

    recommendations = service.list_recommendations(
        session,
        sw_lat=_required_float("sw_lat"),
        sw_lng=_required_float("sw_lng"),
        ne_lat=_required_float("ne_lat"),
        ne_lng=_required_float("ne_lng"),
        category=request.args.get("category"),
        sort=request.args.get("sort"),
        limit=limit,
        viewer_user_id=viewer.id if viewer is not None else None,
        viewer_session_id=viewer_session_id if viewer is None else None,
    )
    return (
        {"data": recommendations, "meta": {"count": len(recommendations)}},
        200,
    )


@api_v1.route("/maps/recommendations", methods=["POST"])
@rate_limit("map_recommendation_submit_ip", limit=10, window_seconds=3600)
def submit_recommendation() -> tuple[dict[str, Any], int]:
    """Create a new community recommendation after verifying the place.

    Request body:
        ``{"query": str, "by": "name" | "address", "lat"?: float,
        "lng"?: float, "venue_id"?: str, "category": str, "body": str,
        "honeypot"?: str, "session_id"?: str}``. ``lat`` and ``lng`` are
        only required when ``by == "name"`` and ``venue_id`` is absent.
        When ``venue_id`` is present the venue's own coords become the
        anchor and the verified place must sit within 1000 m of the
        venue. ``session_id`` is only read when the caller is signed
        out. ``honeypot`` must be empty or absent.

    Returns:
        Tuple of JSON response body and HTTP 201 status code.

    Raises:
        UnauthorizedError: If neither auth nor session_id is supplied.
        ValidationError: For any honeypot / input / spam-gate failure.
        AppError: ``PLACE_VERIFICATION_FAILED`` (422) when Apple cannot
            match the query or the venue guardrail is violated;
            ``APPLE_MAPS_UNAVAILABLE`` propagated.
        NotFoundError: ``VENUE_NOT_FOUND`` when ``venue_id`` is set but
            no venue exists with that id.
        RateLimitExceededError: If the caller exceeds the per-IP cap.
    """
    session = get_db()
    viewer = _maybe_current_user()
    payload = _require_json_object()
    session_id = (
        None if viewer is not None else _sanitized_session_id(payload.get("session_id"))
    )

    raw_venue_id = payload.get("venue_id")
    venue_id = (
        _parse_uuid(raw_venue_id, field="venue_id")
        if isinstance(raw_venue_id, str) and raw_venue_id.strip()
        else None
    )

    recommendation = service.submit_recommendation(
        session,
        user=viewer,
        session_id=session_id,
        query=_require_string(payload, "query"),
        by=_require_string(payload, "by"),
        near_latitude=_optional_float(payload.get("lat")),
        near_longitude=_optional_float(payload.get("lng")),
        venue_id=venue_id,
        category=_require_string(payload, "category"),
        body=_require_string(payload, "body"),
        honeypot=payload.get("honeypot"),
        ip_hash=service.hash_request_ip(get_request_ip()),
    )
    return {"data": recommendation}, 201


@api_v1.route("/maps/recommendations/<recommendation_id>/vote", methods=["POST"])
def vote_on_recommendation(
    recommendation_id: str,
) -> tuple[dict[str, Any], int]:
    """Cast, change, or clear a vote on a recommendation.

    Request body:
        ``{"value": int, "session_id"?: str}``. ``value`` must be
        ``-1``, ``0`` (clear), or ``+1``. ``session_id`` is required
        when the caller is signed out so guest votes can be deduped.

    Args:
        recommendation_id: UUID string of the recommendation.

    Returns:
        Tuple of JSON response body and HTTP 200. Body shape:
        ``{"likes": int, "dislikes": int, "viewer_vote": int | None,
        "suppressed": bool}``.

    Raises:
        UnauthorizedError: If neither auth nor session_id is provided.
        NotFoundError: If the recommendation doesn't exist.
        ValidationError: If ``value`` or ``recommendation_id`` is
            malformed.
    """
    session = get_db()
    parsed_id = _parse_uuid(recommendation_id, field="recommendation_id")
    viewer = _maybe_current_user()
    payload = _require_json_object()
    value = _parse_vote_value(payload.get("value"))
    raw_session_id = (
        None if viewer is not None else _sanitized_session_id(payload.get("session_id"))
    )

    result = service.cast_vote(
        session,
        recommendation_id=parsed_id,
        value=value,
        user=viewer,
        session_id=raw_session_id,
    )
    return {"data": result}, 200


@api_v1.route("/maps/recommendations/<recommendation_id>", methods=["DELETE"])
@require_auth
def delete_recommendation(
    recommendation_id: str,
) -> tuple[dict[str, Any], int]:
    """Delete one of the caller's own recommendations.

    Args:
        recommendation_id: UUID string of the recommendation.

    Returns:
        Tuple of empty body and HTTP 204.

    Raises:
        UnauthorizedError: If the caller is not signed in.
        NotFoundError: If the recommendation doesn't exist.
        ForbiddenError: If the caller is not the author.
        ValidationError: If ``recommendation_id`` is not a valid UUID.
    """
    session = get_db()
    parsed_id = _parse_uuid(recommendation_id, field="recommendation_id")
    service.delete_recommendation(
        session,
        recommendation_id=parsed_id,
        user=get_current_user(),
    )
    return {}, 204


def _maybe_current_user() -> User | None:
    """Return the authenticated user if a valid bearer token is present.

    Unlike :func:`backend.core.auth.require_auth`, this helper does
    not raise when authentication is missing or fails — it just
    returns ``None`` so the caller can fall back to guest behavior.

    Returns:
        The :class:`User` on a successful verification, else ``None``.
    """
    header = request.headers.get("Authorization", "")
    if not header.lower().startswith("bearer "):
        return None
    token = header[len("bearer ") :].strip()
    if not token:
        return None
    try:
        claims = verify_knuckles_token(token)
        sub = claims.get("sub")
        if not isinstance(sub, str):
            return None
        user_id = uuid.UUID(sub)
    except Exception:
        logger.debug("map_recommendation_optional_auth_rejected", exc_info=True)
        return None

    session = get_db()
    user = users_repo.get_user_by_id(session, user_id)
    if user is None or not user.is_active:
        return None
    g.current_user = user
    return user


def _require_json_object() -> dict[str, Any]:
    """Pull a JSON object body off the current request.

    Returns:
        The decoded dict.

    Raises:
        ValidationError: If the body is missing or not a JSON object.
    """
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ValidationError("Request body must be a JSON object.")
    return payload


def _require_string(payload: dict[str, Any], key: str) -> str:
    """Extract a required non-empty string field from a payload.

    Args:
        payload: Request body dict.
        key: Name of the expected field.

    Returns:
        The field value.

    Raises:
        ValidationError: If missing or not a non-empty string.
    """
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"Missing '{key}' field.")
    return value


def _parse_uuid(value: str, *, field: str) -> uuid.UUID:
    """Parse a path segment as a UUID, raising a 422 if malformed.

    Args:
        value: The raw string from the URL.
        field: Name of the field, used in the error message.

    Returns:
        The parsed UUID.

    Raises:
        ValidationError: If ``value`` is not a valid UUID.
    """
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise ValidationError(f"{field} is not a valid UUID: '{value}'.") from exc


def _parse_limit(raw: str | None) -> int:
    """Parse and clamp the ``limit`` query parameter.

    Args:
        raw: Raw query-string value, or None.

    Returns:
        An integer in ``[1, _MAX_LIST_LIMIT]``, defaulting to
        :data:`_DEFAULT_LIST_LIMIT` when absent.

    Raises:
        ValidationError: If ``raw`` is present but not a positive int.
    """
    if raw is None or raw == "":
        return _DEFAULT_LIST_LIMIT
    try:
        parsed = int(raw)
    except ValueError as exc:
        raise ValidationError("limit must be an integer.") from exc
    if parsed < 1:
        raise ValidationError("limit must be positive.")
    return min(parsed, _MAX_LIST_LIMIT)


def _parse_vote_value(raw: Any) -> int:
    """Parse the ``value`` field on a vote payload.

    Args:
        raw: The JSON-decoded value. Booleans are rejected outright
            because ``True`` passes ``isinstance(..., int)``.

    Returns:
        An int in ``{-1, 0, 1}``.

    Raises:
        ValidationError: If missing or not one of the three legal ints.
    """
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValidationError("value must be -1, 0, or +1.")
    if raw not in (-1, 0, 1):
        raise ValidationError("value must be -1, 0, or +1.")
    return raw


def _sanitized_session_id(raw: Any) -> str | None:
    """Validate and return a non-empty guest session id string.

    Args:
        raw: The raw value as pulled from the query string or payload.

    Returns:
        A stripped non-empty string of at most
        :data:`_MAX_SESSION_ID_LEN` chars, or ``None`` if missing or
        malformed.
    """
    if not isinstance(raw, str):
        return None
    trimmed = raw.strip()
    if not trimmed or len(trimmed) > _MAX_SESSION_ID_LEN:
        return None
    return trimmed


def _required_float(name: str) -> float:
    """Parse a required float query arg or raise ``VALIDATION_ERROR``.

    Args:
        name: Query parameter name.

    Returns:
        The parsed float.

    Raises:
        ValidationError: When the parameter is missing, blank, or not a
            valid float.
    """
    raw = request.args.get(name)
    if raw is None or raw == "":
        raise ValidationError(f"`{name}` is required.")
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"`{name}` must be a number.") from exc


def _optional_float(raw: Any) -> float | None:
    """Coerce a payload field to a float, returning ``None`` if absent.

    Args:
        raw: The raw JSON value (number, string, or None).

    Returns:
        ``float(raw)`` when parseable, else ``None``.

    Raises:
        ValidationError: If the value is present but not numeric.
    """
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool):
        raise ValidationError("lat/lng must be numbers.")
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise ValidationError("lat/lng must be numbers.") from exc


__all__ = [
    "delete_recommendation",
    "list_recommendations",
    "submit_recommendation",
    "vote_on_recommendation",
]

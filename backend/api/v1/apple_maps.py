"""Apple Maps route handlers.

Thin endpoints that delegate to :mod:`backend.services.apple_maps`:

* ``GET /maps/token`` — mints a MapKit JS developer token.
* ``GET /venues/<slug>/map-snapshot`` — returns a signed static-image
  URL for the venue, cached for 24 hours.
* ``GET /venues/<slug>/nearby`` — Apple Maps Server API ``searchNearby``
  results within a 400 m radius, cached for 7 days.
"""

from __future__ import annotations

from typing import Any

from flask import request

from backend.api.v1 import api_v1
from backend.core.database import get_db
from backend.core.exceptions import VENUE_NOT_FOUND, AppError
from backend.core.rate_limit import rate_limit
from backend.data.repositories import venues as venues_repo
from backend.services import apple_maps as service


@api_v1.route("/maps/token", methods=["GET"])
@rate_limit("maps_token_ip", limit=60, window_seconds=60)
def mapkit_token() -> tuple[dict[str, Any], int]:
    """Return a cached or freshly-minted MapKit JS developer token.

    Query parameters:
        origin: string — the fully-qualified origin loading MapKit JS
            (e.g. ``"https://www.greenroom.fm"``). Bound into the token
            claim so a leaked token can't be reused on a different site.
            Omit to mint an unbound token.

    Returns:
        Tuple of JSON response body and HTTP 200 status code. Body
        shape: ``{"data": {"token": str, "expires_at": int}}``.

    Raises:
        AppError: ``APPLE_MAPS_UNAVAILABLE`` if the environment is
            missing any Apple Maps credential.
        RateLimitExceededError: If the caller exceeds the per-IP cap.
    """
    origin = request.args.get("origin") or None
    payload = service.mint_mapkit_token(origin=origin)
    return {"data": payload}, 200


@api_v1.route("/venues/<slug>/map-snapshot", methods=["GET"])
@rate_limit("maps_snapshot_ip", limit=120, window_seconds=60)
def venue_map_snapshot(slug: str) -> tuple[dict[str, Any], int]:
    """Return a signed Apple Maps static-image URL for a venue.

    The URL is cached in Redis for 24 hours so busy venue pages don't
    re-sign every request. The browser fetches the PNG directly —
    Apple's CDN serves it without any additional auth header.

    URL parameters:
        slug: Venue slug (e.g. ``"black-cat"``).

    Query parameters:
        width: Image width in CSS pixels. Clamped to 1-640. Default 600.
        height: Image height in CSS pixels. Clamped to 1-640. Default 400.
        zoom: Apple zoom level. Default 15.
        scheme: ``"light"`` or ``"dark"``. Default ``"light"``.
        label: Optional pin glyph (2 chars max). Default is no glyph —
            Apple renders a plain red balloon pin, matching the native
            Apple Maps look. Pass an explicit value to override.

    Returns:
        Tuple of JSON body and HTTP 200. Body shape:
        ``{"data": {"url": str, "width": int, "height": int}}``.

    Raises:
        AppError: ``VENUE_NOT_FOUND`` (404) if the slug doesn't exist
            or the venue has no geocoded coordinates.
        AppError: ``APPLE_MAPS_UNAVAILABLE`` (503) if credentials are
            not configured on this environment.
        RateLimitExceededError: If the caller exceeds the per-IP cap.
    """
    session = get_db()
    venue = venues_repo.get_venue_by_slug(session, slug)
    if venue is None or venue.latitude is None or venue.longitude is None:
        raise AppError(
            code=VENUE_NOT_FOUND,
            message=f"No mappable venue with slug '{slug}'.",
            status_code=404,
        )

    width = _int_arg("width", default=600)
    height = _int_arg("height", default=400)
    zoom = _float_arg("zoom", default=15.0)
    scheme = (request.args.get("scheme") or "light").strip().lower()
    label = (request.args.get("label") or "").strip() or None

    url = service.build_snapshot_url(
        latitude=venue.latitude,
        longitude=venue.longitude,
        zoom=zoom,
        width=width,
        height=height,
        color_scheme=scheme,
        annotation_label=label,
    )
    return {"data": {"url": url, "width": width, "height": height}}, 200


@api_v1.route("/venues/<slug>/nearby", methods=["GET"])
@rate_limit("maps_nearby_ip", limit=60, window_seconds=60)
def venue_nearby(slug: str) -> tuple[dict[str, Any], int]:
    """Return restaurants, bars, and cafes within 400 m of a venue.

    Delegates to :func:`backend.services.apple_maps.fetch_nearby_poi`,
    which hits Apple's ``/v1/searchNearby`` endpoint and caches the
    normalized result list in Redis for 7 days.

    URL parameters:
        slug: Venue slug.

    Query parameters:
        categories: Comma-separated Apple POI categories. Defaults to
            ``"Restaurant,Bar,Cafe"``. Unknown categories pass through
            to Apple unchanged.
        limit: Max POIs to return (after distance sort). Defaults to 12.

    Returns:
        Tuple of JSON body and HTTP 200 status code. Body shape:
        ``{"data": [{"name": str, "category": str, "address": str|None,
        "latitude": float, "longitude": float, "distance_m": int}, ...]}``.

    Raises:
        AppError: ``VENUE_NOT_FOUND`` (404) if the slug doesn't exist
            or the venue has no geocoded coordinates.
        AppError: ``APPLE_MAPS_UNAVAILABLE`` (503) when credentials are
            not configured; (502) when Apple's API returns an error.
        RateLimitExceededError: If the caller exceeds the per-IP cap.
    """
    session = get_db()
    venue = venues_repo.get_venue_by_slug(session, slug)
    if venue is None or venue.latitude is None or venue.longitude is None:
        raise AppError(
            code=VENUE_NOT_FOUND,
            message=f"No mappable venue with slug '{slug}'.",
            status_code=404,
        )

    categories = _categories_arg()
    limit = _int_arg("limit", default=12)
    results = service.fetch_nearby_poi(
        latitude=venue.latitude,
        longitude=venue.longitude,
        categories=categories,
        limit=limit,
    )
    return {"data": results, "meta": {"count": len(results)}}, 200


def _categories_arg() -> tuple[str, ...]:
    """Parse the ``categories`` query arg into a tuple of Apple POI names.

    Returns:
        A non-empty tuple. When the arg is missing or empty, defaults
        to ``("Restaurant", "Bar", "Cafe")``.
    """
    raw = request.args.get("categories")
    if not raw:
        return ("Restaurant", "Bar", "Cafe")
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return tuple(parts) if parts else ("Restaurant", "Bar", "Cafe")


def _int_arg(name: str, *, default: int) -> int:
    """Parse an integer query arg, falling back to ``default``.

    Args:
        name: Query parameter name.
        default: Value to return when the arg is missing or unparseable.

    Returns:
        The parsed int, or ``default`` on any failure. Invalid values
        intentionally degrade silently — the service layer clamps to
        its own safe bounds.
    """
    raw = request.args.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _float_arg(name: str, *, default: float) -> float:
    """Parse a float query arg, falling back to ``default``.

    Args:
        name: Query parameter name.
        default: Value to return when the arg is missing or unparseable.

    Returns:
        The parsed float, or ``default`` on any failure.
    """
    raw = request.args.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default

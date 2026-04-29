"""Map-discovery route handlers — non-venue-scoped endpoints.

The venue-scoped Apple Maps surface (``/maps/token``,
``/venues/<slug>/map-snapshot``, ``/venues/<slug>/nearby``) lives in
:mod:`backend.api.v1.apple_maps`. This module hosts the lookups that
power map-side flows where the input is a free-text query or an
arbitrary lat/lng:

* ``GET /maps/places/nearby`` — POI search around a coordinate.
  Used by the community recommendation form's "what place are you
  recommending?" autocomplete.
* ``GET /maps/places/verify`` — geocode-and-similarity gate that
  every community recommendation must clear before it can be saved.
  Returns 404 ``PLACE_NOT_VERIFIED`` when Apple has no match or the
  similarity floor rejects the only candidate.

Both endpoints serialize :class:`backend.services.apple_maps.NearbyPlace`
and :class:`backend.services.apple_maps.VerifiedPlace` directly into
the JSON envelope so the frontend gets a stable typed payload.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from flask import request

from backend.api.v1 import api_v1
from backend.core.auth import get_current_user, require_auth
from backend.core.database import get_db
from backend.core.exceptions import PLACE_NOT_VERIFIED, AppError
from backend.core.rate_limit import rate_limit
from backend.services import apple_maps as service
from backend.services import events as events_service


def _user_key() -> str:
    """Resolve the current user id as a Redis rate-limit subject.

    Used by authed endpoints that want to cap per-user rather than
    per-IP. Relies on :func:`require_auth` having already populated
    ``g.current_user`` — the rate-limit decorator runs *after*
    ``require_auth``.

    Returns:
        The user id as a string, suitable for embedding in a Redis key.
    """
    user = get_current_user()
    return str(user.id)


@api_v1.route("/maps/places/search", methods=["GET"])
@require_auth
@rate_limit(
    "maps_places_search_user",
    limit=20,
    window_seconds=60,
    key_fn=_user_key,
)
def search_places() -> tuple[dict[str, Any], int]:
    """Authed autocomplete for the "Leave a tip" place picker.

    Wraps :func:`backend.services.apple_maps.search_nearby_places` and
    returns the top matches as a ranked list. Auth is required and the
    limiter is keyed per-user (not per-IP), so the heavier Apple Maps
    calls can't be pounded by anonymous traffic.

    Query parameters:
        lat: WGS-84 latitude. Required.
        lng: WGS-84 longitude. Required.
        q: Optional free-text substring. When provided the results are
            post-filtered to names/categories that contain the query.
        categories: Comma-separated Apple POI categories. Defaults to
            ``"Restaurant,Bar,Cafe"`` to match the "food & drinks"
            framing of the tips feature.
        radius_m: Hard distance cap in metres. Default 1000 (matches
            the venue guardrail), clamped to ``[50, 5000]``.
        limit: Max results returned. Default 8, clamped to ``[1, 20]``.

    Returns:
        Tuple of JSON body and HTTP 200. Body shape:
        ``{"data": [NearbyPlace, ...], "meta": {"count": int}}``.

    Raises:
        AppError: ``INVALID_REQUEST`` (400) for malformed coords;
            ``APPLE_MAPS_UNAVAILABLE`` propagated.
        UnauthorizedError: When the bearer token is missing or invalid.
        RateLimitExceededError: When the per-user cap is exceeded.
    """
    latitude = _required_float("lat")
    longitude = _required_float("lng")
    categories = _parse_categories(default=("Restaurant", "Bar", "Cafe"))
    radius_m = _clamped_int("radius_m", default=1000, low=50, high=5000)
    limit = _clamped_int("limit", default=8, low=1, high=20)
    query = (request.args.get("q") or "").strip()

    places = service.search_nearby_places(
        latitude=latitude,
        longitude=longitude,
        categories=categories,
        radius_m=radius_m,
        limit=limit,
        query=query or None,
    )
    return (
        {
            "data": [asdict(place) for place in places],
            "meta": {"count": len(places)},
        },
        200,
    )


@api_v1.route("/maps/places/nearby", methods=["GET"])
@rate_limit("maps_places_nearby_ip", limit=60, window_seconds=60)
def nearby_places() -> tuple[dict[str, Any], int]:
    """Return Apple Maps POIs near a lat/lng.

    Query parameters:
        lat: WGS-84 latitude. Required.
        lng: WGS-84 longitude. Required.
        categories: Comma-separated Apple POI categories. Defaults to
            ``"Restaurant,Bar,Cafe"``.
        radius_m: Hard distance cap in meters. Default 400, clamped to
            [50, 5000].
        limit: Max results returned after distance sort. Default 12,
            clamped to [1, 30].

    Returns:
        Tuple of JSON body and HTTP 200. Body shape:
        ``{"data": [NearbyPlace, ...], "meta": {"count": int}}``.

    Raises:
        AppError: ``INVALID_REQUEST`` (400) when lat/lng can't be
            parsed; ``APPLE_MAPS_UNAVAILABLE`` (503/502) propagated
            from the service layer.
        RateLimitExceededError: When the per-IP cap is exceeded.
    """
    latitude = _required_float("lat")
    longitude = _required_float("lng")
    categories = _parse_categories(default=("Restaurant", "Bar", "Cafe"))
    radius_m = _clamped_int("radius_m", default=400, low=50, high=5000)
    limit = _clamped_int("limit", default=12, low=1, high=30)

    places = service.search_nearby_places(
        latitude=latitude,
        longitude=longitude,
        categories=categories,
        radius_m=radius_m,
        limit=limit,
    )
    return (
        {
            "data": [asdict(place) for place in places],
            "meta": {"count": len(places)},
        },
        200,
    )


@api_v1.route("/maps/places/verify", methods=["GET"])
@rate_limit("maps_places_verify_ip", limit=30, window_seconds=60)
def verify_place() -> tuple[dict[str, Any], int]:
    """Round-trip a user-typed query through Apple's geocoder.

    Used by the community recommendation form to gate every submission:
    a recommendation cannot be saved until its place name (or address)
    matches a real Apple Maps place above the 0.80 similarity floor.

    Query parameters:
        by: ``"name"`` or ``"address"``. Selects which verifier runs
            and which fields are required.
        q: User-supplied query string. Required, non-empty after trim.
        lat: Search-anchor latitude. Required when ``by=name``.
        lng: Search-anchor longitude. Required when ``by=name``.

    Returns:
        Tuple of JSON body and HTTP 200 with a serialized
        :class:`backend.services.apple_maps.VerifiedPlace`.

    Raises:
        AppError: ``INVALID_REQUEST`` (400) for any malformed input;
            ``PLACE_NOT_VERIFIED`` (404) when Apple has no match or the
            similarity gate rejects the candidate;
            ``APPLE_MAPS_UNAVAILABLE`` (503/502) propagated from the
            service layer.
        RateLimitExceededError: When the per-IP cap is exceeded. The
            verify endpoint is the spam-prone one (free-text input,
            real cost per call) so its cap is half the nearby cap.
    """
    by = (request.args.get("by") or "").strip().lower()
    if by not in {"name", "address"}:
        raise AppError(
            code="INVALID_REQUEST",
            message="`by` must be 'name' or 'address'.",
            status_code=400,
        )
    query = (request.args.get("q") or "").strip()
    if not query:
        raise AppError(
            code="INVALID_REQUEST",
            message="`q` is required and must not be blank.",
            status_code=400,
        )

    if by == "name":
        latitude = _required_float("lat")
        longitude = _required_float("lng")
        place = service.verify_place_by_name(
            query=query,
            near_latitude=latitude,
            near_longitude=longitude,
        )
    else:
        place = service.verify_place_by_address(query=query)

    if place is None:
        raise AppError(
            code=PLACE_NOT_VERIFIED,
            message="Apple Maps did not return a confident match.",
            status_code=404,
        )
    return {"data": asdict(place)}, 200


@api_v1.route("/maps/near-me", methods=["GET"])
def near_me_events() -> tuple[dict[str, Any], int]:
    """Return upcoming DMV events within a radius of a lat/lng.

    Powers the "Shows Near Me" surface. The user's browser supplies
    the lat/lng via the geolocation API; this endpoint fetches the
    matching time window from the event repo, filters to venues within
    ``radius_km`` using an in-process haversine, and returns them
    sorted nearest-first.

    Query parameters:
        lat: WGS-84 latitude of the user's current location. Required.
        lng: WGS-84 longitude of the user's current location. Required.
        radius_km: Maximum great-circle distance to include, in km.
            Default 10, clamped to ``[0.5, 100]``.
        window: ``"tonight"`` (today only, ET) or ``"week"`` (next 7
            days in ET). Default ``"tonight"``.
        limit: Maximum rows returned after distance sort. Default 50,
            clamped to ``[1, 100]``.

    Returns:
        Tuple of JSON body and HTTP 200. See
        :func:`backend.services.events.list_events_near` for the row
        shape and meta contents.

    Raises:
        AppError: ``INVALID_REQUEST`` (400) for missing/malformed coords.
        ValidationError: (422) for unsupported ``window`` values —
            propagated from the service layer.
    """
    session = get_db()
    latitude = _required_float("lat")
    longitude = _required_float("lng")
    radius_km = _clamped_float("radius_km", default=10.0, low=0.5, high=100.0)
    limit = _clamped_int("limit", default=50, low=1, high=100)
    window = (request.args.get("window") or "tonight").strip().lower()

    envelope = events_service.list_events_near(
        session,
        latitude=latitude,
        longitude=longitude,
        radius_km=radius_km,
        window=window,  # type: ignore[arg-type]
        limit=limit,
    )
    return envelope, 200


@api_v1.route("/maps/tonight", methods=["GET"])
def tonight_map() -> tuple[dict[str, Any], int]:
    """Return today's pinnable DMV events for the Tonight map surface.

    Query parameters:
        genres: Optional comma-separated genre list used to filter the
            feed. Genre matching is an overlap check in the repo — an
            event matches when any of its genres appears in the filter.

    Returns:
        Tuple of the standard envelope and HTTP 200. See
        :func:`backend.services.events.list_tonight_map_events` for
        the row shape.
    """
    session = get_db()
    genres = _parse_genres_filter(request.args.get("genres"))
    envelope = events_service.list_tonight_map_events(session, genres=genres)
    return envelope, 200


def _parse_genres_filter(raw: str | None) -> list[str] | None:
    """Split a comma-separated genres query arg into a non-empty list.

    Args:
        raw: Raw query-string value, e.g. ``"indie,punk"``, or None.

    Returns:
        A de-duplicated list of trimmed genre strings, or ``None`` when
        the parameter is missing or contains only whitespace. Returning
        ``None`` (rather than ``[]``) keeps the repo's "no filter"
        behavior distinct from a legit empty filter.
    """
    if raw is None or raw.strip() == "":
        return None
    seen: set[str] = set()
    result: list[str] = []
    for part in raw.split(","):
        trimmed = part.strip()
        if not trimmed or trimmed in seen:
            continue
        seen.add(trimmed)
        result.append(trimmed)
    return result or None


def _required_float(name: str) -> float:
    """Parse a required float query arg or raise ``INVALID_REQUEST``.

    Args:
        name: Query parameter name.

    Returns:
        The parsed float.

    Raises:
        AppError: ``INVALID_REQUEST`` (400) when the parameter is
            missing, blank, or not a valid float.
    """
    raw = request.args.get(name)
    if raw is None or raw == "":
        raise AppError(
            code="INVALID_REQUEST",
            message=f"`{name}` is required.",
            status_code=400,
        )
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise AppError(
            code="INVALID_REQUEST",
            message=f"`{name}` must be a number.",
            status_code=400,
        ) from exc


def _clamped_int(name: str, *, default: int, low: int, high: int) -> int:
    """Parse an int query arg, falling back to ``default`` and clamping.

    Args:
        name: Query parameter name.
        default: Value to use when the arg is missing or unparseable.
        low: Inclusive lower bound after parsing.
        high: Inclusive upper bound after parsing.

    Returns:
        The parsed int, clamped to ``[low, high]``.
    """
    raw = request.args.get(name)
    if raw is None or raw == "":
        value = default
    else:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = default
    return max(low, min(high, value))


def _clamped_float(name: str, *, default: float, low: float, high: float) -> float:
    """Parse a float query arg, falling back to ``default`` and clamping.

    Args:
        name: Query parameter name.
        default: Value to use when the arg is missing or unparseable.
        low: Inclusive lower bound after parsing.
        high: Inclusive upper bound after parsing.

    Returns:
        The parsed float, clamped to ``[low, high]``.
    """
    raw = request.args.get(name)
    if raw is None or raw == "":
        value = default
    else:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = default
    return max(low, min(high, value))


def _parse_categories(*, default: tuple[str, ...]) -> tuple[str, ...]:
    """Parse the ``categories`` query arg into a tuple of Apple POI names.

    Args:
        default: Tuple to return when the arg is missing or empty.

    Returns:
        A non-empty tuple of category strings.
    """
    raw = request.args.get("categories")
    if not raw:
        return default
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return tuple(parts) if parts else default

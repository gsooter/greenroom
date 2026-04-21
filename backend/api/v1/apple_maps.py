"""Apple Maps route handlers.

Thin endpoints that delegate to :mod:`backend.services.apple_maps`. The
only route today is ``GET /maps/token`` which mints a short-lived
MapKit JS developer token. Subsequent commits add ``/maps/snapshot``
and ``/maps/nearby``.
"""

from __future__ import annotations

from typing import Any

from flask import request

from backend.api.v1 import api_v1
from backend.core.rate_limit import rate_limit
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

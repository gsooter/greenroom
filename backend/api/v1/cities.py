"""City route handlers.

Thin handlers that validate input and delegate to the cities service.
No business logic lives here.
"""

from flask import request

from backend.api.v1 import api_v1
from backend.core.database import get_db
from backend.services import cities as cities_service


@api_v1.route("/cities", methods=["GET"])
def list_cities() -> tuple[dict, int]:
    """List active cities, optionally filtered by region.

    Query parameters:
        region: string — filter to cities in a region (e.g., "DMV").

    Returns:
        Tuple of JSON response body and HTTP 200 status code.
    """
    session = get_db()
    region = request.args.get("region")

    cities = cities_service.list_cities(session, region=region)

    return {
        "data": [cities_service.serialize_city(c) for c in cities],
        "meta": {"total": len(cities)},
    }, 200


@api_v1.route("/cities/<slug>", methods=["GET"])
def get_city(slug: str) -> tuple[dict, int]:
    """Fetch a single city by slug.

    Args:
        slug: URL-safe slug identifier.

    Returns:
        Tuple of JSON response body and HTTP 200 status code.
    """
    session = get_db()
    city = cities_service.get_city_by_slug(session, slug)
    return {"data": cities_service.serialize_city(city)}, 200

"""JSON-LD helpers that turn an :class:`Event` into Gmail/Apple cards.

Mailbox providers render rich actionable cards when a transactional
email embeds a Schema.org JSON-LD blob describing the underlying
entity. For us that entity is a ``MusicEvent``: when present,
recipients see the event title, date, venue, and a "View show" CTA
in their inbox preview without opening the email.

The output of :func:`event_to_jsonld` is a plain dict — the renderer
embeds it as ``<script type="application/ld+json">`` inside the email
``<head>``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from backend.data.models.events import Event


# Mapping from our internal status enum values to the Schema.org
# EventStatusType IRIs that mailbox providers recognise.
_STATUS_MAP: dict[str, str] = {
    "confirmed": "https://schema.org/EventScheduled",
    "sold_out": "https://schema.org/EventScheduled",
    "cancelled": "https://schema.org/EventCancelled",
    "postponed": "https://schema.org/EventPostponed",
    "past": "https://schema.org/EventScheduled",
}


def event_to_jsonld(event: Event, *, event_url: str) -> dict[str, Any]:
    """Build a Schema.org ``MusicEvent`` JSON-LD blob for ``event``.

    Args:
        event: The :class:`Event` to describe.
        event_url: Public URL of the event detail page (used as the
            JSON-LD ``url`` field and as the unauthenticated ``Offer``
            fallback when the event has no direct ticket link).

    Returns:
        A plain dict ready to be JSON-encoded into a
        ``<script type="application/ld+json">`` block.
    """
    blob: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "MusicEvent",
        "name": event.title,
        "url": event_url,
        "startDate": event.starts_at.isoformat(),
        "eventStatus": _status_url(event.status),
        "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
        "location": _venue_to_place(event.venue),
    }

    if event.ends_at is not None:
        blob["endDate"] = event.ends_at.isoformat()

    if event.doors_at is not None:
        blob["doorTime"] = event.doors_at.isoformat()

    if event.image_url:
        blob["image"] = event.image_url

    artists = event.artists or []
    if artists:
        blob["performer"] = [
            {"@type": "MusicGroup", "name": artist} for artist in artists
        ]

    offer = _build_offer(event, event_url=event_url)
    if offer is not None:
        blob["offers"] = offer

    return blob


def _venue_to_place(venue: Any) -> dict[str, Any]:
    """Build a ``MusicVenue`` location object for a venue.

    Args:
        venue: The venue ORM row.

    Returns:
        A Schema.org ``MusicVenue`` dict with an embedded
        ``PostalAddress``.
    """
    place: dict[str, Any] = {
        "@type": "MusicVenue",
        "name": venue.name,
    }
    if venue.address:
        place["address"] = {
            "@type": "PostalAddress",
            "streetAddress": venue.address,
        }
    if venue.latitude is not None and venue.longitude is not None:
        place["geo"] = {
            "@type": "GeoCoordinates",
            "latitude": venue.latitude,
            "longitude": venue.longitude,
        }
    return place


def _build_offer(event: Event, *, event_url: str) -> dict[str, Any] | None:
    """Build a single ``Offer`` block for the event, if it's priced.

    Args:
        event: The :class:`Event` whose pricing we surface.
        event_url: Fallback URL when the event has no direct
            ``ticket_url`` to link to.

    Returns:
        A Schema.org ``Offer`` dict, or ``None`` when the event has
        no price data attached. Mailbox providers tolerate missing
        offers cleanly — they just don't render a price line.
    """
    if event.min_price is None:
        return None
    return {
        "@type": "Offer",
        "price": f"{event.min_price:.2f}",
        "priceCurrency": "USD",
        "url": event.ticket_url or event_url,
        "availability": "https://schema.org/InStock",
    }


def _status_url(status: Any) -> str:
    """Map our internal status enum to a Schema.org EventStatusType IRI.

    Args:
        status: An :class:`EventStatus` enum (or a stand-in with a
            ``.value`` string) describing where the event is in its
            lifecycle.

    Returns:
        A Schema.org IRI; defaults to ``EventScheduled`` when the
        status doesn't have a more specific mapping.
    """
    raw = getattr(status, "value", None) or str(status)
    return _STATUS_MAP.get(raw, "https://schema.org/EventScheduled")

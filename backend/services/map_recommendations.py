"""Community map recommendation business logic.

The API layer calls these functions and never touches
:mod:`backend.data.repositories.map_recommendations` directly. This
module owns:

* Place verification — every submission is routed through
  :mod:`backend.services.apple_maps` to anchor the user's free-text
  query to a real Apple-verified place. Submissions that fail
  verification never reach the database.
* Input normalization — trimming, length caps, category parsing, and
  coercion of the submitter identity (user vs guest session).
* Spam gating — honeypot detection, minimum account age for logged-in
  submits, and auto-suppression once a recommendation's net votes
  drop below :data:`AUTO_SUPPRESS_NET_THRESHOLD`.
* Ranking hand-off — exposes the two sort modes the repo supports so
  route handlers can pass a validated string straight through.

Per-IP rate limiting is applied by the API layer's
:func:`backend.core.rate_limit.rate_limit` decorator; the
:func:`hash_request_ip` helper gives both layers a consistent salted
digest so logs and DB rows line up.

All serialization converts UUIDs and enums to strings so the API
layer returns JSON-ready dicts without another transform.
"""

from __future__ import annotations

import hashlib
import math
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from backend.core.config import get_settings
from backend.core.exceptions import (
    PLACE_VERIFICATION_FAILED,
    RECOMMENDATION_NOT_FOUND,
    VENUE_NOT_FOUND,
    AppError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
    ValidationError,
)
from backend.data.models.map_recommendations import MapRecommendationCategory
from backend.data.repositories import map_recommendations as rec_repo
from backend.data.repositories import venues as venues_repo
from backend.services import apple_maps

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.orm import Session

    from backend.data.models.map_recommendations import MapRecommendation
    from backend.data.models.users import User
    from backend.data.models.venues import Venue

VENUE_GUARDRAIL_METERS = 1000.0
"""Maximum distance (metres) between a venue and a tip's verified place.
A submission anchored to a venue whose Apple-verified place sits
farther than this is rejected. Chosen to cover a comfortable walking
radius around a venue without letting submitters pin completely
unrelated parts of the city."""

MAX_BODY_LEN = 2000
MIN_BODY_LEN = 2
MIN_ACCOUNT_AGE = timedelta(minutes=2)
VALID_SORTS = frozenset({"new", "top"})
AUTO_SUPPRESS_NET_THRESHOLD = -5
"""When ``(likes - dislikes) <= AUTO_SUPPRESS_NET_THRESHOLD`` after a
vote lands, the recommendation is auto-suppressed. The threshold is
meant to be insult-proof — a couple of downvotes from a single clique
cannot hide a recommendation on its own."""


def hash_request_ip(raw_ip: str) -> str:
    """Return a stable sha256 digest of an IP salted with the JWT secret.

    The JWT secret is already a per-environment random value and is
    treated as sensitive, so reusing it as the salt keeps us from
    having to manage a second secret just for recommendation rate
    limiting. Rotating the JWT secret naturally invalidates every
    cached hash, which is the right behavior.

    Args:
        raw_ip: Caller's IP as returned by
            :func:`backend.core.rate_limit.get_request_ip`.

    Returns:
        Lowercase hex sha256 string (64 chars).
    """
    salt = get_settings().jwt_secret_key.encode("utf-8")
    return hashlib.sha256(salt + raw_ip.encode("utf-8")).hexdigest()


def _parse_category(raw: str) -> MapRecommendationCategory:
    """Validate and return a :class:`MapRecommendationCategory` from a string.

    Args:
        raw: The ``category`` field from the request payload.

    Returns:
        The matching enum value.

    Raises:
        ValidationError: If ``raw`` isn't a recognized category.
    """
    try:
        return MapRecommendationCategory(raw)
    except ValueError as exc:
        allowed = ", ".join(c.value for c in MapRecommendationCategory)
        raise ValidationError(
            f"Unknown category '{raw}'. Must be one of: {allowed}."
        ) from exc


def _validated_body(raw: str | None) -> str:
    """Trim and length-check a submitted recommendation body.

    Args:
        raw: The raw body string from the request, or None.

    Returns:
        The trimmed body, guaranteed non-empty and within the length
        cap.

    Raises:
        ValidationError: If the body is missing, too short, or too
            long.
    """
    if raw is None:
        raise ValidationError("Missing recommendation body.")
    trimmed = raw.strip()
    if len(trimmed) < MIN_BODY_LEN:
        raise ValidationError("Recommendation is too short.")
    if len(trimmed) > MAX_BODY_LEN:
        raise ValidationError(
            f"Recommendation exceeds the {MAX_BODY_LEN}-character limit."
        )
    return trimmed


def _assert_sort(raw: str | None) -> str:
    """Coerce a query-param sort value into one of the valid modes.

    Args:
        raw: The ``sort`` query parameter, or None.

    Returns:
        ``"top"`` by default, or ``"new"`` when explicitly requested.
    """
    if raw is None:
        return "top"
    sort = raw.lower()
    return sort if sort in VALID_SORTS else "top"


def _as_aware_utc(dt: datetime) -> datetime:
    """Coerce a possibly-naive datetime to aware UTC.

    TimestampMixin columns are stored naive in some older tables;
    normalize on read so age comparisons use a single timezone.

    Args:
        dt: The datetime to coerce.

    Returns:
        A timezone-aware datetime in UTC.
    """
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Return the great-circle distance in metres between two lat/lngs.

    Uses the spherical Earth approximation, which is accurate to a few
    metres over the distances we care about (DMV-scale walks).

    Args:
        lat1: Latitude of the first point in degrees.
        lng1: Longitude of the first point in degrees.
        lat2: Latitude of the second point in degrees.
        lng2: Longitude of the second point in degrees.

    Returns:
        Distance between the two points in metres.
    """
    earth_radius_m = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lam = math.radians(lng2 - lng1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lam / 2) ** 2
    )
    return earth_radius_m * 2 * math.asin(min(1.0, math.sqrt(a)))


def _serialize_recommendation(
    rec: MapRecommendation,
    likes: int,
    dislikes: int,
    viewer_vote: int | None,
    *,
    venue: Venue | None = None,
) -> dict[str, Any]:
    """Produce the JSON-ready dict the frontend renders.

    Args:
        rec: The ORM row.
        likes: Aggregated +1 votes.
        dislikes: Aggregated -1 votes.
        viewer_vote: +1, -1, or None depending on whether the current
            viewer has voted on this recommendation.
        venue: Optional venue to compute ``distance_from_venue_m`` from.
            When the venue has coordinates and the recommendation is
            anchored to it, the distance is included in the response
            so the frontend can show "120 m from venue".

    Returns:
        A plain dict ready to hand to ``jsonify``.
    """
    distance_from_venue_m: int | None = None
    if venue is not None and venue.latitude is not None and venue.longitude is not None:
        distance_from_venue_m = round(
            _haversine_m(
                venue.latitude,
                venue.longitude,
                rec.latitude,
                rec.longitude,
            )
        )
    return {
        "id": str(rec.id),
        "submitter_user_id": (
            str(rec.submitter_user_id) if rec.submitter_user_id else None
        ),
        "venue_id": str(rec.venue_id) if rec.venue_id else None,
        "place_name": rec.place_name,
        "place_address": rec.place_address,
        "latitude": rec.latitude,
        "longitude": rec.longitude,
        "similarity_score": rec.similarity_score,
        "category": (
            rec.category.value
            if isinstance(rec.category, MapRecommendationCategory)
            else rec.category
        ),
        "body": rec.body,
        "likes": likes,
        "dislikes": dislikes,
        "viewer_vote": viewer_vote,
        "suppressed": rec.suppressed_at is not None,
        "distance_from_venue_m": distance_from_venue_m,
        "created_at": rec.created_at.isoformat() if rec.created_at else None,
        "updated_at": rec.updated_at.isoformat() if rec.updated_at else None,
    }


def list_recommendations(
    session: Session,
    *,
    sw_lat: float,
    sw_lng: float,
    ne_lat: float,
    ne_lng: float,
    category: str | None = None,
    sort: str | None = None,
    limit: int = 100,
    viewer_user_id: uuid.UUID | None = None,
    viewer_session_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return serialized recommendations inside a lat/lng bounding box.

    Suppressed rows are excluded from the public feed. Map-surfacing
    threshold (hiding rows with a strongly negative net score) is
    handled by auto-suppression at vote time rather than as a query
    filter, so the feed query stays simple and cache-friendly.

    Args:
        session: Active SQLAlchemy session.
        sw_lat: Southwest corner latitude (inclusive).
        sw_lng: Southwest corner longitude (inclusive).
        ne_lat: Northeast corner latitude (inclusive).
        ne_lng: Northeast corner longitude (inclusive).
        category: Optional category filter string.
        sort: Optional sort mode; defaults to ``"top"``.
        limit: Max rows to return. Repo clamps to 200.
        viewer_user_id: The caller's user id if signed in, used to fill
            out ``viewer_vote`` on each serialized recommendation.
        viewer_session_id: The caller's guest session id, same use.

    Returns:
        A list of serialized recommendation dicts.

    Raises:
        ValidationError: If ``category`` is unrecognized or the
            bounding box is malformed (SW corner >= NE corner).
    """
    if sw_lat > ne_lat or sw_lng > ne_lng:
        raise ValidationError("Bounding box SW corner must be below/left of NE.")

    parsed_category = _parse_category(category) if category else None
    rows = rec_repo.list_recommendations_in_bounds(
        session,
        sw_lat=sw_lat,
        sw_lng=sw_lng,
        ne_lat=ne_lat,
        ne_lng=ne_lng,
        category=parsed_category,
        sort=_assert_sort(sort),
        limit=limit,
    )
    if not rows:
        return []

    viewer_votes = rec_repo.get_voter_values_for_recommendations(
        session,
        [rec.id for rec, _l, _d in rows],
        user_id=viewer_user_id,
        session_id=viewer_session_id,
    )
    return [
        _serialize_recommendation(
            rec,
            likes=likes,
            dislikes=dislikes,
            viewer_vote=viewer_votes.get(rec.id),
        )
        for rec, likes, dislikes in rows
    ]


def list_tips_for_venue(
    session: Session,
    *,
    venue: Venue,
    category: str | None = None,
    limit: int = 100,
    viewer_user_id: uuid.UUID | None = None,
    viewer_session_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return serialized recommendations anchored to a venue.

    Ordering is net votes then recency. Suppressed rows are excluded.
    Each serialized dict carries ``distance_from_venue_m`` so the
    frontend can render "120 m from {venue}".

    Args:
        session: Active SQLAlchemy session.
        venue: The venue whose tips we want. Caller resolves by slug.
        category: Optional category filter string.
        limit: Max rows to return.
        viewer_user_id: Caller's user id, when logged in. Used to
            populate ``viewer_vote`` on each tip.
        viewer_session_id: Caller's guest session id, same use.

    Returns:
        A list of serialized recommendation dicts.

    Raises:
        ValidationError: If ``category`` is unrecognized.
    """
    parsed_category = _parse_category(category) if category else None
    rows = rec_repo.list_recommendations_for_venue(
        session,
        venue_id=venue.id,
        category=parsed_category,
        limit=limit,
    )
    if not rows:
        return []

    viewer_votes = rec_repo.get_voter_values_for_recommendations(
        session,
        [rec.id for rec, _l, _d in rows],
        user_id=viewer_user_id,
        session_id=viewer_session_id,
    )
    return [
        _serialize_recommendation(
            rec,
            likes=likes,
            dislikes=dislikes,
            viewer_vote=viewer_votes.get(rec.id),
            venue=venue,
        )
        for rec, likes, dislikes in rows
    ]


def submit_recommendation(
    session: Session,
    *,
    user: User | None,
    session_id: str | None,
    query: str,
    by: str,
    near_latitude: float | None,
    near_longitude: float | None,
    venue_id: uuid.UUID | None = None,
    category: str,
    body: str,
    honeypot: str | None,
    ip_hash: str | None,
) -> dict[str, Any]:
    """Verify, validate, and insert a new recommendation.

    Flow:

    1. Honeypot — if the hidden form field has any value at all, reject
       with a blanket :class:`ValidationError` so bots get no hint.
    2. Identity — require either an authenticated user OR a guest
       ``session_id``. The schema CHECK would catch a missing identity
       too, but raising here gives the caller a clean 401.
    3. Minimum account age — authenticated accounts younger than
       :data:`MIN_ACCOUNT_AGE` cannot post. Does not apply to guests;
       guests get rate-limited by IP instead.
    4. Venue lookup — when ``venue_id`` is supplied, resolve the venue
       and use its coords as the anchor for name-based verification.
       A tip pinned to a venue must verify to a place within
       :data:`VENUE_GUARDRAIL_METERS` of that venue.
    5. Place verification — round-trip the user's query through Apple
       Maps. Only the verifier's fields (name, address, lat/lng,
       similarity) are persisted, so the client cannot smuggle in an
       unverified location.
    6. Guardrail — when venue-anchored, reject any verified place
       outside the 1000 m radius with ``PLACE_VERIFICATION_FAILED``.
    7. Persist — insert the row with venue_id, category, body, and
       ip_hash.

    Args:
        session: Active SQLAlchemy session.
        user: The authenticated author. ``None`` for guest submits.
        session_id: Guest session id when ``user`` is ``None``.
        query: Free-text place name or address typed by the user.
        by: Either ``"name"`` (default flow, needs a lat/lng anchor)
            or ``"address"``.
        near_latitude: Anchor latitude for name verification. Ignored
            for address verification and when ``venue_id`` is set (the
            venue's own coords become the anchor).
        near_longitude: Anchor longitude for name verification.
        venue_id: Optional FK to the venue the tip should attach to.
            When set, triggers the 1000 m guardrail and overrides the
            anchor lat/lng with the venue's own coords.
        category: Raw category string from the request.
        body: Raw recommendation body from the request.
        honeypot: Value of the hidden honeypot field; must be blank.
        ip_hash: Already-salted IP hash from the caller.

    Returns:
        The serialized recommendation dict. When ``venue_id`` is set
        the dict also includes ``distance_from_venue_m``.

    Raises:
        UnauthorizedError: If neither ``user`` nor ``session_id`` is
            supplied.
        ValidationError: For honeypot, account-age, bad category,
            bad body, or a malformed ``by`` / missing anchor.
        NotFoundError: ``VENUE_NOT_FOUND`` when ``venue_id`` is set but
            no venue exists with that id.
        AppError: ``PLACE_VERIFICATION_FAILED`` (422) when Apple has no
            match, the similarity gate rejects the only candidate, or
            the verified place sits outside the venue guardrail;
            ``APPLE_MAPS_UNAVAILABLE`` propagated from the service
            layer.
    """
    if honeypot:
        raise ValidationError("Could not post recommendation.")
    if user is None and not session_id:
        raise UnauthorizedError("You must sign in or have a session to post.")
    if user is not None and (
        user.created_at is None
        or datetime.now(UTC) - _as_aware_utc(user.created_at) < MIN_ACCOUNT_AGE
    ):
        raise ValidationError("Account is too new to post. Try again in a minute.")

    parsed_category = _parse_category(category)
    trimmed_body = _validated_body(body)

    anchor_venue: Venue | None = None
    if venue_id is not None:
        anchor_venue = venues_repo.get_venue_by_id(session, venue_id)
        if anchor_venue is None:
            raise NotFoundError(
                code=VENUE_NOT_FOUND,
                message=f"No venue found with id {venue_id}.",
            )
        if anchor_venue.latitude is None or anchor_venue.longitude is None:
            raise ValidationError("Venue has no coordinates; cannot anchor a tip.")
        effective_lat: float | None = anchor_venue.latitude
        effective_lng: float | None = anchor_venue.longitude
    else:
        effective_lat = near_latitude
        effective_lng = near_longitude

    verified = _verify_place(
        query=query,
        by=by,
        near_latitude=effective_lat,
        near_longitude=effective_lng,
    )

    if anchor_venue is not None:
        assert anchor_venue.latitude is not None
        assert anchor_venue.longitude is not None
        distance_m = _haversine_m(
            anchor_venue.latitude,
            anchor_venue.longitude,
            verified.latitude,
            verified.longitude,
        )
        if distance_m > VENUE_GUARDRAIL_METERS:
            raise AppError(
                code=PLACE_VERIFICATION_FAILED,
                message=(
                    f"'{verified.name}' is {round(distance_m)} m from "
                    f"{anchor_venue.name} — tips must be within "
                    f"{int(VENUE_GUARDRAIL_METERS)} m of the venue."
                ),
                status_code=422,
            )

    rec = rec_repo.create_recommendation(
        session,
        submitter_user_id=user.id if user is not None else None,
        session_id=session_id if user is None else None,
        venue_id=venue_id,
        place_name=verified.name,
        place_address=verified.address,
        latitude=verified.latitude,
        longitude=verified.longitude,
        similarity_score=verified.similarity,
        category=parsed_category,
        body=trimmed_body,
        ip_hash=ip_hash,
    )
    return _serialize_recommendation(
        rec, likes=0, dislikes=0, viewer_vote=None, venue=anchor_venue
    )


def delete_recommendation(
    session: Session,
    *,
    recommendation_id: uuid.UUID,
    user: User | None,
) -> None:
    """Allow the author of a recommendation to delete it.

    Only the submitting user can delete — guest submissions are not
    user-deletable (the frontend has no way to re-establish identity
    across sessions). Moderators use :func:`suppress_recommendation`
    instead.

    Args:
        session: Active SQLAlchemy session.
        recommendation_id: UUID of the recommendation.
        user: The authenticated caller.

    Raises:
        UnauthorizedError: If the caller is not signed in.
        NotFoundError: If no recommendation exists with that id.
        ForbiddenError: If the caller is not the author.
    """
    if user is None:
        raise UnauthorizedError("You must sign in to delete recommendations.")
    rec = rec_repo.get_recommendation_by_id(session, recommendation_id)
    if rec is None:
        raise NotFoundError(
            code=RECOMMENDATION_NOT_FOUND,
            message=f"No recommendation found with id {recommendation_id}.",
        )
    if rec.submitter_user_id != user.id:
        raise ForbiddenError("You can only delete your own recommendations.")
    rec_repo.delete_recommendation(session, rec)


def cast_vote(
    session: Session,
    *,
    recommendation_id: uuid.UUID,
    value: int,
    user: User | None,
    session_id: str | None,
) -> dict[str, Any]:
    """Record or update a vote on a recommendation, then auto-suppress if needed.

    When a downvote lands and the resulting
    ``(likes - dislikes) <= AUTO_SUPPRESS_NET_THRESHOLD``, the row is
    auto-suppressed so it drops off public map feeds immediately. The
    row is not deleted — admin tooling can un-suppress it later.

    Args:
        session: Active SQLAlchemy session.
        recommendation_id: UUID of the recommendation being voted on.
        value: +1, -1, or 0. ``0`` clears the voter's prior vote.
        user: The authenticated voter, or None for a guest.
        session_id: Guest session id when ``user`` is None.

    Returns:
        ``{"likes": int, "dislikes": int, "viewer_vote": int | None,
        "suppressed": bool}``.

    Raises:
        NotFoundError: If the recommendation doesn't exist.
        UnauthorizedError: If both identities are missing.
        ValidationError: If ``value`` is not -1, 0, or +1.
    """
    if user is None and not session_id:
        raise UnauthorizedError("You must sign in or have a session to vote.")
    if value not in (-1, 0, 1):
        raise ValidationError("Vote value must be -1, 0, or +1.")

    rec = rec_repo.get_recommendation_by_id(session, recommendation_id)
    if rec is None:
        raise NotFoundError(
            code=RECOMMENDATION_NOT_FOUND,
            message=f"No recommendation found with id {recommendation_id}.",
        )

    user_id = user.id if user is not None else None
    effective_session_id = None if user is not None else session_id
    if value == 0:
        rec_repo.clear_vote(
            session,
            recommendation_id=recommendation_id,
            user_id=user_id,
            session_id=effective_session_id,
        )
        viewer_vote: int | None = None
    else:
        rec_repo.upsert_vote(
            session,
            recommendation_id=recommendation_id,
            user_id=user_id,
            session_id=effective_session_id,
            value=value,
        )
        viewer_vote = value

    likes, dislikes = rec_repo.count_votes_for_recommendation(
        session, recommendation_id
    )
    net = likes - dislikes
    if rec.suppressed_at is None and net <= AUTO_SUPPRESS_NET_THRESHOLD:
        rec_repo.suppress_recommendation(session, rec)

    return {
        "likes": likes,
        "dislikes": dislikes,
        "viewer_vote": viewer_vote,
        "suppressed": rec.suppressed_at is not None,
    }


def _verify_place(
    *,
    query: str,
    by: str,
    near_latitude: float | None,
    near_longitude: float | None,
) -> apple_maps.VerifiedPlace:
    """Round-trip a user query through Apple's geocoder.

    Args:
        query: Free-text place name or address typed by the user.
        by: Either ``"name"`` or ``"address"``.
        near_latitude: Anchor latitude, required when ``by == "name"``.
        near_longitude: Anchor longitude, required when ``by == "name"``.

    Returns:
        A verified place with the Apple-canonical fields.

    Raises:
        ValidationError: If ``by`` is unrecognized or the anchor is
            missing for a name lookup.
        AppError: ``PLACE_NOT_VERIFIED`` (404) when Apple has no match
            or the similarity gate rejects the only candidate.
    """
    trimmed_query = (query or "").strip()
    if not trimmed_query:
        raise ValidationError("Missing place query.")
    normalized_by = by.strip().lower() if isinstance(by, str) else ""
    if normalized_by == "name":
        if near_latitude is None or near_longitude is None:
            raise ValidationError("Name verification requires lat and lng anchor.")
        place = apple_maps.verify_place_by_name(
            query=trimmed_query,
            near_latitude=near_latitude,
            near_longitude=near_longitude,
        )
    elif normalized_by == "address":
        place = apple_maps.verify_place_by_address(query=trimmed_query)
    else:
        raise ValidationError("`by` must be 'name' or 'address'.")
    if place is None:
        raise AppError(
            code=PLACE_VERIFICATION_FAILED,
            message="Apple Maps did not return a confident match.",
            status_code=422,
        )
    return place


__all__ = [
    "AUTO_SUPPRESS_NET_THRESHOLD",
    "MAX_BODY_LEN",
    "MIN_ACCOUNT_AGE",
    "MIN_BODY_LEN",
    "VENUE_GUARDRAIL_METERS",
    "cast_vote",
    "delete_recommendation",
    "hash_request_ip",
    "list_recommendations",
    "list_tips_for_venue",
    "submit_recommendation",
]

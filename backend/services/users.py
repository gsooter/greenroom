"""User business logic — profile retrieval, updates, and deactivation.

The API layer calls these functions and never touches the users
repository directly. Spotify-specific lifecycle (initial account
creation on OAuth login, token refresh) lives in
``backend/services/spotify.py``; this module is unaware of OAuth.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from backend.core.config import get_settings
from backend.core.exceptions import (
    CITY_NOT_FOUND,
    USER_NOT_FOUND,
    NotFoundError,
    ValidationError,
)
from backend.core.genres import GENRE_SLUGS
from backend.data.models.users import DigestFrequency, OAuthProvider, User
from backend.data.repositories import cities as cities_repo
from backend.data.repositories import users as users_repo

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

# Fields on ``User`` that callers are allowed to patch via PATCH /me.
# Anything not in this set is silently ignored to prevent mass-assignment
# of columns like ``email`` or ``is_active``.
_PATCHABLE_FIELDS = frozenset(
    {
        "display_name",
        "city_id",
        "digest_frequency",
        "genre_preferences",
        "notification_settings",
    }
)


def get_user(session: Session, user_id: uuid.UUID) -> User:
    """Fetch a user by ID.

    Args:
        session: Active SQLAlchemy session.
        user_id: UUID of the user.

    Returns:
        The :class:`User` instance.

    Raises:
        NotFoundError: If no user with that ID exists.
    """
    user = users_repo.get_user_by_id(session, user_id)
    if user is None:
        raise NotFoundError(
            code=USER_NOT_FOUND,
            message=f"No user found with id {user_id}",
        )
    return user


def update_user_profile(session: Session, user: User, patch: dict[str, Any]) -> User:
    """Apply a partial update to a user's profile.

    Only fields in the allowlist are applied; unknown fields are
    ignored so an attacker cannot flip ``is_active`` or rewrite
    ``email`` via the public PATCH endpoint.

    Args:
        session: Active SQLAlchemy session.
        user: The user to update.
        patch: Dictionary of field names and new values from the caller.

    Returns:
        The updated :class:`User` instance.

    Raises:
        ValidationError: If any value fails type/enum validation.
        NotFoundError: If ``city_id`` references a city that does not exist.
    """
    updates = _validated_updates(session, patch)
    if not updates:
        return user
    return users_repo.update_user(session, user, **updates)


def deactivate_user(session: Session, user: User) -> User:
    """Soft-delete a user by flipping ``is_active`` to False.

    A soft delete is preferred so saved events, recommendations, and
    email digest history remain linkable for analytics without leaking
    the deactivated account into any API response (protected endpoints
    reject inactive users via the ``@require_auth`` decorator).

    Args:
        session: Active SQLAlchemy session.
        user: The user to deactivate.

    Returns:
        The updated :class:`User` instance.
    """
    return users_repo.update_user(session, user, is_active=False)


def reactivate_user(session: Session, user: User) -> User:
    """Flip ``is_active`` back to True for a returning soft-deleted user.

    Called from the Knuckles session-exchange path when a previously
    deactivated account signs in again. A fresh successful identity
    exchange is unambiguous intent to return, and since deactivation
    is a soft delete — no rows are purged — the account is restored
    exactly as the user left it (saved events, follows, preferences).

    Args:
        session: Active SQLAlchemy session.
        user: The :class:`User` row whose ``is_active`` flag is False.

    Returns:
        The :class:`User` instance with ``is_active=True``.
    """
    return users_repo.update_user(session, user, is_active=True)


def serialize_user(user: User) -> dict[str, Any]:
    """Serialize a user for the authenticated ``/me`` endpoint.

    Intentionally omits ``is_active`` and anything OAuth-token-related
    — those are internal state the client never needs.

    Includes ``spotify_beta_access`` so the settings page can render
    the Spotify card as either a working Connect or a disabled
    "Limited access" card. The flag is recomputed on every call so an
    admin can grant access by editing the env var without invalidating
    sessions.

    Args:
        user: The user to serialize.

    Returns:
        Dictionary representation of the user.
    """
    return {
        "id": str(user.id),
        "email": user.email,
        "display_name": user.display_name,
        "avatar_url": user.avatar_url,
        "city_id": str(user.city_id) if user.city_id else None,
        "digest_frequency": user.digest_frequency.value,
        "genre_preferences": user.genre_preferences or [],
        "notification_settings": user.notification_settings or {},
        "spotify_beta_access": is_spotify_beta_user(user),
        "last_login_at": (
            user.last_login_at.isoformat() if user.last_login_at else None
        ),
        "created_at": user.created_at.isoformat(),
    }


def is_spotify_beta_user(user: User) -> bool:
    """Return whether ``user`` is allowlisted for the Spotify dev beta.

    The Spotify app currently runs in development mode, which caps
    real OAuth to 25 testers. Approved testers are listed in the
    ``SPOTIFY_BETA_EMAILS`` env var. Everyone else sees the Spotify
    card disabled with "Limited access" copy so the option doesn't
    feel like a broken promise.

    Args:
        user: The authenticated user.

    Returns:
        True when the user's email (case-insensitive) is in the
        allowlist, False otherwise.
    """
    if not user.email:
        return False
    return user.email.lower() in get_settings().spotify_beta_email_set()


_ARTIST_PREVIEW_LIMIT = 24


def list_music_connections(user: User) -> list[dict[str, Any]]:
    """Return per-provider connection state for the settings UI.

    For each supported music service, reports whether the user has a
    :class:`MusicServiceConnection` row and when their artist cache was
    last synced. Also returns a slim preview of up to
    :data:`_ARTIST_PREVIEW_LIMIT` cached artists so the settings page
    can render the same "Your rotation" chips it shows for Spotify
    across every provider.

    Args:
        user: The authenticated user.

    Returns:
        Ordered list of
        ``{provider, connected, synced_at, artist_count, artists}``
        dicts in display order (Spotify, Tidal, Apple Music). ``artists``
        entries expose only ``{id, name, genres, image_url}`` — the same
        slim shape the per-service sync helpers already persist.
    """
    cache_map: dict[OAuthProvider, tuple[list[dict[str, Any]] | None, Any]] = {
        OAuthProvider.SPOTIFY: (user.spotify_top_artists, user.spotify_synced_at),
        OAuthProvider.TIDAL: (user.tidal_top_artists, user.tidal_synced_at),
        OAuthProvider.APPLE_MUSIC: (
            user.apple_top_artists,
            user.apple_synced_at,
        ),
    }
    connected_providers = {c.provider for c in user.music_connections}
    order = [
        OAuthProvider.SPOTIFY,
        OAuthProvider.TIDAL,
        OAuthProvider.APPLE_MUSIC,
    ]
    result: list[dict[str, Any]] = []
    for provider in order:
        artists, synced_at = cache_map[provider]
        cached = artists or []
        result.append(
            {
                "provider": provider.value,
                "connected": provider in connected_providers,
                "synced_at": synced_at.isoformat() if synced_at else None,
                "artist_count": len(cached),
                "artists": [
                    _slim_artist_preview(a)
                    for a in cached[:_ARTIST_PREVIEW_LIMIT]
                    if isinstance(a, dict)
                ],
            }
        )
    return result


def _slim_artist_preview(artist: dict[str, Any]) -> dict[str, Any]:
    """Reduce a cached artist dict to the four fields the chip row uses.

    The three per-service sync helpers already store this shape, but we
    re-project here so a malformed row (missing name, extra keys) cannot
    leak through the settings endpoint.

    Args:
        artist: Raw cached artist dict from a provider-specific column.

    Returns:
        Dict with ``id``, ``name``, ``genres``, and ``image_url``. Missing
        values fall back to sensible defaults so the UI need not guard.
    """
    name = artist.get("name")
    genres = artist.get("genres")
    image_url = artist.get("image_url")
    return {
        "id": artist.get("id"),
        "name": name if isinstance(name, str) else "",
        "genres": (
            [g for g in genres if isinstance(g, str)]
            if isinstance(genres, list)
            else []
        ),
        "image_url": image_url if isinstance(image_url, str) else None,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validated_updates(session: Session, patch: dict[str, Any]) -> dict[str, Any]:
    """Filter, type-check, and coerce a profile patch payload.

    Args:
        session: Active SQLAlchemy session (used to validate ``city_id``).
        patch: Raw patch dictionary from the request body.

    Returns:
        Dictionary of vetted field/value pairs ready for the repository.

    Raises:
        ValidationError: If a value has the wrong type or enum.
        NotFoundError: If ``city_id`` references a nonexistent city.
    """
    updates: dict[str, Any] = {}
    for field, value in patch.items():
        if field not in _PATCHABLE_FIELDS:
            continue

        if field == "display_name":
            if value is not None and not isinstance(value, str):
                raise ValidationError("display_name must be a string or null.")
            updates[field] = value

        elif field == "city_id":
            updates[field] = _coerce_city_id(session, value)

        elif field == "digest_frequency":
            updates[field] = _coerce_digest_frequency(value)

        elif field == "genre_preferences":
            updates[field] = _coerce_genre_list(value)

        elif field == "notification_settings":
            if value is not None and not isinstance(value, dict):
                raise ValidationError(
                    "notification_settings must be an object or null."
                )
            updates[field] = value

    return updates


def _coerce_city_id(session: Session, value: Any) -> uuid.UUID | None:
    """Coerce and validate a ``city_id`` patch value.

    Args:
        session: Active SQLAlchemy session.
        value: Raw value from the request body.

    Returns:
        A UUID if the city exists, or None when the caller cleared it.

    Raises:
        ValidationError: If ``value`` is not a UUID string or null.
        NotFoundError: If the city does not exist.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError("city_id must be a UUID string or null.")
    try:
        city_id = uuid.UUID(value)
    except ValueError as exc:
        raise ValidationError(f"city_id is not a valid UUID: '{value}'") from exc
    if cities_repo.get_city_by_id(session, city_id) is None:
        raise NotFoundError(
            code=CITY_NOT_FOUND,
            message=f"No city found with id {city_id}",
        )
    return city_id


def _coerce_digest_frequency(value: Any) -> DigestFrequency:
    """Coerce a ``digest_frequency`` patch value to the enum type.

    Args:
        value: Raw value from the request body.

    Returns:
        The matching :class:`DigestFrequency` member.

    Raises:
        ValidationError: If ``value`` is not a valid frequency string.
    """
    if not isinstance(value, str):
        raise ValidationError("digest_frequency must be a string.")
    try:
        return DigestFrequency(value)
    except ValueError as exc:
        allowed = ", ".join(f.value for f in DigestFrequency)
        raise ValidationError(f"digest_frequency must be one of: {allowed}") from exc


def _coerce_genre_list(value: Any) -> list[str] | None:
    """Coerce a ``genre_preferences`` patch value to ``list[str] | None``.

    Each submitted slug is validated against
    :data:`backend.core.genres.GENRE_SLUGS` — unknown slugs are rejected
    so a client bug or stale UI build cannot write values the backend
    won't recognize later.

    Args:
        value: Raw value from the request body.

    Returns:
        A deduped list of known genre slugs, or None if the caller
        cleared the field.

    Raises:
        ValidationError: If ``value`` is not a list of strings or any
            submitted slug is not in the canonical set.
    """
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValidationError("genre_preferences must be an array of strings or null.")
    seen: set[str] = set()
    cleaned: list[str] = []
    for genre in value:
        if not isinstance(genre, str):
            raise ValidationError("genre_preferences must be an array of strings.")
        trimmed = genre.strip()
        if not trimmed:
            continue
        if trimmed not in GENRE_SLUGS:
            raise ValidationError(
                f"genre_preferences contains unknown slug '{trimmed}'."
            )
        if trimmed in seen:
            continue
        seen.add(trimmed)
        cleaned.append(trimmed)
    return cleaned

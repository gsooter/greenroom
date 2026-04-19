"""Repository functions for user, music-service connection, and saved event access.

All database queries related to users, their connected music services,
saved events, and recommendations are defined here.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.data.models.recommendations import Recommendation
from backend.data.models.users import (
    MusicServiceConnection,
    OAuthProvider,
    SavedEvent,
    User,
)

# ---------------------------------------------------------------------------
# User queries
# ---------------------------------------------------------------------------


def get_user_by_id(session: Session, user_id: uuid.UUID) -> User | None:
    """Fetch a user by their primary key.

    Args:
        session: Active SQLAlchemy session.
        user_id: UUID of the user.

    Returns:
        The User if found, otherwise None.
    """
    return session.get(User, user_id)


def get_user_by_email(session: Session, email: str) -> User | None:
    """Fetch a user by their email address.

    Args:
        session: Active SQLAlchemy session.
        email: User's email address.

    Returns:
        The User if found, otherwise None.
    """
    stmt = select(User).where(User.email == email)
    return session.execute(stmt).scalar_one_or_none()


def create_user(
    session: Session,
    *,
    email: str,
    user_id: uuid.UUID | None = None,
    display_name: str | None = None,
    avatar_url: str | None = None,
    city_id: uuid.UUID | None = None,
) -> User:
    """Create a new user.

    Args:
        session: Active SQLAlchemy session.
        email: User's email address.
        user_id: Optional explicit primary key. After Decision 030 the
            auto-provision path passes the Knuckles ``sub`` so Greenroom
            ``users.id`` stays identical to the Knuckles user UUID. When
            ``None``, SQLAlchemy generates a fresh UUID.
        display_name: User's display name.
        avatar_url: URL to user's profile image.
        city_id: Optional preferred city UUID.

    Returns:
        The newly created User instance.
    """
    user = User(
        id=user_id if user_id is not None else uuid.uuid4(),
        email=email,
        display_name=display_name,
        avatar_url=avatar_url,
        city_id=city_id,
    )
    session.add(user)
    session.flush()
    return user


def update_user(
    session: Session,
    user: User,
    **kwargs: Any,
) -> User:
    """Update a user's attributes.

    Args:
        session: Active SQLAlchemy session.
        user: The User instance to update.
        **kwargs: Attribute names and their new values.

    Returns:
        The updated User instance.
    """
    for key, value in kwargs.items():
        if hasattr(user, key):
            setattr(user, key, value)
    session.flush()
    return user


def update_last_login(session: Session, user: User) -> User:
    """Update the user's last_login_at timestamp to now.

    Args:
        session: Active SQLAlchemy session.
        user: The User instance.

    Returns:
        The updated User instance.
    """
    user.last_login_at = datetime.utcnow()
    session.flush()
    return user


# ---------------------------------------------------------------------------
# Music-service connection queries
# ---------------------------------------------------------------------------


def get_music_connection(
    session: Session,
    provider: OAuthProvider,
    provider_user_id: str,
) -> MusicServiceConnection | None:
    """Fetch a music-service connection by provider type and external user ID.

    Used during the connect flow to find if a Spotify (or other) account
    is already linked to a user.

    Args:
        session: Active SQLAlchemy session.
        provider: The music-service provider type.
        provider_user_id: User's ID on the provider platform.

    Returns:
        The MusicServiceConnection if found, otherwise None.
    """
    stmt = select(MusicServiceConnection).where(
        MusicServiceConnection.provider == provider,
        MusicServiceConnection.provider_user_id == provider_user_id,
    )
    return session.execute(stmt).scalar_one_or_none()


def create_music_connection(
    session: Session,
    *,
    user_id: uuid.UUID,
    provider: OAuthProvider,
    provider_user_id: str,
    access_token: str | None = None,
    refresh_token: str | None = None,
    token_expires_at: datetime | None = None,
    scopes: str | None = None,
    provider_data: dict[str, Any] | None = None,
) -> MusicServiceConnection:
    """Create a new music-service connection for a user.

    Args:
        session: Active SQLAlchemy session.
        user_id: UUID of the user to link.
        provider: The music-service provider type.
        provider_user_id: User's ID on the provider platform.
        access_token: OAuth access token.
        refresh_token: OAuth refresh token.
        token_expires_at: Token expiry datetime.
        scopes: Granted OAuth scopes.
        provider_data: Additional provider-specific data.

    Returns:
        The newly created MusicServiceConnection instance.
    """
    connection = MusicServiceConnection(
        user_id=user_id,
        provider=provider,
        provider_user_id=provider_user_id,
        access_token=access_token,
        refresh_token=refresh_token,
        token_expires_at=token_expires_at,
        scopes=scopes,
        provider_data=provider_data,
    )
    session.add(connection)
    session.flush()
    return connection


def update_music_connection_tokens(
    session: Session,
    connection: MusicServiceConnection,
    *,
    access_token: str,
    refresh_token: str | None = None,
    token_expires_at: datetime | None = None,
) -> MusicServiceConnection:
    """Update OAuth tokens on a music-service connection after a refresh.

    Args:
        session: Active SQLAlchemy session.
        connection: The MusicServiceConnection instance to update.
        access_token: New access token.
        refresh_token: New refresh token, if rotated.
        token_expires_at: New token expiry datetime.

    Returns:
        The updated MusicServiceConnection instance.
    """
    connection.access_token = access_token
    if refresh_token is not None:
        connection.refresh_token = refresh_token
    if token_expires_at is not None:
        connection.token_expires_at = token_expires_at
    session.flush()
    return connection


# ---------------------------------------------------------------------------
# Saved event queries
# ---------------------------------------------------------------------------


def get_saved_event(
    session: Session,
    user_id: uuid.UUID,
    event_id: uuid.UUID,
) -> SavedEvent | None:
    """Check if a user has saved a specific event.

    Args:
        session: Active SQLAlchemy session.
        user_id: UUID of the user.
        event_id: UUID of the event.

    Returns:
        The SavedEvent if found, otherwise None.
    """
    stmt = select(SavedEvent).where(
        SavedEvent.user_id == user_id,
        SavedEvent.event_id == event_id,
    )
    return session.execute(stmt).scalar_one_or_none()


def list_saved_events(
    session: Session,
    user_id: uuid.UUID,
    *,
    page: int = 1,
    per_page: int = 20,
) -> tuple[list[SavedEvent], int]:
    """Fetch a user's saved events with pagination.

    Args:
        session: Active SQLAlchemy session.
        user_id: UUID of the user.
        page: Page number, 1-indexed. Defaults to 1.
        per_page: Results per page. Defaults to 20.

    Returns:
        Tuple of (saved events list, total count).
    """
    base = select(SavedEvent).where(SavedEvent.user_id == user_id)

    count_stmt = select(func.count()).select_from(base.subquery())
    total = session.execute(count_stmt).scalar_one()

    stmt = (
        base.order_by(SavedEvent.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    saved = list(session.execute(stmt).scalars().all())
    return saved, total


def create_saved_event(
    session: Session,
    *,
    user_id: uuid.UUID,
    event_id: uuid.UUID,
) -> SavedEvent:
    """Save an event for a user.

    Args:
        session: Active SQLAlchemy session.
        user_id: UUID of the user.
        event_id: UUID of the event to save.

    Returns:
        The newly created SavedEvent instance.
    """
    saved = SavedEvent(user_id=user_id, event_id=event_id)
    session.add(saved)
    session.flush()
    return saved


def delete_saved_event(session: Session, saved: SavedEvent) -> None:
    """Remove a saved event.

    Args:
        session: Active SQLAlchemy session.
        saved: The SavedEvent instance to delete.
    """
    session.delete(saved)
    session.flush()


# ---------------------------------------------------------------------------
# Recommendation queries
# ---------------------------------------------------------------------------


def list_recommendations(
    session: Session,
    user_id: uuid.UUID,
    *,
    include_dismissed: bool = False,
    page: int = 1,
    per_page: int = 20,
) -> tuple[list[Recommendation], int]:
    """Fetch a user's recommendations ordered by score descending.

    Args:
        session: Active SQLAlchemy session.
        user_id: UUID of the user.
        include_dismissed: If True, include dismissed recommendations.
            Defaults to False.
        page: Page number, 1-indexed. Defaults to 1.
        per_page: Results per page. Defaults to 20.

    Returns:
        Tuple of (recommendations list, total count).
    """
    base = select(Recommendation).where(Recommendation.user_id == user_id)

    if not include_dismissed:
        base = base.where(Recommendation.is_dismissed.is_(False))

    count_stmt = select(func.count()).select_from(base.subquery())
    total = session.execute(count_stmt).scalar_one()

    stmt = (
        base.order_by(Recommendation.score.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    recs = list(session.execute(stmt).scalars().all())
    return recs, total


def create_recommendation(
    session: Session,
    *,
    user_id: uuid.UUID,
    event_id: uuid.UUID,
    score: float,
    score_breakdown: dict[str, Any],
) -> Recommendation:
    """Create a new recommendation for a user.

    Args:
        session: Active SQLAlchemy session.
        user_id: UUID of the user.
        event_id: UUID of the recommended event.
        score: Normalized score from 0.0 to 1.0.
        score_breakdown: Per-scorer scores and reasoning as dict.

    Returns:
        The newly created Recommendation instance.
    """
    rec = Recommendation(
        user_id=user_id,
        event_id=event_id,
        score=score,
        score_breakdown=score_breakdown,
    )
    session.add(rec)
    session.flush()
    return rec


def dismiss_recommendation(
    session: Session,
    recommendation: Recommendation,
) -> Recommendation:
    """Mark a recommendation as dismissed.

    Args:
        session: Active SQLAlchemy session.
        recommendation: The Recommendation instance to dismiss.

    Returns:
        The updated Recommendation instance.
    """
    recommendation.is_dismissed = True
    session.flush()
    return recommendation


def delete_recommendations_for_user(
    session: Session,
    user_id: uuid.UUID,
) -> int:
    """Delete all recommendations for a user.

    Used before regenerating recommendations so stale results
    don't accumulate.

    Args:
        session: Active SQLAlchemy session.
        user_id: UUID of the user.

    Returns:
        Number of recommendations deleted.
    """
    stmt = select(Recommendation).where(Recommendation.user_id == user_id)
    recs = list(session.execute(stmt).scalars().all())
    count = len(recs)
    for rec in recs:
        session.delete(rec)
    session.flush()
    return count

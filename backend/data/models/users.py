"""SQLAlchemy ORM models for users and OAuth providers.

Spotify OAuth is the only login method at launch (Decision 003).
The provider table pattern is implemented from day one so adding
Google or Apple OAuth later requires no schema migration — just
a new provider type in user_oauth_providers.
"""

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.data.models.events import Event
    from backend.data.models.recommendations import Recommendation


class OAuthProvider(enum.StrEnum):
    """Supported OAuth provider types.

    Only SPOTIFY is active at launch. Others defined for future
    expansion without schema migration (Decision 003).
    """

    SPOTIFY = "spotify"
    GOOGLE = "google"
    APPLE = "apple"


class DigestFrequency(enum.StrEnum):
    """Email digest frequency preferences."""

    DAILY = "daily"
    WEEKLY = "weekly"
    NEVER = "never"


class User(TimestampMixin, Base):
    """A registered user of the platform.

    Users authenticate via Spotify OAuth. Their Spotify listening
    data powers recommendations, saved shows, and email digests.

    Attributes:
        id: Unique identifier for the user.
        email: User's email address from their OAuth provider.
        display_name: User's display name from their OAuth provider.
        avatar_url: URL to the user's profile image.
        city_id: Optional preferred city for filtering events.
        digest_frequency: Email digest preference.
        genre_preferences: User's preferred genres for filtering.
        notification_settings: JSONB of notification preferences.
        is_active: Whether this account is active.
        last_login_at: Timestamp of the user's last login.
        spotify_top_artist_ids: Array of Spotify artist IDs cached from
            the user's /me/top/artists call. Used by the artist-match
            scorer against ``events.spotify_artist_ids`` — stored as
            an indexed array so matching can use array-overlap SQL.
        spotify_top_artists: JSONB snapshot of full artist records
            (id, name, genres, image) so the UI can render the
            top-artists grid without another Spotify API call.
        spotify_recent_artist_ids: Array of Spotify artist IDs derived
            from the user's recently-played tracks. Consumed by the
            artist-match scorer alongside the top-artist list so a user
            picking up a new artist this week still gets matched.
        spotify_recent_artists: JSONB snapshot of the recently-played
            artist records (id, name, genres, image), same shape as
            ``spotify_top_artists``.
        spotify_synced_at: Last time spotify_top_* / spotify_recent_*
            fields were refreshed.
        oauth_providers: Relationship to linked OAuth providers.
        saved_events: Relationship to user's saved events.
        recommendations: Relationship to user's recommendations.
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    email: Mapped[str] = mapped_column(
        String(320), unique=True, nullable=False, index=True
    )
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    city_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cities.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    digest_frequency: Mapped[DigestFrequency] = mapped_column(
        Enum(DigestFrequency, name="digest_frequency", native_enum=True),
        nullable=False,
        default=DigestFrequency.WEEKLY,
    )
    genre_preferences: Mapped[list[str] | None] = mapped_column(
        ARRAY(String(50)), nullable=True
    )
    notification_settings: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, default=dict
    )
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    spotify_top_artist_ids: Mapped[list[str] | None] = mapped_column(
        ARRAY(String(100)), nullable=True
    )
    spotify_top_artists: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSONB, nullable=True
    )
    spotify_recent_artist_ids: Mapped[list[str] | None] = mapped_column(
        ARRAY(String(100)), nullable=True
    )
    spotify_recent_artists: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSONB, nullable=True
    )
    spotify_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    oauth_providers: Mapped[list["UserOAuthProvider"]] = relationship(
        back_populates="user",
        lazy="selectin",
        cascade="all, delete-orphan",
    )
    saved_events: Mapped[list["SavedEvent"]] = relationship(
        back_populates="user",
        lazy="selectin",
        cascade="all, delete-orphan",
    )
    recommendations: Mapped[list["Recommendation"]] = relationship(
        back_populates="user",
        lazy="selectin",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        """Return a string representation of the User.

        Returns:
            String representation with user email.
        """
        return f"<User {self.email}>"


class UserOAuthProvider(TimestampMixin, Base):
    """A linked OAuth provider for a user.

    Provider table pattern (Decision 003) — adding a new OAuth provider
    (Google, Apple) requires only inserting a new row, no schema change.

    Attributes:
        id: Unique identifier for the provider link.
        user_id: Foreign key to the user.
        provider: OAuth provider type (spotify, google, apple).
        provider_user_id: User's ID on the provider platform.
        access_token: Current OAuth access token (encrypted at rest).
        refresh_token: OAuth refresh token (encrypted at rest).
        token_expires_at: When the access token expires.
        scopes: OAuth scopes granted by the user.
        provider_data: Additional provider-specific data as JSONB.
        user: Relationship to the parent user.
    """

    __tablename__ = "user_oauth_providers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider: Mapped[OAuthProvider] = mapped_column(
        Enum(OAuthProvider, name="oauth_provider", native_enum=True),
        nullable=False,
    )
    provider_user_id: Mapped[str] = mapped_column(
        String(200), nullable=False, index=True
    )
    access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    scopes: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # Relationships
    user: Mapped["User"] = relationship(
        back_populates="oauth_providers",
    )

    def __repr__(self) -> str:
        """Return a string representation of the UserOAuthProvider.

        Returns:
            String representation with provider type and user ID.
        """
        return f"<UserOAuthProvider {self.provider.value} user={self.user_id}>"


class SavedEvent(TimestampMixin, Base):
    """A user's saved/bookmarked event.

    Attributes:
        id: Unique identifier for the saved event record.
        user_id: Foreign key to the user who saved the event.
        event_id: Foreign key to the saved event.
        user: Relationship to the parent user.
        event: Relationship to the saved event.
    """

    __tablename__ = "saved_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Relationships
    user: Mapped["User"] = relationship(
        back_populates="saved_events",
    )
    event: Mapped["Event"] = relationship()

    def __repr__(self) -> str:
        """Return a string representation of the SavedEvent.

        Returns:
            String representation with user and event IDs.
        """
        return f"<SavedEvent user={self.user_id} event={self.event_id}>"

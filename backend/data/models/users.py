"""SQLAlchemy ORM models for users and connected music services.

After the Knuckles cutover (Decision 030), identity lives entirely in
Knuckles — local magic-link, Google, Apple, and passkey tables are gone.
Greenroom keeps the ``users`` row as a profile + preferences record whose
``id`` is the Knuckles user UUID, and the ``music_service_connections``
table as the link to connected music services (Spotify today; Apple
Music and Tidal in Phase 5).
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
    """Supported music-service provider types.

    After Decision 030, identity providers (google/apple/passkey) live
    in Knuckles; this enum only enumerates the music services Greenroom
    connects to. Apple Music and Tidal ship in Phase 5 — values are
    defined here so later phases stay data-only.
    """

    SPOTIFY = "spotify"
    APPLE_MUSIC = "apple_music"
    TIDAL = "tidal"


class DigestFrequency(enum.StrEnum):
    """Email digest frequency preferences."""

    DAILY = "daily"
    WEEKLY = "weekly"
    NEVER = "never"


class User(TimestampMixin, Base):
    """A Greenroom profile record keyed by its Knuckles user UUID.

    Knuckles is the identity anchor (Decision 030); ``users.id`` equals
    the Knuckles user's UUID, and this row stores only Greenroom-specific
    fields: preferences, Spotify caches, and digest settings. The profile
    is created lazily on the first authenticated request.

    Attributes:
        id: Knuckles user UUID. Primary key; also the ``sub`` claim on
            every Knuckles-issued access token.
        email: User's email address, mirrored from Knuckles for
            display and digest sending.
        display_name: User's display name.
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
        onboarding_completed_at: Timestamp set once when the user has
            finished the post-signup onboarding flow (Phase 4 genre
            picker). Null means the app should show onboarding on next
            visit; a non-null value means skip/don't re-show.
        music_connections: Relationship to connected music services.
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
    onboarding_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    music_connections: Mapped[list["MusicServiceConnection"]] = relationship(
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


class MusicServiceConnection(TimestampMixin, Base):
    """A connected music-service link for a user.

    Provider-table pattern (Decision 003) — adding a new music service
    (Apple Music, Tidal) is a row-level insert, not a schema change.
    Identity providers are handled by Knuckles; this table never holds
    a login-only provider after Decision 030.

    Attributes:
        id: Unique identifier for the connection row.
        user_id: Foreign key to the user.
        provider: Music-service provider type (spotify, apple_music, tidal).
        provider_user_id: User's ID on the provider platform.
        access_token: Current OAuth access token (encrypted at rest).
        refresh_token: OAuth refresh token (encrypted at rest).
        token_expires_at: When the access token expires.
        scopes: OAuth scopes granted by the user.
        provider_data: Additional provider-specific data as JSONB.
        user: Relationship to the parent user.
    """

    __tablename__ = "music_service_connections"

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
        back_populates="music_connections",
    )

    def __repr__(self) -> str:
        """Return a string representation of the MusicServiceConnection.

        Returns:
            String representation with provider type and user ID.
        """
        return f"<MusicServiceConnection {self.provider.value} user={self.user_id}>"


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

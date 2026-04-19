"""SQLAlchemy ORM models for users, OAuth providers, magic-link tokens,
and passkey credentials.

Greenroom has its own identity anchor (Decision 026): users authenticate
with a magic-link email, Google OAuth, Apple OAuth, or a WebAuthn passkey,
and Spotify is a connected music service rather than the login method.
The provider table pattern makes adding new providers (Apple Music, Tidal)
a row-level change rather than a schema migration.
"""

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.database import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.data.models.events import Event
    from backend.data.models.recommendations import Recommendation


class OAuthProvider(enum.StrEnum):
    """Supported OAuth provider types.

    Identity providers: ``google``, ``apple``, ``passkey``.
    ``spotify`` is retained as a connected music service (Decision 026).
    Music providers ``apple_music`` and ``tidal`` ship in Phase 5 — the
    enum values are defined here so later phases don't need to touch the
    schema enum.
    """

    SPOTIFY = "spotify"
    GOOGLE = "google"
    APPLE = "apple"
    PASSKEY = "passkey"
    APPLE_MUSIC = "apple_music"
    TIDAL = "tidal"


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
        password_hash: Reserved slot for future password-based auth.
            Nullable because the primary auth paths — magic link, Google,
            Apple, and passkey — never populate it.
        onboarding_completed_at: Timestamp set once when the user has
            finished the post-signup onboarding flow (Phase 4 genre
            picker). Null means the app should show onboarding on next
            visit; a non-null value means skip/don't re-show.
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
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    onboarding_completed_at: Mapped[datetime | None] = mapped_column(
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


class MagicLinkToken(TimestampMixin, Base):
    """A single-use magic-link sign-in token.

    The raw token (the long random string that rides in the email URL)
    is never stored — we persist a SHA-256 hash of it so a database
    disclosure does not hand out live login tokens. Rows live only
    long enough to be either consumed (``used_at`` set) or to expire
    (``expires_at`` passes); a nightly cleanup job prunes old rows.

    Attributes:
        id: Unique identifier for the token row.
        email: The address the link was issued to. Stored even when the
            user already exists so the verify step can upsert on a
            stable key without re-hitting the user row.
        token_hash: SHA-256 hex digest of the raw token. The raw token
            appears only in the outgoing email and is matched by hashing
            incoming values and comparing here.
        expires_at: Wall-clock UTC time after which the token is invalid
            regardless of ``used_at`` state.
        used_at: Timestamp when the token was redeemed. Null means the
            token is still redeemable (if within ``expires_at``).
        user_id: Populated once the token is redeemed so the audit trail
            points at the user it created/authenticated. Null until the
            verify step runs, because a request-link for a brand-new
            address has no user row yet.
    """

    __tablename__ = "magic_link_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(
        String(128), nullable=False, unique=True, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    def __repr__(self) -> str:
        """Return a string representation of the MagicLinkToken.

        Returns:
            String representation with email and used state.
        """
        state = "used" if self.used_at is not None else "pending"
        return f"<MagicLinkToken email={self.email} state={state}>"


class PasskeyCredential(TimestampMixin, Base):
    """A WebAuthn credential (Face ID / Touch ID / security key) for a user.

    Each row is one registered authenticator. Users may have multiple
    rows — one per device that completed passkey registration.

    Attributes:
        id: Unique identifier for the credential row.
        user_id: Foreign key to the owning user.
        credential_id: Raw credential identifier returned by the
            authenticator. Stored base64url-encoded. Unique across all
            users because the WebAuthn spec requires it.
        public_key: CBOR-encoded public key (base64url) that verifies
            signatures produced by this authenticator.
        sign_count: Monotonic usage counter reported by the authenticator.
            Incremented on each successful auth; a regression signals a
            cloned credential and MUST fail verification.
        transports: Optional comma-separated list of transports the
            authenticator advertises (e.g. "internal,hybrid"). Hint for
            the browser on the next authentication.
        name: Optional user-facing label for the credential
            ("MacBook Air", "iPhone"). Nullable because first-time
            registration doesn't ask.
        last_used_at: Timestamp of the most recent successful auth with
            this credential. Null until first use.
        user: Relationship back to the owning user.
    """

    __tablename__ = "passkey_credentials"
    __table_args__ = (
        UniqueConstraint("credential_id", name="uq_passkey_credential_id"),
    )

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
    credential_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    public_key: Mapped[str] = mapped_column(Text, nullable=False)
    sign_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    transports: Mapped[str | None] = mapped_column(String(200), nullable=True)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    user: Mapped["User"] = relationship()

    def __repr__(self) -> str:
        """Return a string representation of the PasskeyCredential.

        Returns:
            String representation with user id and credential label.
        """
        label = self.name or self.credential_id[:12]
        return f"<PasskeyCredential user={self.user_id} name={label}>"

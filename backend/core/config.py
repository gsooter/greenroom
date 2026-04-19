"""Application configuration loaded from environment variables.

All environment variables are defined and validated here using Pydantic
Settings. The app fails loudly at startup if a required variable is missing.
No other module should read os.environ directly.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    Attributes:
        spotify_client_id: Spotify OAuth client ID.
        spotify_client_secret: Spotify OAuth client secret.
        spotify_redirect_uri: Spotify OAuth redirect URI.
        database_url: PostgreSQL connection string.
        redis_url: Redis connection string.
        jwt_secret_key: Secret key for signing JWTs.
        jwt_expiry_seconds: JWT token expiry in seconds.
        sendgrid_api_key: SendGrid API key for email sending.
        sendgrid_from_email: Sender email address for SendGrid.
        ticketmaster_api_key: Ticketmaster Discovery API key.
        seatgeek_client_id: SeatGeek API client ID.
        seatgeek_client_secret: SeatGeek API client secret.
        admin_secret_key: Secret key for admin API routes.
        slack_webhook_url: Slack webhook URL for scraper alerts.
        alert_email: Fallback email for scraper failure alerts.
        posthog_api_key: PostHog analytics API key.
        posthog_host: PostHog instance host URL.
        frontend_base_url: Public URL of the Next.js app. Used when the
            backend generates user-facing links (magic-link verify URL,
            OAuth redirect landings, share URLs).
        magic_link_ttl_seconds: How long a magic-link token stays
            redeemable before ``expires_at`` invalidates it.
        google_oauth_client_id: Google Sign-In client id. Empty when the
            Google path is not configured.
        google_oauth_client_secret: Google Sign-In client secret.
        google_oauth_redirect_uri: Google OAuth redirect landing.
        apple_oauth_client_id: Sign-in-with-Apple "services id".
        apple_oauth_team_id: Apple Developer team id used to sign the
            client secret JWT.
        apple_oauth_key_id: Apple private-key id (``*.p8`` filename).
        apple_oauth_private_key: PEM-encoded Apple private key contents
            (``-----BEGIN PRIVATE KEY-----`` ... ``-----END ...``).
        apple_oauth_redirect_uri: Apple OAuth redirect landing.
        webauthn_rp_id: Relying-party id for WebAuthn — normally the
            apex domain of ``frontend_base_url`` (e.g. ``greenroom.app``).
        webauthn_rp_name: Display name shown on the native passkey
            prompt ("Save passkey for <rp_name>").
        webauthn_origin: Expected origin on WebAuthn ceremonies. Must
            include scheme and host, no trailing slash.
        tidal_client_id: Tidal Developer Platform client id (Phase 5).
        tidal_client_secret: Tidal Developer Platform client secret.
        tidal_redirect_uri: Tidal OAuth redirect URI.
        apple_music_developer_token: Apple MusicKit developer token —
            signed ES256 JWT minted offline and rotated via env
            (Phase 5).
        debug: Enable debug mode. Defaults to False.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Spotify
    spotify_client_id: str
    spotify_client_secret: str
    spotify_redirect_uri: str

    # Database
    database_url: str

    # Redis
    redis_url: str

    # JWT
    jwt_secret_key: str
    jwt_expiry_seconds: int = 3600

    # SendGrid
    sendgrid_api_key: str
    sendgrid_from_email: str

    # Ticketmaster
    ticketmaster_api_key: str

    # SeatGeek
    seatgeek_client_id: str
    seatgeek_client_secret: str

    # Admin
    admin_secret_key: str

    # Alerting
    slack_webhook_url: str
    alert_email: str

    # PostHog
    posthog_api_key: str
    posthog_host: str

    # App
    frontend_base_url: str = "http://localhost:3000"
    debug: bool = False

    # Magic link
    magic_link_ttl_seconds: int = 15 * 60

    # Google OAuth
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    google_oauth_redirect_uri: str = ""

    # Apple OAuth
    apple_oauth_client_id: str = ""
    apple_oauth_team_id: str = ""
    apple_oauth_key_id: str = ""
    apple_oauth_private_key: str = ""
    apple_oauth_redirect_uri: str = ""

    # WebAuthn (passkey)
    webauthn_rp_id: str = "localhost"
    webauthn_rp_name: str = "Greenroom"
    webauthn_origin: str = "http://localhost:3000"

    # Tidal (Phase 5)
    tidal_client_id: str = ""
    tidal_client_secret: str = ""
    tidal_redirect_uri: str = ""

    # Apple Music (Phase 5)
    apple_music_developer_token: str = ""


def get_settings() -> Settings:
    """Create and return a validated Settings instance.

    Returns:
        A Settings instance with all environment variables loaded.

    Raises:
        ValidationError: If any required environment variable is missing.
    """
    return Settings()  # type: ignore[call-arg]

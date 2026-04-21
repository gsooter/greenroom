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
        resend_api_key: Resend API key for transactional email.
        resend_from_email: Sender email address used on every Resend send.
        ticketmaster_api_key: Ticketmaster Discovery API key.
        seatgeek_client_id: SeatGeek API client ID.
        seatgeek_client_secret: SeatGeek API client secret.
        admin_secret_key: Secret key for admin API routes.
        slack_webhook_url: Slack webhook URL for scraper alerts.
        alert_email: Fallback email for scraper failure alerts.
        posthog_api_key: PostHog analytics API key.
        posthog_host: PostHog instance host URL.
        frontend_base_url: Public URL of the Next.js app. Used when the
            backend generates user-facing links (OAuth redirect landings,
            share URLs).
        tidal_client_id: Tidal Developer Platform client id (Phase 5).
        tidal_client_secret: Tidal Developer Platform client secret.
        tidal_redirect_uri: Tidal OAuth redirect URI.
        apple_music_team_id: Apple Developer Program team ID (10-char
            string shown top-right in the Apple developer portal).
        apple_music_key_id: MusicKit key identifier (10-char string
            printed next to the downloaded .p8 file).
        apple_music_private_key: The MusicKit .p8 private key contents
            (``-----BEGIN PRIVATE KEY-----...`` PEM). Prefer the inline
            value in prod; ``apple_music_private_key_path`` is a dev
            convenience that loads the key from disk at startup.
        apple_music_private_key_path: Optional filesystem path to the
            .p8 file. Used only when ``apple_music_private_key`` is
            empty — loaded once at startup.
        apple_music_bundle_id: MusicKit identifier registered in the
            Apple developer portal (e.g. ``music.com.greenroom.web``).
            Required on every developer-token mint.
        apple_mapkit_team_id: Apple Developer Program team ID used for
            MapKit JS and Maps Snapshot tokens. Usually the same value
            as ``apple_music_team_id`` (the team ID is account-wide),
            but exposed separately so the MapKit credentials can roll
            independently of MusicKit.
        apple_mapkit_key_id: MapKit JS Services key identifier — the
            10-char ID printed next to the downloaded .p8 file in the
            Apple developer portal.
        apple_mapkit_private_key: PEM contents of the MapKit .p8 key.
            Prefer this over ``apple_mapkit_private_key_path`` in prod.
        apple_mapkit_private_key_path: Optional filesystem path to the
            MapKit .p8, used only when the inline value is empty.
        knuckles_url: Base URL of the Knuckles identity service (no
            trailing slash). Empty during local dev when the legacy
            HS256 path is still in use.
        knuckles_client_id: ``app_clients.client_id`` Knuckles assigned
            to Greenroom. Used as the audience claim on access tokens
            and as the ``X-Client-Id`` header on Knuckles app-client
            endpoints.
        knuckles_client_secret: Matching app-client secret. Sent with
            ``X-Client-Secret`` on every Knuckles call.
        knuckles_jwks_cache_ttl_seconds: How long to keep a fetched
            JWKS in memory before refetching. A token signed with an
            unknown ``kid`` always triggers an immediate refresh
            regardless of TTL.
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

    # Resend
    resend_api_key: str
    resend_from_email: str

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

    # Tidal (Phase 5)
    tidal_client_id: str = ""
    tidal_client_secret: str = ""
    tidal_redirect_uri: str = ""

    # Apple Music (Phase 5 — pending Apple Developer Program approval)
    apple_music_team_id: str = ""
    apple_music_key_id: str = ""
    apple_music_private_key: str = ""
    apple_music_private_key_path: str = ""
    apple_music_bundle_id: str = ""

    # Apple Maps (MapKit JS + Snapshot + Maps Server API)
    apple_mapkit_team_id: str = ""
    apple_mapkit_key_id: str = ""
    apple_mapkit_private_key: str = ""
    apple_mapkit_private_key_path: str = ""

    # Knuckles identity service
    knuckles_url: str = ""
    knuckles_client_id: str = ""
    knuckles_client_secret: str = ""
    knuckles_jwks_cache_ttl_seconds: int = 60 * 60


def get_settings() -> Settings:
    """Create and return a validated Settings instance.

    Returns:
        A Settings instance with all environment variables loaded.

    Raises:
        ValidationError: If any required environment variable is missing.
    """
    return Settings()  # type: ignore[call-arg]

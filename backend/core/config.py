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
    debug: bool = False


def get_settings() -> Settings:
    """Create and return a validated Settings instance.

    Returns:
        A Settings instance with all environment variables loaded.

    Raises:
        ValidationError: If any required environment variable is missing.
    """
    return Settings()  # type: ignore[call-arg]

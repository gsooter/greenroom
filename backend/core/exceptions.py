"""Custom exception classes and error codes.

All API error codes are defined here as constants. Route handlers
catch these exceptions and return standardized error responses.
"""


class AppError(Exception):
    """Base exception for all application errors.

    Attributes:
        code: Machine-readable error code string.
        message: Human-readable error description.
        status_code: HTTP status code for the error response.
    """

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
    ) -> None:
        """Initialize an AppError.

        Args:
            code: Machine-readable error code string.
            message: Human-readable error description.
            status_code: HTTP status code. Defaults to 400.
        """
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class NotFoundError(AppError):
    """Raised when a requested resource does not exist."""

    def __init__(self, code: str, message: str) -> None:
        """Initialize a NotFoundError.

        Args:
            code: Machine-readable error code string.
            message: Human-readable error description.
        """
        super().__init__(code=code, message=message, status_code=404)


class UnauthorizedError(AppError):
    """Raised when authentication is missing or invalid."""

    def __init__(self, message: str = "Authentication required.") -> None:
        """Initialize an UnauthorizedError.

        Args:
            message: Human-readable error description.
        """
        super().__init__(code="UNAUTHORIZED", message=message, status_code=401)


class ForbiddenError(AppError):
    """Raised when the user lacks permission for the action."""

    def __init__(self, message: str = "Forbidden.") -> None:
        """Initialize a ForbiddenError.

        Args:
            message: Human-readable error description.
        """
        super().__init__(code="FORBIDDEN", message=message, status_code=403)


class ValidationError(AppError):
    """Raised when request input fails validation."""

    def __init__(self, message: str) -> None:
        """Initialize a ValidationError.

        Args:
            message: Human-readable validation error description.
        """
        super().__init__(code="VALIDATION_ERROR", message=message, status_code=422)


class RateLimitExceededError(AppError):
    """Raised when a caller exceeds a per-endpoint rate limit.

    Rendered as HTTP 429 with the ``RATE_LIMITED`` code so the client
    can distinguish "slow down" from other 4xx errors.
    """

    def __init__(
        self,
        message: str = "Too many requests. Please try again later.",
        retry_after_seconds: int | None = None,
    ) -> None:
        """Initialize a RateLimitExceededError.

        Args:
            message: Human-readable hint shown to the caller.
            retry_after_seconds: Approximate seconds until the caller
                may retry. Surfaced on the exception for the route
                handler to add as a ``Retry-After`` header.
        """
        super().__init__(code="RATE_LIMITED", message=message, status_code=429)
        self.retry_after_seconds = retry_after_seconds


# Error code constants
EVENT_NOT_FOUND = "EVENT_NOT_FOUND"
VENUE_NOT_FOUND = "VENUE_NOT_FOUND"
COMMENT_NOT_FOUND = "COMMENT_NOT_FOUND"
RECOMMENDATION_NOT_FOUND = "RECOMMENDATION_NOT_FOUND"
USER_NOT_FOUND = "USER_NOT_FOUND"
CITY_NOT_FOUND = "CITY_NOT_FOUND"
INVALID_TOKEN = "INVALID_TOKEN"
TOKEN_EXPIRED = "TOKEN_EXPIRED"
SPOTIFY_AUTH_FAILED = "SPOTIFY_AUTH_FAILED"
TIDAL_AUTH_FAILED = "TIDAL_AUTH_FAILED"
APPLE_MUSIC_AUTH_FAILED = "APPLE_MUSIC_AUTH_FAILED"
APPLE_MAPS_UNAVAILABLE = "APPLE_MAPS_UNAVAILABLE"
PLACE_NOT_VERIFIED = "PLACE_NOT_VERIFIED"
EMAIL_DELIVERY_FAILED = "EMAIL_DELIVERY_FAILED"

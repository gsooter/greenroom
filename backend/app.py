"""Flask application factory.

Creates and configures the Flask app with error handlers, CORS,
database session management, and blueprint registration.
"""

from typing import Any

from flask import Flask, Response, request
from werkzeug.exceptions import HTTPException

from backend.api.v1 import api_v1
from backend.core.config import get_settings
from backend.core.database import init_db
from backend.core.exceptions import AppError
from backend.core.logging import setup_logging

_CORS_ALLOWED_HEADERS = "Content-Type, Authorization"
_CORS_ALLOWED_METHODS = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
_CORS_MAX_AGE_SECONDS = 600


def create_app() -> Flask:
    """Create and configure the Flask application.

    Initializes logging, database session management, error handlers,
    CORS headers, and registers all API blueprints.

    Returns:
        A fully configured Flask application instance.
    """
    settings = get_settings()

    setup_logging(debug=settings.debug)

    app = Flask(__name__)
    app.config["DEBUG"] = settings.debug

    # Database session management
    init_db(app)

    # Error handlers
    _register_error_handlers(app)

    # CORS — allow frontend origin
    app.after_request(_add_cors_headers)

    # Blueprints
    app.register_blueprint(api_v1)

    # Health check
    @app.route("/health")
    def health() -> tuple[dict[str, str], int]:
        """Health check endpoint for load balancers and uptime monitors.

        Returns:
            Tuple of JSON response body and HTTP 200 status code.
        """
        return {"status": "ok"}, 200

    return app


def _register_error_handlers(app: Flask) -> None:
    """Register custom error handlers on the Flask app.

    Catches AppError subclasses and Werkzeug HTTP exceptions and
    returns standardized JSON error responses matching the API
    response format defined in CLAUDE.md.

    Args:
        app: The Flask application instance.
    """

    @app.errorhandler(AppError)
    def handle_app_error(
        error: AppError,
    ) -> tuple[dict[str, Any], int] | tuple[dict[str, Any], int, dict[str, str]]:
        """Handle custom application errors.

        Args:
            error: The AppError instance.

        Returns:
            Tuple of JSON error response and HTTP status code, plus a
            ``Retry-After`` header when the error is a rate-limit
            violation so clients can back off politely.
        """
        response = {
            "error": {
                "code": error.code,
                "message": error.message,
            }
        }
        retry_after = getattr(error, "retry_after_seconds", None)
        if retry_after is not None:
            return response, error.status_code, {"Retry-After": str(retry_after)}
        return response, error.status_code

    @app.errorhandler(HTTPException)
    def handle_http_error(error: HTTPException) -> tuple[dict[str, Any], int]:
        """Handle Werkzeug HTTP exceptions.

        Args:
            error: The HTTPException instance.

        Returns:
            Tuple of JSON error response and HTTP status code.
        """
        response = {
            "error": {
                "code": error.name.upper().replace(" ", "_"),
                "message": error.description or str(error),
            }
        }
        return response, error.code or 500

    @app.errorhandler(Exception)
    def handle_unexpected_error(error: Exception) -> tuple[dict[str, Any], int]:
        """Handle unexpected unhandled exceptions.

        Logs the full traceback and returns a generic 500 error.
        Never exposes raw exception messages to the client.

        Args:
            error: The unhandled exception.

        Returns:
            Tuple of JSON error response and HTTP 500 status code.
        """
        app.logger.exception("Unhandled exception: %s", error)
        response = {
            "error": {
                "code": "INTERNAL_SERVER_ERROR",
                "message": "An unexpected error occurred.",
            }
        }
        return response, 500


def _allowed_origins() -> set[str]:
    """Return the set of origins the API accepts cross-origin requests from.

    The frontend is the only browser-facing consumer, so the allowlist
    is a single origin: ``frontend_base_url``. Keeping it explicit —
    rather than the previous ``*`` — prevents third-party sites from
    issuing authenticated requests against the API.

    Returns:
        A set of exact-match allowed origin strings.
    """
    settings = get_settings()
    origin = settings.frontend_base_url.rstrip("/")
    return {origin} if origin else set()


def _add_cors_headers(response: Response) -> Response:
    """Attach CORS headers scoped to the configured frontend origin.

    The request's ``Origin`` is echoed back only when it matches an
    entry in :func:`_allowed_origins`. Browsers that receive no
    ``Access-Control-Allow-Origin`` header on a cross-origin response
    block the JavaScript caller — so disallowed origins get a clean
    rejection without any headers that could be mistaken for consent.
    ``Vary: Origin`` keeps intermediary caches from serving the wrong
    variant when the header is dynamically chosen.

    Args:
        response: The Flask response being returned.

    Returns:
        The same response with CORS headers added when appropriate.
    """
    origin = request.headers.get("Origin")
    if origin and origin in _allowed_origins():
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Headers"] = _CORS_ALLOWED_HEADERS
        response.headers["Access-Control-Allow-Methods"] = _CORS_ALLOWED_METHODS
        response.headers["Access-Control-Max-Age"] = str(_CORS_MAX_AGE_SECONDS)
    return response

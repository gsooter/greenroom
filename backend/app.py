"""Flask application factory."""

from flask import Flask

from backend.api.v1 import api_v1


def create_app() -> Flask:
    """Create and configure the Flask application.

    Returns:
        A configured Flask application instance.
    """
    app = Flask(__name__)

    app.register_blueprint(api_v1)

    return app

"""Flask API v1 blueprint registration."""

from flask import Blueprint

api_v1 = Blueprint("api_v1", __name__, url_prefix="/api/v1")

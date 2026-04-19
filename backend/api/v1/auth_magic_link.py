"""HTTP routes for the magic-link sign-in flow.

Two endpoints:

1. ``POST /api/v1/auth/magic-link/request`` — accepts an email, asks
   the auth service to mint a token and email the link. Always returns
   HTTP 202 with the same shape regardless of delivery outcome so the
   endpoint can't be used to enumerate which addresses are registered.

2. ``POST /api/v1/auth/magic-link/verify`` — accepts a raw token from
   the email URL, returns a Greenroom session JWT and the serialized
   user on success.
"""

from __future__ import annotations

from typing import Any

from flask import request

from backend.api.v1 import api_v1
from backend.core.database import get_db
from backend.core.exceptions import AppError, ValidationError
from backend.core.logging import get_logger
from backend.services import auth as auth_service
from backend.services import users as users_service

logger = get_logger(__name__)


@api_v1.route("/auth/magic-link/request", methods=["POST"])
def request_magic_link() -> tuple[dict[str, Any], int]:
    """Issue a magic-link token and email it to the caller.

    The response is intentionally uninformative: whether the address
    exists, whether SendGrid succeeded, and whether the user is active
    all produce the same 202 body. Anything else leaks information an
    attacker can use to enumerate accounts.

    Returns:
        Tuple of JSON body (``email_sent: true``) and HTTP 202.

    Raises:
        ValidationError: If the body is not a JSON object or ``email``
            is missing.
    """
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ValidationError("Request body must be a JSON object.")
    email = payload.get("email")
    if not isinstance(email, str) or not email.strip():
        raise ValidationError("Missing 'email' in request body.")

    session = get_db()
    try:
        auth_service.generate_magic_link(session, email=email)
    except AppError as exc:
        # Swallow delivery and service errors so the response shape is
        # identical for every caller — see Decision 027 and the route
        # docstring above.
        logger.warning("magic_link_request_failed: %s", exc)

    return {"data": {"email_sent": True}}, 202


@api_v1.route("/auth/magic-link/verify", methods=["POST"])
def verify_magic_link() -> tuple[dict[str, Any], int]:
    """Redeem a magic-link token and issue a Greenroom session JWT.

    Request body: ``{"token": "..."}``.

    Returns:
        Tuple of JSON body (``token``, ``user``) and HTTP 200.

    Raises:
        ValidationError: If ``token`` is missing or not a string.
        AppError: ``MAGIC_LINK_INVALID`` / ``_EXPIRED`` / ``_ALREADY_USED``
            as raised by the service layer.
    """
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ValidationError("Request body must be a JSON object.")
    token = payload.get("token")
    if not isinstance(token, str) or not token:
        raise ValidationError("Missing 'token' in request body.")

    session = get_db()
    result = auth_service.verify_magic_link(session, token=token)

    return {
        "data": {
            "token": result.jwt,
            "user": users_service.serialize_user(result.user),
        }
    }, 200

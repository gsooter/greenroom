"""One-click unsubscribe endpoint (RFC 8058).

This is the only Greenroom route that's intentionally unauthenticated.
Recipients click a link in their email client and we have to trust the
HMAC-signed token rather than a session cookie or bearer header — the
token *is* the credential.

GET previews the action without writing, so the frontend can render a
confirmation screen before the user commits. POST commits the change
and is the path mailbox providers hit when a user presses the
inbox-level "Unsubscribe" pill (the body arrives as
``List-Unsubscribe=One-Click`` per RFC 8058 — we ignore it; the token
in the query string is what matters).

Malformed, tampered, or expired tokens surface as ``VALIDATION_ERROR``
(HTTP 422) rather than 401, so the failure mode is "invalid input"
not "unauthorized" — the endpoint never expected an Authorization
header in the first place.
"""

from __future__ import annotations

from typing import Any

from flask import request

from backend.api.v1 import api_v1
from backend.core.database import get_db
from backend.core.exceptions import ValidationError
from backend.services import email_tokens
from backend.services import unsubscribe as unsubscribe_service


def _read_token() -> str:
    """Pull the unsubscribe token from the query string.

    Returns:
        The raw token string from the ``token`` query parameter.

    Raises:
        ValidationError: If the parameter is missing or empty. Surfaces
            as HTTP 422 so callers see a "validation" failure rather
            than an "unauthorized" one — the endpoint is not gated by
            an auth header in the first place.
    """
    token = request.args.get("token", "").strip()
    if not token:
        raise ValidationError("Missing 'token' query parameter.")
    return token


@api_v1.route("/unsubscribe", methods=["GET"])
def preview_unsubscribe() -> tuple[dict[str, Any], int]:
    """Verify the token and report what would happen, without writing.

    Lets the frontend render a "Confirm unsubscribe" screen for the
    affected scope before the recipient commits.

    Returns:
        Tuple of JSON envelope (``{"data": {...}}``) and HTTP 200.

    Raises:
        ValidationError: If the token is missing, malformed, expired,
            or its signature does not verify.
    """
    token = _read_token()
    decoded = email_tokens.verify_unsubscribe_token(token)
    return {
        "data": {
            "user_id": str(decoded.user_id),
            "scope": decoded.scope,
        }
    }, 200


@api_v1.route("/unsubscribe", methods=["POST"])
def commit_unsubscribe() -> tuple[dict[str, Any], int]:
    """Apply the unsubscribe described by the signed token.

    RFC 8058 mailbox providers POST
    ``List-Unsubscribe=One-Click`` as form-encoded body to the
    ``List-Unsubscribe`` URL — we don't parse the body; the token in
    the query string carries everything we need.

    Returns:
        Tuple of JSON envelope reporting the affected scope and a
        ``confirmed: true`` flag, plus HTTP 200.

    Raises:
        ValidationError: If the token is missing, malformed, expired,
            or its signature does not verify.
    """
    token = _read_token()
    session = get_db()
    decoded = unsubscribe_service.unsubscribe_with_token(session, token)
    return {
        "data": {
            "scope": decoded.scope,
            "confirmed": True,
        }
    }, 200

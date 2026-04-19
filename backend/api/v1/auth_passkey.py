"""HTTP routes for the WebAuthn passkey flows.

Four endpoints across two ceremonies:

Registration (signed-in user adding a passkey):
  * ``POST /auth/passkey/register/start``
  * ``POST /auth/passkey/register/complete``

Authentication (anonymous user signing in with a passkey):
  * ``POST /auth/passkey/authenticate/start``
  * ``POST /auth/passkey/authenticate/complete``

Ceremony state (the challenge plus, for registration, the user id) is
carried in a short-lived signed JWT rather than Redis — the same
pattern the Google and Apple state tokens use.
"""

from __future__ import annotations

from typing import Any

from flask import request

from backend.api.v1 import api_v1
from backend.core.auth import get_current_user, require_auth
from backend.core.database import get_db
from backend.core.exceptions import ValidationError
from backend.services import auth as auth_service
from backend.services import users as users_service


@api_v1.route("/auth/passkey/register/start", methods=["POST"])
@require_auth
def passkey_register_start() -> tuple[dict[str, Any], int]:
    """Return public-key options + signed state for registering a passkey.

    Returns:
        Tuple of JSON body (``options``, ``state``) and HTTP 200.
    """
    session = get_db()
    challenge = auth_service.passkey_registration_options(
        session, user=get_current_user()
    )
    return {"data": {"options": challenge.options, "state": challenge.state}}, 200


@api_v1.route("/auth/passkey/register/complete", methods=["POST"])
@require_auth
def passkey_register_complete() -> tuple[dict[str, Any], int]:
    """Verify the attestation and persist the new passkey credential.

    Request body::

        {"credential": <PublicKeyCredential JSON>, "state": "...",
         "name": "optional label"}

    Returns:
        Tuple of JSON body (``registered: true``) and HTTP 200.

    Raises:
        ValidationError: If the body is missing or malformed.
        AppError: ``PASSKEY_REGISTRATION_FAILED`` on any verification
            failure from the service layer.
    """
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ValidationError("Request body must be a JSON object.")
    credential = payload.get("credential")
    state = payload.get("state")
    name = payload.get("name")
    if not isinstance(credential, dict):
        raise ValidationError("Missing 'credential' in request body.")
    if not isinstance(state, str) or not state:
        raise ValidationError("Missing 'state' in request body.")
    if name is not None and not isinstance(name, str):
        raise ValidationError("'name' must be a string when supplied.")

    session = get_db()
    auth_service.passkey_register_complete(
        session,
        user=get_current_user(),
        credential=credential,
        state=state,
        name=name,
    )
    return {"data": {"registered": True}}, 200


@api_v1.route("/auth/passkey/authenticate/start", methods=["POST"])
def passkey_authenticate_start() -> tuple[dict[str, Any], int]:
    """Return public-key request options for a passkey sign-in.

    Returns:
        Tuple of JSON body (``options``, ``state``) and HTTP 200.
    """
    challenge = auth_service.passkey_authentication_options()
    return {"data": {"options": challenge.options, "state": challenge.state}}, 200


@api_v1.route("/auth/passkey/authenticate/complete", methods=["POST"])
def passkey_authenticate_complete() -> tuple[dict[str, Any], int]:
    """Verify an assertion and mint a Greenroom session JWT.

    Request body::

        {"credential": <PublicKeyCredential JSON>, "state": "..."}

    Returns:
        Tuple of JSON body (``token``, ``user``) and HTTP 200.

    Raises:
        ValidationError: If the body is missing or malformed.
        AppError: ``PASSKEY_AUTH_FAILED`` on any verification failure
            or unknown credential from the service layer.
    """
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ValidationError("Request body must be a JSON object.")
    credential = payload.get("credential")
    state = payload.get("state")
    if not isinstance(credential, dict):
        raise ValidationError("Missing 'credential' in request body.")
    if not isinstance(state, str) or not state:
        raise ValidationError("Missing 'state' in request body.")

    session = get_db()
    login = auth_service.passkey_authenticate_complete(
        session, credential=credential, state=state
    )
    return {
        "data": {
            "token": login.jwt,
            "user": users_service.serialize_user(login.user),
        }
    }, 200

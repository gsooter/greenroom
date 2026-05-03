"""Web Push subscription and test routes.

Three endpoints live here:

* ``GET  /api/v1/push/vapid-public-key`` — public, returns the VAPID
  public key the browser needs before calling
  ``pushManager.subscribe()``.
* ``POST /api/v1/push/subscribe`` — authenticated, persists or
  refreshes a subscription record for the caller.
* ``DELETE /api/v1/push/subscriptions`` — authenticated, removes a
  subscription by its endpoint (used when the user revokes
  permission).
* ``POST /api/v1/push/test`` — admin, sends a canned notification to
  every active subscription belonging to a given user. Useful in
  staging and in production support situations.
"""

from __future__ import annotations

import uuid
from typing import Any

from flask import request

from backend.api.v1 import api_v1
from backend.api.v1.admin import require_admin
from backend.core.auth import get_current_user, require_auth
from backend.core.config import get_settings
from backend.core.database import get_db
from backend.core.exceptions import ValidationError
from backend.core.logging import get_logger
from backend.core.vapid_keys import to_raw_public_key
from backend.data.repositories import push_subscriptions as push_repo
from backend.services import push as push_service

logger = get_logger(__name__)


@api_v1.route("/push/vapid-public-key", methods=["GET"])
def get_vapid_public_key() -> tuple[dict[str, Any], int]:
    """Return the VAPID public key the browser needs to subscribe.

    Returns:
        Tuple of ``({"data": {"public_key": ...}}, 200)``.
        ``public_key`` is empty when VAPID has not been configured —
        the frontend treats this as "push is not available in this
        environment" and hides the permission prompt. The configured
        value may be PEM or raw base64url; this endpoint always emits
        the raw form because that's what ``pushManager.subscribe``
        accepts.
    """
    raw_value = get_settings().vapid_public_key
    try:
        public_key = to_raw_public_key(raw_value)
    except ValueError as err:
        logger.error("vapid_public_key_unparseable", extra={"error": str(err)})
        public_key = ""
    return {"data": {"public_key": public_key}}, 200


@api_v1.route("/push/subscribe", methods=["POST"])
@require_auth
def subscribe() -> tuple[dict[str, Any], int]:
    """Persist a browser-supplied push subscription for the caller.

    Request body:
        ``endpoint`` — push-service URL.
        ``keys.p256dh`` — browser P-256 public key.
        ``keys.auth`` — browser auth secret.

    Returns:
        Tuple of ``({"data": {"subscribed": true}}, 201)``.

    Raises:
        ValidationError: If any required field is missing or has the
            wrong type. The frontend retries the subscribe call after
            re-fetching the VAPID key on a 422.
    """
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        raise ValidationError("Request body must be a JSON object.")

    endpoint = payload.get("endpoint")
    keys = payload.get("keys") or {}
    if not isinstance(endpoint, str) or not endpoint:
        raise ValidationError("Field 'endpoint' is required and must be a string.")
    if not isinstance(keys, dict):
        raise ValidationError("Field 'keys' must be an object.")
    p256dh = keys.get("p256dh")
    auth = keys.get("auth")
    if not isinstance(p256dh, str) or not p256dh:
        raise ValidationError("Field 'keys.p256dh' is required.")
    if not isinstance(auth, str) or not auth:
        raise ValidationError("Field 'keys.auth' is required.")

    user = get_current_user()
    session = get_db()
    push_repo.upsert(
        session,
        user_id=user.id,
        endpoint=endpoint,
        p256dh_key=p256dh,
        auth_key=auth,
        user_agent=request.headers.get("User-Agent", "")[:500] or None,
    )
    session.commit()
    return {"data": {"subscribed": True}}, 201


@api_v1.route("/push/subscriptions", methods=["DELETE"])
@require_auth
def unsubscribe() -> tuple[dict[str, Any], int]:
    """Remove a subscription identified by its endpoint.

    Request body:
        ``endpoint`` — the push-service URL the row was created with.

    Returns:
        Tuple of ``({"data": {"removed": <bool>}}, 200)``. ``removed``
        is True when a row matched and was deleted, False when no
        row matched (idempotent — clients can call this on every
        permission revoke without checking first).

    Raises:
        ValidationError: If ``endpoint`` is missing.
    """
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        raise ValidationError("Request body must be a JSON object.")
    endpoint = payload.get("endpoint")
    if not isinstance(endpoint, str) or not endpoint:
        raise ValidationError("Field 'endpoint' is required.")

    user = get_current_user()
    session = get_db()
    removed = push_repo.delete_for_endpoint(session, user.id, endpoint)
    session.commit()
    return {"data": {"removed": removed}}, 200


@api_v1.route("/push/test", methods=["POST"])
@require_admin
def send_test_push() -> tuple[dict[str, Any], int]:
    """Send a canned notification to every subscription of a user.

    Used by ops/support to confirm a recipient's PWA is wired up
    end-to-end after they report not receiving expected pushes.

    Request body:
        ``user_id`` — UUID of the recipient user.
        ``title`` — optional override for the notification title.
        ``body`` — optional override for the notification body.

    Returns:
        Tuple of JSON response with the
        :class:`backend.services.push.SendResult` fields and
        HTTP 200, even when nothing was sent — the result body
        explains why (no subscriptions, VAPID not configured, etc.).

    Raises:
        ValidationError: If ``user_id`` is missing or not a UUID.
    """
    payload = request.get_json(silent=True) or {}
    raw_user_id = payload.get("user_id")
    try:
        user_id = uuid.UUID(str(raw_user_id))
    except (TypeError, ValueError) as exc:
        raise ValidationError("'user_id' must be a UUID.") from exc

    title = payload.get("title") or "Greenroom test"
    body = (
        payload.get("body")
        or "Push notifications are wired up correctly. No action required."
    )
    session = get_db()
    result = push_service.send_to_user(
        session,
        user_id,
        push_service.PushPayload(
            title=str(title),
            body=str(body),
            url=get_settings().frontend_base_url,
            tag="greenroom-test",
        ),
    )
    session.commit()
    return {
        "data": {
            "attempted": result.attempted,
            "succeeded": result.succeeded,
            "disabled": result.disabled,
            "skipped_no_vapid": result.skipped_no_vapid,
        }
    }, 200

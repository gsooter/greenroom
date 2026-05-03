"""Inbound webhook endpoints from third-party services.

Today this module owns one route: ``POST /api/v1/webhooks/resend``,
the receiver Resend posts to whenever a previously-sent message
bounces, generates a complaint, or otherwise produces a delivery
event we want to react to.

The route deliberately verifies the Svix-style signature *before*
parsing the JSON body. A forged or replayed payload never reaches
the database — the handler short-circuits to a 422 long before any
write happens. Resend's reference implementation tolerates a 5-minute
timestamp window; ours does too.

The route returns 200 on a successful verify even if the event was
not actionable (e.g. an opens/clicks event we don't track yet).
Returning 4xx for ignorable events would cause Resend to retry the
delivery indefinitely, which clutters the webhook queue without
adding signal.
"""

from __future__ import annotations

from typing import Any

from flask import request

from backend.api.v1 import api_v1
from backend.core.database import get_db
from backend.core.exceptions import ValidationError
from backend.core.logging import get_logger
from backend.services import resend_webhooks

logger = get_logger(__name__)


@api_v1.route("/webhooks/resend", methods=["POST"])
def resend_webhook() -> tuple[dict[str, Any], int]:
    """Receive a single Resend webhook event.

    Verifies the Svix signature using the configured
    ``RESEND_WEBHOOK_SECRET``, then dispatches to the service layer
    which decides whether the event marks a recipient as bounced.

    Returns:
        Tuple of ``({"data": {...}}, 200)`` on success. The ``data``
        dict reports the action taken (``marked_hard_bounce``,
        ``marked_complaint``, or ``ignored``) so the integration is
        debuggable from the server-side request log.

    Raises:
        ValidationError: If the signature headers are missing, the
            signature does not verify, the timestamp is outside the
            tolerance window, or the configured secret is missing.
            All surface as HTTP 422 to make signature failures visually
            distinct from auth failures (this endpoint never had an
            ``Authorization`` header to fail).
    """
    svix_id = request.headers.get("svix-id", "").strip()
    svix_ts = request.headers.get("svix-timestamp", "").strip()
    svix_sig = request.headers.get("svix-signature", "").strip()
    if not svix_id or not svix_ts or not svix_sig:
        raise ValidationError("Missing svix-* signature headers.")

    raw = request.get_data(cache=False, as_text=False)
    resend_webhooks.verify_signature(
        raw,
        svix_id=svix_id,
        svix_timestamp=svix_ts,
        svix_signature=svix_sig,
    )

    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        raise ValidationError("Webhook body must be a JSON object.")

    session = get_db()
    result = resend_webhooks.handle_event(session, payload)
    session.commit()

    logger.info(
        "resend_webhook_processed",
        extra={
            "event_type": payload.get("type"),
            "action": result.get("action"),
            "user_id": result.get("user_id"),
        },
    )
    return {"data": result}, 200

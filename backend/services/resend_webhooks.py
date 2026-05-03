"""Resend webhook payload handling.

Resend posts JSON events to ``POST /api/v1/webhooks/resend`` whenever
something interesting happens to a previously-sent message: it was
delivered, opened, clicked, bounced, or generated a complaint. We
care about three of those for sender-reputation hygiene:

* ``email.bounced`` (especially hard bounces) — the recipient address
  is no longer valid; future sends would be guaranteed-fail and harm
  our domain reputation. Mark the user as bounced so the send
  pipeline skips them until an admin clears the flag.
* ``email.complained`` — the recipient pressed "report spam." Same
  treatment as a bounce: stop sending until the user updates their
  preferences explicitly.
* ``email.delivered`` / ``email.opened`` / ``email.clicked`` — useful
  telemetry to attach to the matching ``email_digest_log`` row, but
  not required for the bounce-skip flow.

The webhook itself is signed with a Svix-style ``whsec_...`` shared
secret. We verify the signature before trusting the payload — Resend
re-uses the Svix scheme so a forged body without the right HMAC is
rejected at the door.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from backend.core.config import get_settings
from backend.core.exceptions import ValidationError
from backend.core.logging import get_logger

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = get_logger(__name__)


# Resend events we treat as "stop sending to this recipient until
# manually cleared." Soft bounces (transient mailbox-full type
# errors) are intentionally excluded — Resend will retry them and
# we don't want to block a legitimate inbox over a temporary blip.
_HARD_FAIL_EVENTS: frozenset[str] = frozenset(
    {
        "email.bounced",
        "email.complained",
    }
)

# Tolerance window around the webhook timestamp. Svix's reference
# implementation uses 5 minutes — anything older than that is
# treated as a replay attack.
_TIMESTAMP_TOLERANCE_SECONDS: int = 5 * 60


def verify_signature(
    payload: bytes,
    *,
    svix_id: str,
    svix_timestamp: str,
    svix_signature: str,
) -> None:
    """Verify a Resend webhook payload's Svix-style signature.

    Args:
        payload: The raw request body bytes — must be the exact bytes
            Resend hashed, so callers must pass ``request.get_data()``
            rather than a re-encoded JSON dict.
        svix_id: Value of the ``svix-id`` header.
        svix_timestamp: Value of the ``svix-timestamp`` header (a Unix
            epoch second as a string).
        svix_signature: Value of the ``svix-signature`` header. May
            contain multiple space-separated ``v1,<sig>`` entries; we
            accept the request if any of them matches.

    Raises:
        ValidationError: If the configured webhook secret is missing,
            the timestamp is outside the tolerance window, or no
            provided signature matches.
    """
    settings = get_settings()
    secret = settings.resend_webhook_secret
    if not secret:
        raise ValidationError("Resend webhook secret is not configured.")
    if not secret.startswith("whsec_"):
        raise ValidationError("Resend webhook secret must start with 'whsec_'.")

    try:
        ts = int(svix_timestamp)
    except (TypeError, ValueError) as exc:
        raise ValidationError("Invalid svix-timestamp header.") from exc

    if abs(int(_now()) - ts) > _TIMESTAMP_TOLERANCE_SECONDS:
        raise ValidationError("Webhook timestamp outside tolerance window.")

    secret_bytes = base64.b64decode(secret[len("whsec_") :])
    signed_input = f"{svix_id}.{svix_timestamp}.".encode() + payload
    expected = base64.b64encode(
        hmac.new(secret_bytes, signed_input, hashlib.sha256).digest()
    ).decode("ascii")

    candidates = [
        chunk.split(",", 1)[1]
        for chunk in svix_signature.split(" ")
        if chunk.startswith("v1,")
    ]
    if not any(hmac.compare_digest(c, expected) for c in candidates):
        raise ValidationError("Webhook signature did not verify.")


def handle_event(session: Session, event: dict[str, Any]) -> dict[str, Any]:
    """Process a single Resend webhook event.

    Args:
        session: Active SQLAlchemy session. The caller (route handler)
            commits or rolls back.
        event: Decoded JSON event body from the webhook.

    Returns:
        A small dict the route echoes back to Resend in the 200
        response: ``{"action": "marked_bounced"|"marked_complaint"|
        "ignored", "user_id": <uuid str | None>}``. Resend ignores the
        body but the dict makes the integration easier to debug from
        the request log.
    """
    event_type = event.get("type")
    data = event.get("data") or {}
    if not isinstance(event_type, str) or not isinstance(data, dict):
        return {"action": "ignored", "reason": "malformed_event"}

    if event_type not in _HARD_FAIL_EVENTS:
        return {"action": "ignored", "reason": "not_a_hard_fail_event"}

    to_field = data.get("to")
    if isinstance(to_field, list) and to_field:
        recipient = to_field[0]
    elif isinstance(to_field, str):
        recipient = to_field
    else:
        return {"action": "ignored", "reason": "no_recipient"}
    if not isinstance(recipient, str):
        return {"action": "ignored", "reason": "no_recipient"}

    # Local import to avoid a heavy module pull at import time of this
    # service module (which loads even in unit tests that never touch
    # users).
    from backend.data.repositories import users as users_repo

    user = users_repo.get_user_by_email(session, recipient.lower())
    if user is None:
        logger.info(
            "resend_webhook_ignored_unknown_recipient",
            extra={"event_type": event_type, "recipient": recipient},
        )
        return {"action": "ignored", "reason": "unknown_recipient"}

    reason = "complaint" if event_type == "email.complained" else "hard_bounce"
    user.email_bounced_at = datetime.now(UTC)
    user.email_bounce_reason = reason
    return {
        "action": f"marked_{reason}",
        "user_id": str(user.id),
    }


def _now() -> float:
    """Return the current Unix timestamp.

    Wrapped so tests can monkey-patch wall-clock time without touching
    :mod:`time`.

    Returns:
        Current epoch time in seconds.
    """
    import time

    return time.time()

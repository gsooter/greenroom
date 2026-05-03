"""Web Push send pipeline.

The unified notification dispatcher (:mod:`backend.services.notifications.dispatcher`)
hands a :class:`PushPayload` and a recipient user id to
:func:`send_to_user`. We look up every active push subscription for
that user, encrypt the payload per RFC 8291 + RFC 8030, and POST the
ciphertext to each endpoint with the VAPID JWT signed by our private
key. Failures bump per-row counters; permanent-failure responses
disable the row outright.

All of the cryptography lives in :mod:`pywebpush` so we never have
to touch the ECDH math directly. Our wrapper owns:

* fan-out (one user → N subscriptions),
* shape coercion (dispatcher dataclass → pywebpush dict),
* failure classification (transient retry vs permanent disable),
* result aggregation (so the dispatcher can log a single line per
  send).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from backend.core.config import get_settings
from backend.core.logging import get_logger
from backend.data.repositories import push_subscriptions as push_repo

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.orm import Session

    from backend.data.models.push import PushSubscription

logger = get_logger(__name__)


# Once a single subscription has failed this many sends in a row,
# the dispatcher treats it as dead even if the server has not yet
# returned a permanent-failure status code. Most legitimate transient
# failures recover inside two retries; five is comfortable headroom.
MAX_CONSECUTIVE_FAILURES: int = 5

# HTTP status codes the Web Push protocol defines as "the endpoint
# is gone — never send to it again." Anything else (including 5xx)
# is treated as transient and bumps the failure counter.
_PERMANENT_FAILURE_STATUSES: frozenset[int] = frozenset({404, 410})

# TTL the push service should hold the message for if the device is
# offline. 24 hours is the common default; we don't use higher values
# because the notifications we send are time-sensitive (a "tour just
# announced" push that arrives three days late is worse than nothing).
_DEFAULT_TTL_SECONDS: int = 24 * 60 * 60


@dataclass(frozen=True)
class PushPayload:
    """The actual push body delivered to the user's device.

    The payload is JSON-encoded by :func:`send_to_user` before
    encryption — keep the dataclass shallow so the JSON stays under
    the 4 KB Web Push payload limit.

    Attributes:
        title: Inbox-level title — short, specific, no hedging.
        body: One-line body. Aim for under 80 chars total with the
            title so the notification fits inside iOS's collapsed view
            without ellipsis.
        url: Absolute URL the service worker opens when the user
            taps the notification.
        tag: Optional grouping tag. The OS replaces an existing
            notification with the same tag rather than stacking it,
            which keeps "show reminder" pings from piling up.
    """

    title: str
    body: str
    url: str
    tag: str | None = None

    def to_json(self) -> str:
        """Serialize the payload for the service worker.

        Returns:
            JSON string the ``push`` event handler in ``public/sw.js``
            parses and feeds to ``self.registration.showNotification``.
        """
        out: dict[str, Any] = {
            "title": self.title,
            "body": self.body,
            "url": self.url,
        }
        if self.tag is not None:
            out["tag"] = self.tag
        return json.dumps(out, separators=(",", ":"))


@dataclass(frozen=True)
class SendResult:
    """Outcome of a single :func:`send_to_user` invocation.

    Attributes:
        attempted: Subscriptions we tried to send to.
        succeeded: Subscriptions that returned a 2xx.
        disabled: Subscriptions disabled this round (permanent failure
            or failure-ceiling tripped).
        skipped_no_vapid: When True, the dispatcher short-circuited
            the entire send because VAPID is not configured. Useful
            for the test endpoint to surface "you forgot to set the
            keys" without crashing.
    """

    attempted: int
    succeeded: int
    disabled: int
    skipped_no_vapid: bool


def is_configured() -> bool:
    """Return True when every VAPID env var is non-empty.

    Returns:
        True if the dispatcher can attempt a send; False if it must
        short-circuit. Read by the API test endpoint to surface the
        misconfiguration to the caller rather than 500.
    """
    settings = get_settings()
    return bool(
        settings.vapid_private_key
        and settings.vapid_public_key
        and settings.vapid_subject
    )


def send_to_user(
    session: Session,
    user_id: uuid.UUID,
    payload: PushPayload,
    *,
    ttl_seconds: int = _DEFAULT_TTL_SECONDS,
) -> SendResult:
    """Fan out ``payload`` to every active subscription owned by ``user_id``.

    Args:
        session: Active SQLAlchemy session. The caller commits or
            rolls back; this function only writes failure-count and
            disabled-at updates inside the same transaction.
        user_id: UUID of the recipient user.
        payload: Push body to deliver.
        ttl_seconds: How long the push service should hold the
            message for an offline device.

    Returns:
        A :class:`SendResult` summarizing fan-out outcomes.
    """
    if not is_configured():
        logger.warning("push_send_skipped_no_vapid", extra={"user_id": str(user_id)})
        return SendResult(attempted=0, succeeded=0, disabled=0, skipped_no_vapid=True)

    subs = push_repo.list_active_for_user(session, user_id)
    if not subs:
        return SendResult(attempted=0, succeeded=0, disabled=0, skipped_no_vapid=False)

    succeeded = 0
    disabled = 0
    body = payload.to_json()
    settings = get_settings()
    vapid_claims = {"sub": settings.vapid_subject}

    for sub in subs:
        outcome = _send_one(
            sub,
            body=body,
            ttl=ttl_seconds,
            vapid_private_key=settings.vapid_private_key,
            vapid_claims=vapid_claims,
        )
        if outcome == "ok":
            push_repo.record_success(session, sub)
            succeeded += 1
        elif outcome == "permanent":
            push_repo.disable_subscription(session, sub)
            disabled += 1
        else:
            push_repo.record_failure(session, sub)
            if (sub.failure_count or 0) >= MAX_CONSECUTIVE_FAILURES:
                push_repo.disable_subscription(session, sub)
                disabled += 1

    return SendResult(
        attempted=len(subs),
        succeeded=succeeded,
        disabled=disabled,
        skipped_no_vapid=False,
    )


def _send_one(
    subscription: PushSubscription,
    *,
    body: str,
    ttl: int,
    vapid_private_key: str,
    vapid_claims: dict[str, str],
) -> str:
    """POST a single encrypted payload to one subscription endpoint.

    Args:
        subscription: The subscription row to send to.
        body: The plaintext JSON payload (UTF-8 string).
        ttl: Push-service TTL in seconds.
        vapid_private_key: PEM or base64url-encoded VAPID private key.
        vapid_claims: VAPID claims dict (must include ``sub``).

    Returns:
        ``"ok"`` on 2xx, ``"permanent"`` for 404/410, ``"transient"``
        for any other failure (including network errors). The string
        return type keeps the caller free of exception handling for
        the common "endpoint dead" case.
    """
    # Local import: pywebpush pulls in cryptography at import time,
    # which is heavy. Keep it out of unit-test paths that never call
    # the real send.
    from pywebpush import WebPushException, webpush  # type: ignore[import-not-found]

    sub_info = {
        "endpoint": subscription.endpoint,
        "keys": {
            "p256dh": subscription.p256dh_key,
            "auth": subscription.auth_key,
        },
    }
    try:
        webpush(
            subscription_info=sub_info,
            data=body,
            vapid_private_key=vapid_private_key,
            vapid_claims=dict(vapid_claims),
            ttl=ttl,
        )
        return "ok"
    except WebPushException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in _PERMANENT_FAILURE_STATUSES:
            logger.info(
                "push_endpoint_disabled",
                extra={
                    "subscription_id": str(subscription.id),
                    "status": status,
                },
            )
            return "permanent"
        logger.warning(
            "push_send_transient_failure",
            extra={
                "subscription_id": str(subscription.id),
                "status": status,
            },
        )
        return "transient"
    except Exception:
        logger.exception(
            "push_send_unexpected_error",
            extra={"subscription_id": str(subscription.id)},
        )
        return "transient"

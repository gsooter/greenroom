"""Authenticated "send me a test email" endpoint.

Powers the "Send a test email" button on /settings/notifications. The
endpoint renders the same ``show_announcement`` template the weekly
digest uses against a tiny placeholder context, then hands it to the
shared :func:`backend.services.email.compose_email` so the recipient
sees the real branding, footer, and one-click unsubscribe link.

Rate-limited to five sends per five-minute window per user — enough
slack for someone tweaking settings and re-checking, low enough that
abusing the endpoint as a free relay isn't interesting.
"""

from __future__ import annotations

from typing import Any

from backend.api.v1 import api_v1
from backend.core.auth import get_current_user, require_auth
from backend.core.config import get_settings
from backend.core.exceptions import AppError
from backend.core.logging import get_logger
from backend.core.rate_limit import rate_limit
from backend.services import email as email_service

logger = get_logger(__name__)


def _current_user_id_key() -> str:
    """Rate-limit subject keyed on the current user id.

    Returns:
        UUID-as-string for the Redis bucket key. ``require_auth`` runs
        before the rate-limit decorator, so the user is already loaded.
    """
    return str(get_current_user().id)


def _placeholder_show(frontend_base_url: str) -> dict[str, Any]:
    """Build one fake show row to populate the test email body.

    The real weekly digest pulls these from the recommender. For a
    test we want a row that renders correctly without depending on
    the user's saved shows or city data, so the values are
    self-explanatory and obviously synthetic.

    Args:
        frontend_base_url: Base URL the placeholder show links to.
            Always points at the live frontend so a recipient who
            taps it lands somewhere real.

    Returns:
        A show dict matching the keys
        ``backend/services/email_templates/_show_card.html`` reads.
    """
    return {
        "url": f"{frontend_base_url}/events",
        "headliner": "Test Show",
        "venue": "Greenroom Settings",
        "date_short": "TODAY",
        "time_short": "Anytime",
        "image_url": None,
        "supporting": "",
        "min_price": None,
        "venue_url": frontend_base_url,
    }


@api_v1.route("/me/email/test", methods=["POST"])
@require_auth
@rate_limit(
    "me_email_test_user",
    limit=5,
    window_seconds=300,
    key_fn=_current_user_id_key,
)
def send_test_email_to_self() -> tuple[dict[str, Any], int]:
    """Send a sample email to the caller's address.

    The email reuses the production ``show_announcement`` template so
    the recipient sees the real branding, including the one-click
    unsubscribe link (scoped to ``weekly_digest`` since that's the
    shape of the body — clicking it would turn off the weekly digest).

    Refuses to send when the address has bounced or generated a
    complaint, since that's the same reason the dispatcher would
    skip the user; surfacing the refusal here saves the user from
    "I clicked test and nothing happened, why?"

    Returns:
        Tuple of ``({"data": {"sent": <bool>, "to": "<masked email>",
        "reason": "<short tag>"}}, 200)``. ``sent`` is True when
        Resend accepted the message; ``reason`` is one of
        ``"sent"``, ``"bounced"``, ``"no_email"``,
        ``"delivery_failed"``.
    """
    user = get_current_user()
    if not user.email:
        return (
            {"data": {"sent": False, "to": "", "reason": "no_email"}},
            200,
        )
    if user.email_bounced_at is not None:
        return (
            {
                "data": {
                    "sent": False,
                    "to": _mask_email(user.email),
                    "reason": "bounced",
                }
            },
            200,
        )

    settings = get_settings()
    base_url = settings.frontend_base_url or "https://greenroom.gstwentyseven.com"
    context: dict[str, Any] = {
        "heading": "This is a test email from Greenroom",
        "intro": (
            "You triggered this from your notification settings. "
            "It's safe to ignore — no real shows are referenced."
        ),
        "preheader": "Test send from your settings page.",
        "cta_url": f"{base_url}/settings/notifications",
        "cta_label": "Back to settings",
        "shows": [_placeholder_show(base_url)],
        "structured_data": [],
        "manage_url": f"{base_url}/settings/notifications",
    }

    try:
        email_service.compose_email(
            to=user.email,
            user_id=user.id,
            subject="Greenroom test email",
            template="show_announcement",
            scope="weekly_digest",
            context=context,
        )
    except AppError as exc:
        logger.warning(
            "test_email_send_failed",
            extra={"user_id": str(user.id), "code": exc.code},
        )
        return (
            {
                "data": {
                    "sent": False,
                    "to": _mask_email(user.email),
                    "reason": "delivery_failed",
                }
            },
            200,
        )

    return (
        {
            "data": {
                "sent": True,
                "to": _mask_email(user.email),
                "reason": "sent",
            }
        },
        200,
    )


def _mask_email(address: str) -> str:
    """Partially mask an email for inclusion in a JSON response.

    Args:
        address: Full email address.

    Returns:
        ``a***@example.com`` style mask. Keeps the first letter and
        the full domain so the user can recognise it without the
        response leaking a complete address into logs.
    """
    if "@" not in address:
        return address
    local, _, domain = address.partition("@")
    if not local:
        return address
    return f"{local[0]}***@{domain}"

"""Transactional email delivery through SendGrid.

The service layer talks to this module rather than importing SendGrid
directly, so tests can stub the single :func:`send_email` entry point
without monkey-patching the SDK.
"""

from typing import Any

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

from backend.core.config import get_settings
from backend.core.exceptions import EMAIL_DELIVERY_FAILED, AppError
from backend.core.logging import get_logger

logger = get_logger(__name__)


def send_email(
    *,
    to: str,
    subject: str,
    html_body: str,
    text_body: str | None = None,
) -> None:
    """Send a single transactional email.

    Args:
        to: Recipient address.
        subject: Email subject line.
        html_body: HTML body of the email.
        text_body: Plain-text fallback body. Most modern clients render
            HTML, but deliverability is better when both are present.

    Raises:
        AppError: ``EMAIL_DELIVERY_FAILED`` on any SendGrid error. The
            caller decides whether to surface this to the user or log
            and swallow.
    """
    settings = get_settings()
    message = Mail(
        from_email=settings.sendgrid_from_email,
        to_emails=to,
        subject=subject,
        html_content=html_body,
        plain_text_content=text_body or _strip_html(html_body),
    )
    try:
        client = SendGridAPIClient(settings.sendgrid_api_key)
        response: Any = client.send(message)
    except Exception as exc:  # SendGrid raises a broad set of classes
        logger.warning("sendgrid_send_failed: %s", exc)
        raise AppError(
            code=EMAIL_DELIVERY_FAILED,
            message="Failed to deliver email.",
            status_code=502,
        ) from exc

    status = getattr(response, "status_code", None)
    if isinstance(status, int) and status >= 400:
        logger.warning("sendgrid_http_error: status=%s to=%s", status, to)
        raise AppError(
            code=EMAIL_DELIVERY_FAILED,
            message="SendGrid returned a non-success status.",
            status_code=502,
        )


def _strip_html(html: str) -> str:
    """Produce a plain-text fallback from an HTML string.

    A naive tag stripper is fine here — magic-link emails are one
    paragraph with a link, so the fallback only needs the link to be
    clickable and the prose to be readable.

    Args:
        html: HTML source.

    Returns:
        The input with HTML tags removed and whitespace collapsed.
    """
    import re

    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text).strip()

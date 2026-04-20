"""Transactional email delivery through Resend.

The service layer talks to this module rather than importing the HTTP
client directly, so tests can stub the single :func:`send_email` entry
point without monkey-patching any SDK.
"""

from typing import Any

import requests

from backend.core.config import get_settings
from backend.core.exceptions import EMAIL_DELIVERY_FAILED, AppError
from backend.core.logging import get_logger

logger = get_logger(__name__)

_RESEND_ENDPOINT = "https://api.resend.com/emails"
_RESEND_TIMEOUT_SECONDS = 10


def send_email(
    *,
    to: str,
    subject: str,
    html_body: str,
    text_body: str | None = None,
) -> None:
    """Send a single transactional email via Resend.

    Args:
        to: Recipient address.
        subject: Email subject line.
        html_body: HTML body of the email.
        text_body: Plain-text fallback body. Most modern clients render
            HTML, but deliverability is better when both are present.

    Raises:
        AppError: ``EMAIL_DELIVERY_FAILED`` on any Resend error. The
            caller decides whether to surface this to the user or log
            and swallow.
    """
    settings = get_settings()
    payload: dict[str, Any] = {
        "from": settings.resend_from_email,
        "to": [to],
        "subject": subject,
        "html": html_body,
        "text": text_body or _strip_html(html_body),
    }
    headers = {
        "Authorization": f"Bearer {settings.resend_api_key}",
        "Content-Type": "application/json",
    }
    try:
        response = requests.post(
            _RESEND_ENDPOINT,
            json=payload,
            headers=headers,
            timeout=_RESEND_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        logger.warning("resend_send_failed: %s", exc)
        raise AppError(
            code=EMAIL_DELIVERY_FAILED,
            message="Failed to deliver email.",
            status_code=502,
        ) from exc

    if response.status_code >= 400:
        logger.warning(
            "resend_http_error: status=%s to=%s body=%s",
            response.status_code,
            to,
            response.text,
        )
        raise AppError(
            code=EMAIL_DELIVERY_FAILED,
            message="Resend returned a non-success status.",
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

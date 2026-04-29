"""Transactional email delivery through Resend.

The service layer talks to this module rather than importing the HTTP
client directly, so tests can stub the single :func:`send_email` entry
point without monkey-patching any SDK.

Two entry points are exposed:

* :func:`send_email` is the low-level wrapper around the Resend HTTP
  API. It owns the transport contract — payload shape, headers, error
  mapping — and is the only place in the codebase that talks to
  ``api.resend.com``.
* :func:`compose_email` is the high-level helper most callers should
  reach for. It renders a template, mints an RFC 8058 unsubscribe
  token scoped to the right preference column, and forwards the
  result to :func:`send_email` with the ``List-Unsubscribe`` headers
  already attached.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import requests

from backend.core.config import get_settings
from backend.core.exceptions import EMAIL_DELIVERY_FAILED, AppError
from backend.core.logging import get_logger
from backend.services.email_renderer import RenderedEmail, render_email
from backend.services.email_tokens import mint_unsubscribe_token

if TYPE_CHECKING:
    import uuid

logger = get_logger(__name__)

_RESEND_ENDPOINT = "https://api.resend.com/emails"
_RESEND_TIMEOUT_SECONDS = 10


def send_email(
    *,
    to: str,
    subject: str,
    html_body: str,
    text_body: str | None = None,
    unsubscribe_url: str | None = None,
) -> None:
    """Send a single transactional email via Resend.

    Args:
        to: Recipient address.
        subject: Email subject line.
        html_body: HTML body of the email.
        text_body: Plain-text fallback body. Most modern clients render
            HTML, but deliverability is better when both are present.
        unsubscribe_url: Public URL of the one-click unsubscribe
            endpoint, with the recipient's signed token already in the
            query string. When supplied, the message is sent with the
            RFC 8058 ``List-Unsubscribe`` and ``List-Unsubscribe-Post``
            headers so mailbox providers render an inbox-level
            unsubscribe pill. Omit for transactional one-offs (magic
            links, password resets) that don't carry unsubscribe
            semantics.

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
    if unsubscribe_url:
        payload["headers"] = {
            "List-Unsubscribe": f"<{unsubscribe_url}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
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


def compose_email(
    *,
    to: str,
    user_id: uuid.UUID,
    subject: str,
    template: str,
    scope: str,
    context: dict[str, Any],
) -> None:
    """Render, sign, and send a templated email in one call.

    Mints an unsubscribe token scoped to ``scope``, builds the public
    unsubscribe URL, injects that URL into ``context`` so the base
    template's footer renders a clickable link, renders the
    ``template`` pair (HTML + text), and forwards everything to
    :func:`send_email` along with the RFC 8058 ``List-Unsubscribe``
    headers.

    Args:
        to: Recipient address.
        user_id: UUID of the recipient. Used to mint the unsubscribe
            token; never logged.
        subject: Email subject line.
        template: Template stem under
            ``backend/services/email_templates/``, e.g.
            ``"show_announcement"``.
        scope: Unsubscribe scope to embed in the token. ``"all"``
            routes to the global pause; any per-type value names a
            single ``NotificationPreferences`` boolean column.
        context: Caller-supplied template context. ``unsubscribe_url``
            is injected automatically — caller-supplied keys take
            precedence in case of collision (which is also a sign of
            a bug worth catching in review).

    Raises:
        ValueError: If ``scope`` is not a recognised unsubscribe scope.
        EmailTemplateNotFoundError: If the template pair does not
            exist on disk.
        AppError: ``EMAIL_DELIVERY_FAILED`` if Resend fails.
    """
    token = mint_unsubscribe_token(user_id, scope)
    unsubscribe_url = _build_unsubscribe_url(token)

    full_context: dict[str, Any] = {"unsubscribe_url": unsubscribe_url}
    full_context.update(context)

    rendered: RenderedEmail = render_email(template, full_context)

    send_email(
        to=to,
        subject=subject,
        html_body=rendered.html,
        text_body=rendered.text,
        unsubscribe_url=unsubscribe_url,
    )


def _build_unsubscribe_url(token: str) -> str:
    """Build the public one-click unsubscribe URL for a minted token.

    Args:
        token: The signed token returned by
            :func:`backend.services.email_tokens.mint_unsubscribe_token`.

    Returns:
        Absolute URL pointing at ``/api/v1/unsubscribe`` on the public
        frontend host, with the token in the query string.
    """
    settings = get_settings()
    base = settings.frontend_base_url.rstrip("/")
    return f"{base}/api/v1/unsubscribe?token={token}"


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

"""Render outbound email bodies from Jinja2 templates.

Templates live in ``backend/services/email_templates/``. Each email
type has both an ``.html`` and a ``.txt`` template — the HTML is what
mailbox previews show; the plain-text fallback is what screen readers
and bandwidth-constrained clients render. Most clients also use the
plain-text body to compute spam scores, so we always send both.

The shared base templates own the brand chrome (header, footer,
unsubscribe links). Per-email templates extend the bases and pass a
context dict that names the heading, intro copy, the list of show
cards to render, and the JSON-LD structured-data blob (if any) to
embed for Gmail/Apple actionable cards.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2 import (
    Environment,
    FileSystemLoader,
    TemplateNotFound,
    select_autoescape,
)

_TEMPLATE_DIR = Path(__file__).parent / "email_templates"

# Two environments because autoescape semantics differ. HTML output
# must escape every interpolation by default to neutralise hostile
# strings; plain-text output must not insert HTML entity references
# (recipients would see ``&amp;`` rather than ``&``).
_html_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(["html"]),
    trim_blocks=False,
    lstrip_blocks=False,
)
_text_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=False,
    trim_blocks=False,
    lstrip_blocks=False,
)


class EmailTemplateNotFoundError(LookupError):
    """Raised when a caller asks for a template that doesn't exist.

    This is a developer error, never user-facing. Email templates are
    a fixed enum baked into the codebase, so a missing one means a
    typo in the call site.
    """


@dataclass(frozen=True)
class RenderedEmail:
    """The HTML and plain-text bodies produced for a single send.

    Attributes:
        html: Fully-rendered HTML body, ready for the Resend ``html``
            field.
        text: Fully-rendered plain-text body, ready for the Resend
            ``text`` field. Always non-empty so deliverability tools
            can score the message.
    """

    html: str
    text: str


def render_email(template: str, context: dict[str, Any]) -> RenderedEmail:
    """Render an email's HTML and plain-text bodies in one call.

    Args:
        template: Stem name of the template pair, e.g.
            ``"show_announcement"`` for the
            ``show_announcement.html`` / ``show_announcement.txt``
            pair under :data:`_TEMPLATE_DIR`.
        context: Mapping passed to both Jinja2 environments. Keys
            consumed by the base templates: ``preheader``, ``heading``,
            ``intro``, ``shows`` (list of show-card dicts), ``cta_url``,
            ``cta_label``, ``user_email``, ``unsubscribe_url``,
            ``manage_url``, and (optional) ``structured_data``.

    Returns:
        A :class:`RenderedEmail` with both body variants.

    Raises:
        EmailTemplateNotFoundError: If either the ``.html`` or ``.txt``
            half of the template pair is missing.
    """
    try:
        html = _html_env.get_template(f"{template}.html").render(**context)
    except TemplateNotFound as exc:
        raise EmailTemplateNotFoundError(
            f"No HTML email template named {template!r}"
        ) from exc

    try:
        text = _text_env.get_template(f"{template}.txt").render(**context)
    except TemplateNotFound as exc:
        raise EmailTemplateNotFoundError(
            f"No text email template named {template!r}"
        ) from exc

    return RenderedEmail(html=html, text=text)

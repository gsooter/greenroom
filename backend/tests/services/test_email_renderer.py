"""Tests for :mod:`backend.services.email_renderer`.

Email rendering touches three things every send relies on: the HTML
template (must escape user input, must include the unsubscribe URL),
the plain-text fallback (deliverability + accessibility), and the
JSON-LD blob that Gmail and Apple Mail use to render actionable
cards. Each is exercised here so a template change can't break a
production send silently.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.services import email_renderer


def test_render_returns_html_and_text() -> None:
    """A rendered email exposes both the HTML and plain-text bodies."""
    rendered = email_renderer.render_email(
        template="show_announcement",
        context={
            "preheader": "A show you'll love just got announced.",
            "heading": "Phoebe Bridgers at 9:30 Club",
            "intro": "Tickets dropped today.",
            "shows": [_show()],
            "cta_label": "View show",
            "cta_url": "https://greenroom.test/events/abc",
            "user_email": "fan@example.com",
            "unsubscribe_url": "https://greenroom.test/unsub?token=tok",
            "manage_url": "https://greenroom.test/settings/notifications",
        },
    )
    assert "<html" in rendered.html.lower()
    assert "Phoebe Bridgers" in rendered.html
    assert "Phoebe Bridgers" in rendered.text
    assert rendered.text.strip()


def test_render_escapes_html_in_user_data() -> None:
    """Untrusted strings in context don't break out of the template."""
    rendered = email_renderer.render_email(
        template="show_announcement",
        context={
            "preheader": "x",
            "heading": "<script>alert(1)</script>",
            "intro": "x",
            "shows": [_show(headliner="<script>")],
            "cta_label": "View show",
            "cta_url": "https://greenroom.test/x",
            "user_email": "fan@example.com",
            "unsubscribe_url": "https://greenroom.test/unsub?token=tok",
            "manage_url": "https://greenroom.test/settings/notifications",
        },
    )
    assert "<script>alert(1)</script>" not in rendered.html
    assert "&lt;script&gt;" in rendered.html


def test_render_injects_unsubscribe_url_into_footer() -> None:
    """The unsubscribe URL must appear verbatim in both bodies."""
    url = "https://greenroom.test/unsub?token=abc.def.ghi"
    rendered = email_renderer.render_email(
        template="show_announcement",
        context={
            "preheader": "x",
            "heading": "x",
            "intro": "x",
            "shows": [_show()],
            "cta_label": "View show",
            "cta_url": "https://greenroom.test/x",
            "user_email": "fan@example.com",
            "unsubscribe_url": url,
            "manage_url": "https://greenroom.test/settings/notifications",
        },
    )
    assert url in rendered.html
    assert url in rendered.text


def test_render_embeds_jsonld_when_provided() -> None:
    """Structured-data context becomes a script tag in the head."""
    jsonld = {
        "@context": "http://schema.org",
        "@type": "EventReservation",
        "reservationStatus": "http://schema.org/Confirmed",
    }
    rendered = email_renderer.render_email(
        template="show_announcement",
        context={
            "preheader": "x",
            "heading": "x",
            "intro": "x",
            "shows": [_show()],
            "cta_label": "View show",
            "cta_url": "https://greenroom.test/x",
            "user_email": "fan@example.com",
            "unsubscribe_url": "https://greenroom.test/unsub",
            "manage_url": "https://greenroom.test/settings/notifications",
            "structured_data": jsonld,
        },
    )
    assert "application/ld+json" in rendered.html
    assert "EventReservation" in rendered.html


def test_render_omits_jsonld_block_when_not_provided() -> None:
    """No structured_data context → no ld+json script tag."""
    rendered = email_renderer.render_email(
        template="show_announcement",
        context={
            "preheader": "x",
            "heading": "x",
            "intro": "x",
            "shows": [_show()],
            "cta_label": "View show",
            "cta_url": "https://greenroom.test/x",
            "user_email": "fan@example.com",
            "unsubscribe_url": "https://greenroom.test/unsub",
            "manage_url": "https://greenroom.test/settings/notifications",
        },
    )
    assert "application/ld+json" not in rendered.html


def test_render_unknown_template_raises() -> None:
    """Asking for a template that doesn't exist is a developer error."""
    with pytest.raises(email_renderer.EmailTemplateNotFoundError):
        email_renderer.render_email(template="not_a_real_template", context={})


def test_render_show_card_partial_renders_inside_email() -> None:
    """The show-card partial renders venue + date inside the message."""
    rendered = email_renderer.render_email(
        template="show_announcement",
        context={
            "preheader": "x",
            "heading": "Phoebe Bridgers",
            "intro": "x",
            "shows": [
                _show(
                    headliner="Phoebe Bridgers",
                    venue="9:30 Club",
                    date_label="Friday · Apr 26 · 8 PM",
                )
            ],
            "cta_label": "View show",
            "cta_url": "https://greenroom.test/x",
            "user_email": "fan@example.com",
            "unsubscribe_url": "https://greenroom.test/unsub",
            "manage_url": "https://greenroom.test/settings/notifications",
        },
    )
    assert "9:30 Club" in rendered.html
    assert "9:30 Club" in rendered.text
    assert "Friday · Apr 26 · 8 PM" in rendered.html


def test_plain_text_strips_html() -> None:
    """Plain-text variant must not carry residual HTML markup."""
    rendered = email_renderer.render_email(
        template="show_announcement",
        context={
            "preheader": "x",
            "heading": "Heads up",
            "intro": "Tickets dropped <b>today</b>.",
            "shows": [_show()],
            "cta_label": "View show",
            "cta_url": "https://greenroom.test/x",
            "user_email": "fan@example.com",
            "unsubscribe_url": "https://greenroom.test/unsub",
            "manage_url": "https://greenroom.test/settings/notifications",
        },
    )
    # Tags introduced by the user-provided intro must be escaped or
    # absent — never raw — so plain-text clients don't render them.
    assert "<b>" not in rendered.text
    assert "</b>" not in rendered.text


def _show(
    *,
    headliner: str = "Phoebe Bridgers",
    venue: str = "9:30 Club",
    date_label: str = "Friday · Apr 26 · 8 PM",
) -> dict[str, str]:
    """Build a show-card context dict with sensible defaults."""
    return {
        "headliner": headliner,
        "venue": venue,
        "date_label": date_label,
        "image_url": "https://example.com/img.jpg",
        "url": "https://greenroom.test/events/abc",
        "starts_at": datetime(2026, 4, 26, 20, 0, tzinfo=UTC).isoformat(),
    }

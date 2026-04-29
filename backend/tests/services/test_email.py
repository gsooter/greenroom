"""Tests for :mod:`backend.services.email`.

Covers two pieces of the send pipeline:

1. :func:`send_email` — the low-level Resend wrapper. Tests pin the
   payload that hits the Resend API: ``from``, ``to``, ``subject``,
   ``html``, ``text``, and (when an unsubscribe URL is supplied) the
   RFC 8058 ``List-Unsubscribe`` and ``List-Unsubscribe-Post`` headers
   that mailbox providers read to render the inbox-level pill.
2. :func:`compose_email` — the high-level helper that bundles
   rendering, token minting, and sending in one call. Tests cover the
   happy path (renderer is invoked, the unsubscribe URL embeds a
   minted token, the resulting bodies plus URL hit ``send_email``).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from backend.core.exceptions import AppError
from backend.services import email as email_service
from backend.services import email_renderer, email_tokens


class _FakeResponse:
    """Minimal stand-in for :class:`requests.Response`."""

    def __init__(self, *, status_code: int = 200, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


# ---------------------------------------------------------------------------
# send_email
# ---------------------------------------------------------------------------


def test_send_email_posts_expected_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Resend POST carries from/to/subject/html/text in the body."""
    captured: dict[str, Any] = {}

    def fake_post(
        url: str, *, json: dict[str, Any], headers: dict[str, str], timeout: int
    ) -> _FakeResponse:
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _FakeResponse(status_code=200)

    monkeypatch.setattr(email_service.requests, "post", fake_post)
    email_service.send_email(
        to="user@example.com",
        subject="hi",
        html_body="<p>hi</p>",
        text_body="hi",
    )

    assert captured["url"] == email_service._RESEND_ENDPOINT
    assert captured["json"]["to"] == ["user@example.com"]
    assert captured["json"]["subject"] == "hi"
    assert captured["json"]["html"] == "<p>hi</p>"
    assert captured["json"]["text"] == "hi"
    assert captured["headers"]["Authorization"].startswith("Bearer ")


def test_send_email_falls_back_to_html_strip_when_text_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing text body is filled in by stripping HTML tags."""
    captured: dict[str, Any] = {}

    def fake_post(*_args: Any, **kwargs: Any) -> _FakeResponse:
        captured["json"] = kwargs["json"]
        return _FakeResponse(status_code=200)

    monkeypatch.setattr(email_service.requests, "post", fake_post)
    email_service.send_email(
        to="user@example.com",
        subject="hi",
        html_body="<p>hello <b>world</b></p>",
    )

    assert captured["json"]["text"] == "hello world"


def test_send_email_attaches_list_unsubscribe_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``unsubscribe_url`` is set, RFC 8058 headers are attached.

    Mailbox providers read these headers to render the inbox-level
    "Unsubscribe" pill. ``List-Unsubscribe-Post: List-Unsubscribe=One-Click``
    is what makes Gmail and Apple Mail pick the gateway POST path
    rather than opening the URL in a browser.
    """
    captured: dict[str, Any] = {}

    def fake_post(*_args: Any, **kwargs: Any) -> _FakeResponse:
        captured["json"] = kwargs["json"]
        return _FakeResponse(status_code=200)

    monkeypatch.setattr(email_service.requests, "post", fake_post)
    email_service.send_email(
        to="user@example.com",
        subject="hi",
        html_body="<p>hi</p>",
        unsubscribe_url="https://greenroom.test/api/v1/unsubscribe?token=abc",
    )

    headers = captured["json"]["headers"]
    assert (
        headers["List-Unsubscribe"]
        == "<https://greenroom.test/api/v1/unsubscribe?token=abc>"
    )
    assert headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


def test_send_email_omits_list_unsubscribe_headers_when_url_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No unsubscribe URL → no List-Unsubscribe header section.

    Resend rejects empty header values, so we omit the key entirely
    when the caller didn't supply a URL (transactional one-offs like
    magic-link emails don't carry unsubscribe semantics).
    """
    captured: dict[str, Any] = {}

    def fake_post(*_args: Any, **kwargs: Any) -> _FakeResponse:
        captured["json"] = kwargs["json"]
        return _FakeResponse(status_code=200)

    monkeypatch.setattr(email_service.requests, "post", fake_post)
    email_service.send_email(
        to="user@example.com",
        subject="hi",
        html_body="<p>hi</p>",
    )

    assert "headers" not in captured["json"]


def test_send_email_raises_on_resend_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-2xx Resend responses surface as EMAIL_DELIVERY_FAILED."""
    monkeypatch.setattr(
        email_service.requests,
        "post",
        lambda *_a, **_k: _FakeResponse(status_code=500, text="boom"),
    )
    with pytest.raises(AppError) as exc:
        email_service.send_email(
            to="user@example.com",
            subject="hi",
            html_body="<p>hi</p>",
        )
    assert exc.value.code == "EMAIL_DELIVERY_FAILED"


# ---------------------------------------------------------------------------
# compose_email
# ---------------------------------------------------------------------------


def test_compose_email_renders_template_and_sends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """compose_email mints a token, renders, and forwards to send_email."""
    user_id = uuid.uuid4()
    rendered = email_renderer.RenderedEmail(html="<p>hi</p>", text="hi")
    captured: dict[str, Any] = {}

    def fake_render(
        template: str, context: dict[str, Any]
    ) -> email_renderer.RenderedEmail:
        captured["template"] = template
        captured["context"] = context
        return rendered

    def fake_send(
        *,
        to: str,
        subject: str,
        html_body: str,
        text_body: str | None = None,
        unsubscribe_url: str | None = None,
    ) -> None:
        captured["to"] = to
        captured["subject"] = subject
        captured["html_body"] = html_body
        captured["text_body"] = text_body
        captured["unsubscribe_url"] = unsubscribe_url

    monkeypatch.setattr(email_service, "render_email", fake_render)
    monkeypatch.setattr(email_service, "send_email", fake_send)

    email_service.compose_email(
        to="user@example.com",
        user_id=user_id,
        subject="Friday picks",
        template="show_announcement",
        scope="staff_picks",
        context={"heading": "Friday picks", "shows": []},
    )

    assert captured["template"] == "show_announcement"
    assert captured["to"] == "user@example.com"
    assert captured["subject"] == "Friday picks"
    assert captured["html_body"] == "<p>hi</p>"
    assert captured["text_body"] == "hi"

    unsubscribe_url = captured["unsubscribe_url"]
    assert unsubscribe_url.startswith("http")
    assert "/api/v1/unsubscribe?token=" in unsubscribe_url
    token = unsubscribe_url.split("token=", 1)[1]
    decoded = email_tokens.verify_unsubscribe_token(token)
    assert decoded.user_id == user_id
    assert decoded.scope == "staff_picks"


def test_compose_email_injects_unsubscribe_url_into_template_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The renderer receives the unsubscribe_url so the footer renders.

    The base templates read ``unsubscribe_url`` directly out of the
    template context — if compose_email forgot to inject it, the
    rendered footer would be empty and the email would ship without
    a clickable unsubscribe link.
    """
    user_id = uuid.uuid4()
    captured: dict[str, Any] = {}

    def fake_render(
        _template: str, context: dict[str, Any]
    ) -> email_renderer.RenderedEmail:
        captured["context"] = context
        return email_renderer.RenderedEmail(html="x", text="x")

    monkeypatch.setattr(email_service, "render_email", fake_render)
    monkeypatch.setattr(email_service, "send_email", lambda **_k: None)

    email_service.compose_email(
        to="user@example.com",
        user_id=user_id,
        subject="s",
        template="show_announcement",
        scope="all",
        context={"heading": "h"},
    )

    ctx = captured["context"]
    assert "unsubscribe_url" in ctx
    assert "/api/v1/unsubscribe?token=" in ctx["unsubscribe_url"]
    assert ctx["heading"] == "h"  # caller-supplied keys are preserved


def test_compose_email_uses_scope_specific_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each compose call mints a token for the scope it was given."""
    user_id = uuid.uuid4()
    captured: dict[str, Any] = {}

    def fake_render(
        _template: str, context: dict[str, Any]
    ) -> email_renderer.RenderedEmail:
        captured["context"] = context
        return email_renderer.RenderedEmail(html="x", text="x")

    monkeypatch.setattr(email_service, "render_email", fake_render)
    monkeypatch.setattr(email_service, "send_email", lambda **_k: None)

    email_service.compose_email(
        to="user@example.com",
        user_id=user_id,
        subject="weekly",
        template="show_announcement",
        scope="weekly_digest",
        context={},
    )

    token = captured["context"]["unsubscribe_url"].split("token=", 1)[1]
    decoded = email_tokens.verify_unsubscribe_token(token)
    assert decoded.scope == "weekly_digest"

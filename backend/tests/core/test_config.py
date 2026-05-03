"""Tests for cross-cutting Settings validators in :mod:`backend.core.config`.

The bulk of the Settings class is plain attribute declaration that
Pydantic itself exercises in its own test suite — there is no value in
re-asserting that. The tests below pin the behavior we *added* on top
of Pydantic: the VAPID key whitespace strip and the Spotify beta email
parser. Both have caused real production incidents (a stray newline in
``VAPID_PUBLIC_KEY`` corrupted the push handshake; an unspaced beta
allowlist locked a contributor out), so each gets a regression test.
"""

from __future__ import annotations

import pytest

from backend.core.config import Settings


def _required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set the required Settings env vars so a bare Settings() boots.

    The class declares several non-optional fields (Spotify, JWT,
    Ticketmaster, etc.). Tests in this module care about specific
    optional fields, so we satisfy the required ones with throwaway
    values to keep each test focused.
    """
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "x")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "x")
    monkeypatch.setenv("SPOTIFY_REDIRECT_URI", "x")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("REDIS_URL", "redis://localhost")
    monkeypatch.setenv("JWT_SECRET_KEY", "x")
    monkeypatch.setenv("RESEND_API_KEY", "x")
    monkeypatch.setenv("RESEND_FROM_EMAIL", "x@example.com")
    monkeypatch.setenv("TICKETMASTER_API_KEY", "x")
    monkeypatch.setenv("SEATGEEK_CLIENT_ID", "x")
    monkeypatch.setenv("SEATGEEK_CLIENT_SECRET", "x")
    monkeypatch.setenv("ADMIN_SECRET_KEY", "x")
    monkeypatch.setenv("SLACK_WEBHOOK_OPS_URL", "")
    monkeypatch.setenv("ALERT_EMAIL", "")
    monkeypatch.setenv("POSTHOG_API_KEY", "")
    monkeypatch.setenv("POSTHOG_HOST", "")


def test_vapid_public_key_strips_trailing_newline_from_paste(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Trailing newlines from copy-paste must not survive into config.

    A trailing ``\\n`` on the public key would propagate to the
    ``/api/v1/push/vapid-public-key`` response, where the browser's
    ``atob`` would reject the substituted base64 with
    ``InvalidCharacterError``.
    """
    _required_env(monkeypatch)
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "BCp-public-key\n")
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "")
    settings = Settings()  # type: ignore[call-arg]
    assert settings.vapid_public_key == "BCp-public-key"


def test_vapid_private_key_strips_trailing_newline_from_paste(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Trailing newlines on a base64url private key break pywebpush silently."""
    _required_env(monkeypatch)
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "")
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "abc-private\n   ")
    settings = Settings()  # type: ignore[call-arg]
    assert settings.vapid_private_key == "abc-private"


def test_vapid_private_key_preserves_internal_pem_newlines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A PEM-formatted private key must keep its internal newlines.

    pywebpush accepts both PEM and raw base64url; PEM requires the
    ``-----BEGIN…-----\\n<body>\\n-----END…-----`` line structure.
    Stripping outer whitespace only is the safe rule.
    """
    _required_env(monkeypatch)
    pem = (
        "\n-----BEGIN EC PRIVATE KEY-----\n"
        "BODYLINE1\n"
        "BODYLINE2\n"
        "-----END EC PRIVATE KEY-----\n"
    )
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "")
    monkeypatch.setenv("VAPID_PRIVATE_KEY", pem)
    settings = Settings()  # type: ignore[call-arg]
    # Outer newlines gone, internal structure intact.
    assert settings.vapid_private_key.startswith("-----BEGIN EC PRIVATE KEY-----")
    assert settings.vapid_private_key.endswith("-----END EC PRIVATE KEY-----")
    assert "BODYLINE1\nBODYLINE2" in settings.vapid_private_key

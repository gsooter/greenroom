"""Unit tests for :mod:`backend.services.email_tokens`.

Every email Greenroom sends carries a token in its unsubscribe URL.
That token is the only thing standing between the recipient and
mass-pause/unsubscribe abuse, so the test surface here is deliberately
strict: round-trip, expiry, tampering, scope mismatch, and malformed
input are each isolated to their own test.
"""

from __future__ import annotations

import hmac
import time
import uuid
from typing import Any

import pytest

from backend.core.exceptions import ValidationError
from backend.services import email_tokens


def test_mint_and_verify_round_trip() -> None:
    """A freshly-minted token verifies for the same user and scope."""
    user_id = uuid.uuid4()
    token = email_tokens.mint_unsubscribe_token(user_id, "weekly_digest")
    decoded = email_tokens.verify_unsubscribe_token(
        token, expected_scope="weekly_digest"
    )
    assert decoded.user_id == user_id
    assert decoded.scope == "weekly_digest"


def test_mint_returns_url_safe_token() -> None:
    """Tokens must be URL-safe so they can drop into a query string."""
    token = email_tokens.mint_unsubscribe_token(uuid.uuid4(), "all")
    # The token is a single header.payload.signature triple — no '+',
    # no '/', no '=' padding once base64url-encoded.
    for forbidden in (" ", "+", "/", "\n"):
        assert forbidden not in token


def test_verify_rejects_tampered_signature() -> None:
    """Flipping a bit in the signature breaks verification."""
    token = email_tokens.mint_unsubscribe_token(uuid.uuid4(), "all")
    # The signature is the third dot-segment.
    head, body, sig = token.split(".")
    flipped = sig[:-1] + ("a" if sig[-1] != "a" else "b")
    tampered = f"{head}.{body}.{flipped}"
    with pytest.raises(ValidationError):
        email_tokens.verify_unsubscribe_token(tampered)


def test_verify_rejects_tampered_payload() -> None:
    """Editing the payload after signing fails verification."""
    token = email_tokens.mint_unsubscribe_token(uuid.uuid4(), "all")
    head, _body, sig = token.split(".")
    # Replace the payload with another valid base64url-encoded JSON
    # blob — same shape, different user_id.
    other = email_tokens.mint_unsubscribe_token(uuid.uuid4(), "all")
    _other_head, other_body, _other_sig = other.split(".")
    forged = f"{head}.{other_body}.{sig}"
    with pytest.raises(ValidationError):
        email_tokens.verify_unsubscribe_token(forged)


def test_verify_rejects_expired_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tokens older than the configured TTL are rejected."""
    user_id = uuid.uuid4()
    # Pretend the token was minted 100 days ago — comfortably past the
    # 90-day TTL.
    real_time = time.time
    monkeypatch.setattr(email_tokens, "_now", lambda: real_time() - 100 * 86400)
    token = email_tokens.mint_unsubscribe_token(user_id, "all")
    monkeypatch.setattr(email_tokens, "_now", real_time)
    with pytest.raises(ValidationError):
        email_tokens.verify_unsubscribe_token(token)


def test_verify_rejects_scope_mismatch() -> None:
    """Token minted for one scope must not validate against another."""
    token = email_tokens.mint_unsubscribe_token(uuid.uuid4(), "weekly_digest")
    with pytest.raises(ValidationError):
        email_tokens.verify_unsubscribe_token(token, expected_scope="staff_picks")


def test_verify_accepts_any_scope_when_no_expectation() -> None:
    """Without a constraint, any scope decodes successfully."""
    token = email_tokens.mint_unsubscribe_token(uuid.uuid4(), "weekly_digest")
    decoded = email_tokens.verify_unsubscribe_token(token)
    assert decoded.scope == "weekly_digest"


@pytest.mark.parametrize("garbage", ["", "abc", "a.b", "a.b.c.d", "...", "@@@@"])
def test_verify_rejects_malformed_token(garbage: str) -> None:
    """Anything that doesn't parse cleanly is rejected before HMAC."""
    with pytest.raises(ValidationError):
        email_tokens.verify_unsubscribe_token(garbage)


def test_verify_rejects_unknown_scope() -> None:
    """Mint refuses to issue a token for a scope it doesn't recognise."""
    with pytest.raises(ValueError):
        email_tokens.mint_unsubscribe_token(uuid.uuid4(), "made_up_field")  # type: ignore[arg-type]


def test_mint_uses_explicit_secret_when_email_token_secret_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When EMAIL_TOKEN_SECRET is set we sign with that, not jwt secret."""
    fake_settings = _Settings(
        jwt_secret_key="jwt-secret",
        email_token_secret="email-secret",
    )
    monkeypatch.setattr(email_tokens, "get_settings", lambda: fake_settings)
    token = email_tokens.mint_unsubscribe_token(uuid.uuid4(), "all")
    # Reaching into the implementation: the signing key should be the
    # email_token_secret, not jwt_secret_key. We verify by recomputing.
    head, body, sig = token.split(".")
    expected = email_tokens._sign(f"{head}.{body}", "email-secret")
    assert hmac.compare_digest(sig, expected)


def test_mint_falls_back_to_jwt_secret_when_email_secret_blank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty email_token_secret means we sign with jwt_secret_key."""
    fake_settings = _Settings(jwt_secret_key="jwt-secret", email_token_secret="")
    monkeypatch.setattr(email_tokens, "get_settings", lambda: fake_settings)
    token = email_tokens.mint_unsubscribe_token(uuid.uuid4(), "all")
    head, body, sig = token.split(".")
    expected = email_tokens._sign(f"{head}.{body}", "jwt-secret")
    assert hmac.compare_digest(sig, expected)


class _Settings:
    """Shape-compatible stand-in for backend.core.config.Settings.

    The real Settings class loads every env var at import time;
    constructing a stub keeps the tests independent of the test
    environment's secrets.
    """

    def __init__(self, **values: Any) -> None:
        for key, value in values.items():
            setattr(self, key, value)

"""Unit tests for :mod:`backend.core.auth`.

JWT encode/decode is a pure function, so these tests need no database
or Flask app. Decorator-integration tests that exercise
``@require_auth`` live alongside the API tests in ``tests/api/v1``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from backend.core import auth
from backend.core.config import get_settings
from backend.core.exceptions import (
    INVALID_TOKEN,
    TOKEN_EXPIRED,
    AppError,
)


def test_issue_token_then_verify_roundtrips_the_user_id() -> None:
    """A freshly issued token decodes back to the same user UUID."""
    user_id = uuid.uuid4()
    token = auth.issue_token(user_id)
    assert auth.verify_token(token) == user_id


def test_issue_token_embeds_exp_and_iat() -> None:
    """The encoded token carries ``iat`` and ``exp`` claims we can inspect."""
    user_id = uuid.uuid4()
    token = auth.issue_token(user_id)
    settings = get_settings()
    payload = jwt.decode(
        token, settings.jwt_secret_key, algorithms=["HS256"]
    )
    assert payload["sub"] == str(user_id)
    assert payload["exp"] - payload["iat"] == settings.jwt_expiry_seconds


def test_verify_token_raises_token_expired_for_past_exp() -> None:
    """Expired tokens surface as ``TOKEN_EXPIRED`` / HTTP 401."""
    settings = get_settings()
    past = datetime.now(timezone.utc) - timedelta(seconds=60)
    token = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "iat": int((past - timedelta(seconds=60)).timestamp()),
            "exp": int(past.timestamp()),
        },
        settings.jwt_secret_key,
        algorithm="HS256",
    )

    with pytest.raises(AppError) as excinfo:
        auth.verify_token(token)
    assert excinfo.value.code == TOKEN_EXPIRED
    assert excinfo.value.status_code == 401


def test_verify_token_raises_invalid_token_for_bad_signature() -> None:
    """A token signed with the wrong key is rejected as INVALID_TOKEN."""
    token = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "iat": 0,
            "exp": 9999999999,
        },
        "not-the-real-secret",
        algorithm="HS256",
    )
    with pytest.raises(AppError) as excinfo:
        auth.verify_token(token)
    assert excinfo.value.code == INVALID_TOKEN


def test_verify_token_raises_invalid_token_when_sub_missing() -> None:
    """Tokens without a ``sub`` claim are rejected."""
    settings = get_settings()
    token = jwt.encode(
        {"iat": 0, "exp": 9999999999},
        settings.jwt_secret_key,
        algorithm="HS256",
    )
    with pytest.raises(AppError) as excinfo:
        auth.verify_token(token)
    assert excinfo.value.code == INVALID_TOKEN


def test_verify_token_raises_invalid_token_when_sub_not_uuid() -> None:
    """Tokens whose ``sub`` is not a UUID string are rejected."""
    settings = get_settings()
    token = jwt.encode(
        {"sub": "not-a-uuid", "iat": 0, "exp": 9999999999},
        settings.jwt_secret_key,
        algorithm="HS256",
    )
    with pytest.raises(AppError) as excinfo:
        auth.verify_token(token)
    assert excinfo.value.code == INVALID_TOKEN


def test_verify_token_raises_invalid_token_for_garbage() -> None:
    """A syntactically broken token string surfaces as INVALID_TOKEN."""
    with pytest.raises(AppError) as excinfo:
        auth.verify_token("this-is-not-a-jwt")
    assert excinfo.value.code == INVALID_TOKEN

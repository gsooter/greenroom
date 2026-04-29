"""Unit tests for :mod:`backend.services.unsubscribe`.

The service is the bridge between an unsubscribe token (arriving via
the public endpoint) and a concrete change to a user's notification
preferences. Tests lock down the routing logic: ``"all"`` scope flows
through ``pause_all_emails``, while a per-type scope toggles exactly
that one boolean column on the prefs row and nothing else.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.core.exceptions import ValidationError
from backend.services import email_tokens
from backend.services import unsubscribe as unsub_service


def test_unsubscribe_with_all_scope_calls_pause_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An ``all``-scoped token routes through pause_all_emails."""
    user_id = uuid.uuid4()
    token = email_tokens.mint_unsubscribe_token(user_id, "all")
    captured: dict[str, Any] = {}

    def fake_pause(_session: Any, uid: uuid.UUID) -> str:
        captured["pause_user"] = uid
        return "paused-row"

    def fake_update(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("update_preferences_for_user must not be called")

    monkeypatch.setattr(unsub_service.prefs_service, "pause_all_emails", fake_pause)
    monkeypatch.setattr(
        unsub_service.prefs_service, "update_preferences_for_user", fake_update
    )

    result = unsub_service.unsubscribe_with_token(MagicMock(), token)
    assert captured["pause_user"] == user_id
    assert result.user_id == user_id
    assert result.scope == "all"


def test_unsubscribe_with_per_type_scope_patches_single_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``weekly_digest`` token flips exactly that field to False."""
    user_id = uuid.uuid4()
    token = email_tokens.mint_unsubscribe_token(user_id, "weekly_digest")
    captured: dict[str, Any] = {}

    def fake_update(_session: Any, uid: uuid.UUID, patch: dict[str, Any]) -> str:
        captured["uid"] = uid
        captured["patch"] = patch
        return "updated-row"

    def fake_pause(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("pause_all_emails must not be called for per-type scope")

    monkeypatch.setattr(unsub_service.prefs_service, "pause_all_emails", fake_pause)
    monkeypatch.setattr(
        unsub_service.prefs_service, "update_preferences_for_user", fake_update
    )

    unsub_service.unsubscribe_with_token(MagicMock(), token)
    assert captured["uid"] == user_id
    assert captured["patch"] == {"weekly_digest": False}


def test_unsubscribe_rejects_tampered_token() -> None:
    """A bad signature surfaces as a ValidationError before any DB hit."""
    with pytest.raises(ValidationError):
        unsub_service.unsubscribe_with_token(MagicMock(), "garbage.token.here")


def test_unsubscribe_is_idempotent_across_repeats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Calling twice with the same token applies the same patch twice.

    The endpoint contract is "the user wants this off"; the second
    click is a no-op at the DB level (boolean toggle to False when
    already False), but the service must not refuse the second call.
    """
    user_id = uuid.uuid4()
    token = email_tokens.mint_unsubscribe_token(user_id, "weekly_digest")
    calls: list[dict[str, Any]] = []

    def fake_update(_s: Any, _uid: uuid.UUID, patch: dict[str, Any]) -> str:
        calls.append(patch)
        return "row"

    monkeypatch.setattr(
        unsub_service.prefs_service, "update_preferences_for_user", fake_update
    )
    monkeypatch.setattr(
        unsub_service.prefs_service,
        "pause_all_emails",
        lambda *_a, **_k: pytest.fail("must not pause"),
    )

    unsub_service.unsubscribe_with_token(MagicMock(), token)
    unsub_service.unsubscribe_with_token(MagicMock(), token)
    assert calls == [
        {"weekly_digest": False},
        {"weekly_digest": False},
    ]

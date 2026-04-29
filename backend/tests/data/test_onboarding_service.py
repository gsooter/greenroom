"""Unit tests for :mod:`backend.services.onboarding`.

Uses the real Postgres test database via the ``session`` fixture so the
composition of service-level orchestration and repo-level SQL is
covered end-to-end.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from backend.core.exceptions import ValidationError
from backend.data.models.users import User
from backend.data.repositories import onboarding as onboarding_repo
from backend.services import onboarding as onboarding_service


def test_get_state_lazy_creates_row(
    session: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    state = onboarding_service.get_state(session, user)
    assert state.user_id == user.id
    assert state.taste_completed_at is None


def test_mark_step_complete_stamps_and_is_idempotent(
    session: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    state = onboarding_service.mark_step_complete(session, user, "taste")
    assert state.taste_completed_at is not None
    stamp = state.taste_completed_at

    # Calling again must not move the timestamp.
    state = onboarding_service.mark_step_complete(session, user, "taste")
    assert state.taste_completed_at == stamp


def test_mark_step_complete_rejects_unknown_step(
    session: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    with pytest.raises(ValidationError):
        onboarding_service.mark_step_complete(session, user, "profile")


def test_mark_skipped_entirely_stamps_every_step_and_skipped_at(
    session: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    state = onboarding_service.mark_skipped_entirely(session, user)
    assert state.skipped_entirely_at is not None
    assert state.taste_completed_at is not None
    assert state.venues_completed_at is not None
    assert state.music_services_completed_at is not None
    assert state.passkey_completed_at is not None


def test_increment_browse_sessions_no_op_without_skip(
    session: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    state = onboarding_service.increment_browse_sessions(session, user)
    assert state.browse_sessions_since_skipped == 0


def test_increment_browse_sessions_counts_after_skip(
    session: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    onboarding_service.mark_skipped_entirely(session, user)
    for _ in range(3):
        onboarding_service.increment_browse_sessions(session, user)
    state = onboarding_service.get_state(session, user)
    assert state.browse_sessions_since_skipped == 3


def test_dismiss_banner_stamps_dismissed_at(
    session: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    state = onboarding_service.dismiss_banner(session, user)
    assert state.banner_dismissed_at is not None


def test_serialize_state_fresh_user(
    session: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    state = onboarding_service.get_state(session, user)
    payload = onboarding_service.serialize_state(state)
    assert payload["steps"] == {
        "taste": False,
        "venues": False,
        "music_services": False,
        "passkey": False,
    }
    assert payload["completed"] is False
    assert payload["skipped_entirely_at"] is None
    assert payload["banner"] == {
        "visible": False,
        "dismissed_at": None,
        "browse_sessions_since_skipped": 0,
    }


def test_serialize_state_all_complete(
    session: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    for step in ("taste", "venues", "music_services", "passkey"):
        onboarding_service.mark_step_complete(session, user, step)
    state = onboarding_service.get_state(session, user)
    payload = onboarding_service.serialize_state(state)
    assert payload["completed"] is True
    assert all(payload["steps"].values())


def test_serialize_state_banner_visible_after_skip(
    session: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    onboarding_service.mark_skipped_entirely(session, user)
    state = onboarding_service.get_state(session, user)
    payload = onboarding_service.serialize_state(state)
    assert payload["banner"]["visible"] is True
    assert payload["skipped_entirely_at"] is not None


def test_serialize_state_banner_hides_after_dismiss(
    session: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    onboarding_service.mark_skipped_entirely(session, user)
    onboarding_service.dismiss_banner(session, user)
    state = onboarding_service.get_state(session, user)
    payload = onboarding_service.serialize_state(state)
    assert payload["banner"]["visible"] is False


def test_serialize_state_banner_hides_after_seven_sessions(
    session: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    onboarding_service.mark_skipped_entirely(session, user)
    for _ in range(7):
        onboarding_service.increment_browse_sessions(session, user)
    state = onboarding_service.get_state(session, user)
    payload = onboarding_service.serialize_state(state)
    assert payload["banner"]["visible"] is False


def test_serialize_state_emits_iso_timestamps(
    session: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    stamp = datetime.now(UTC) - timedelta(minutes=5)
    onboarding_repo.mark_skipped_entirely(session, user.id, now=stamp)
    onboarding_repo.dismiss_banner(session, user.id, now=stamp)
    state = onboarding_service.get_state(session, user)
    payload = onboarding_service.serialize_state(state)
    # Stored value round-trips as ISO.
    assert payload["skipped_entirely_at"].startswith(
        stamp.replace(microsecond=0).isoformat()[:10]
    )
    assert payload["banner"]["dismissed_at"] is not None

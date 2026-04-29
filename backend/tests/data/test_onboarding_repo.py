"""Repository tests for :mod:`backend.data.repositories.onboarding`.

Runs against the ``greenroom_test`` Postgres database using the
transactional fixture in ``conftest.py`` — every write is rolled back
on teardown.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from backend.data.models.users import User
from backend.data.repositories import onboarding as onboarding_repo


def test_get_or_create_state_creates_row_on_first_call(
    session: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    state = onboarding_repo.get_or_create_state(session, user.id)
    assert state.user_id == user.id
    assert state.taste_completed_at is None
    assert state.venues_completed_at is None
    assert state.music_services_completed_at is None
    assert state.passkey_completed_at is None
    assert state.skipped_entirely_at is None
    assert state.banner_dismissed_at is None
    assert state.browse_sessions_since_skipped == 0


def test_get_or_create_state_is_idempotent(
    session: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    first = onboarding_repo.get_or_create_state(session, user.id)
    second = onboarding_repo.get_or_create_state(session, user.id)
    assert first.user_id == second.user_id
    # Only one row should exist for this user.
    assert second.created_at == first.created_at


@pytest.mark.parametrize(
    ("step", "column"),
    [
        ("taste", "taste_completed_at"),
        ("venues", "venues_completed_at"),
        ("music_services", "music_services_completed_at"),
        ("passkey", "passkey_completed_at"),
    ],
)
def test_mark_step_complete_stamps_the_right_column(
    session: Session,
    make_user: Callable[..., User],
    step: str,
    column: str,
) -> None:
    user = make_user()
    stamp = datetime(2026, 4, 20, tzinfo=UTC)
    state = onboarding_repo.mark_step_complete(
        session,
        user.id,
        step,
        now=stamp,  # type: ignore[arg-type]
    )
    assert getattr(state, column) == stamp
    # Only the one column is set.
    for other in (
        "taste_completed_at",
        "venues_completed_at",
        "music_services_completed_at",
        "passkey_completed_at",
    ):
        if other != column:
            assert getattr(state, other) is None


def test_mark_step_complete_is_idempotent(
    session: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    first_stamp = datetime(2026, 4, 20, tzinfo=UTC)
    onboarding_repo.mark_step_complete(session, user.id, "taste", now=first_stamp)
    # Repeat call a day later — original timestamp should stick.
    later_stamp = datetime(2026, 4, 21, tzinfo=UTC)
    state = onboarding_repo.mark_step_complete(
        session, user.id, "taste", now=later_stamp
    )
    assert state.taste_completed_at == first_stamp


def test_mark_skipped_entirely_stamps_and_is_idempotent(
    session: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    stamp = datetime(2026, 4, 20, tzinfo=UTC)
    state = onboarding_repo.mark_skipped_entirely(session, user.id, now=stamp)
    assert state.skipped_entirely_at == stamp

    later = datetime(2026, 5, 1, tzinfo=UTC)
    state = onboarding_repo.mark_skipped_entirely(session, user.id, now=later)
    assert state.skipped_entirely_at == stamp


def test_dismiss_banner_stamps_and_is_idempotent(
    session: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    stamp = datetime(2026, 4, 20, tzinfo=UTC)
    state = onboarding_repo.dismiss_banner(session, user.id, now=stamp)
    assert state.banner_dismissed_at == stamp

    later = datetime(2026, 5, 1, tzinfo=UTC)
    state = onboarding_repo.dismiss_banner(session, user.id, now=later)
    assert state.banner_dismissed_at == stamp


def test_increment_browse_sessions_only_counts_after_skip(
    session: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    # Not yet skipped — increment is a no-op.
    state = onboarding_repo.increment_browse_sessions(session, user.id)
    assert state.browse_sessions_since_skipped == 0

    onboarding_repo.mark_skipped_entirely(session, user.id)
    onboarding_repo.increment_browse_sessions(session, user.id)
    onboarding_repo.increment_browse_sessions(session, user.id)
    state = onboarding_repo.increment_browse_sessions(session, user.id)
    assert state.browse_sessions_since_skipped == 3


def test_is_onboarding_complete_true_when_all_steps_stamped(
    session: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    state = onboarding_repo.get_or_create_state(session, user.id)
    assert onboarding_repo.is_onboarding_complete(state) is False

    for step in ("taste", "venues", "music_services", "passkey"):
        onboarding_repo.mark_step_complete(session, user.id, step)  # type: ignore[arg-type]
    state = onboarding_repo.get_or_create_state(session, user.id)
    assert onboarding_repo.is_onboarding_complete(state) is True


def test_list_states_for_users_batch_fetches(
    session: Session, make_user: Callable[..., User]
) -> None:
    one = make_user()
    two = make_user()
    three = make_user()  # Never has a state row created.
    onboarding_repo.get_or_create_state(session, one.id)
    onboarding_repo.get_or_create_state(session, two.id)

    result = onboarding_repo.list_states_for_users(session, [one.id, two.id, three.id])
    assert set(result.keys()) == {one.id, two.id}


def test_list_states_for_users_empty_input(session: Session) -> None:
    assert onboarding_repo.list_states_for_users(session, []) == {}

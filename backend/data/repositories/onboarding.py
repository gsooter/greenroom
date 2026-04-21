"""Repository for the ``user_onboarding_state`` table.

Reads and writes are keyed by ``user_id``. The row is auto-created on
first read if missing — new user rows that bypass the migration
back-fill (e.g. created after the migration shipped) get populated
lazily so the service layer never has to worry about the ``None`` case.
"""

import uuid
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from backend.data.models.onboarding import UserOnboardingState

OnboardingStep = Literal["taste", "venues", "music_services", "passkey"]

_STEP_COLUMN: dict[OnboardingStep, str] = {
    "taste": "taste_completed_at",
    "venues": "venues_completed_at",
    "music_services": "music_services_completed_at",
    "passkey": "passkey_completed_at",
}


def get_or_create_state(session: Session, user_id: uuid.UUID) -> UserOnboardingState:
    """Fetch the onboarding row for a user, creating it if missing.

    Args:
        session: Active SQLAlchemy session.
        user_id: UUID of the user.

    Returns:
        The existing or newly-created :class:`UserOnboardingState`.
    """
    state = session.get(UserOnboardingState, user_id)
    if state is not None:
        return state

    stmt = (
        insert(UserOnboardingState)
        .values(user_id=user_id)
        .on_conflict_do_nothing(index_elements=["user_id"])
    )
    session.execute(stmt)
    session.flush()
    return session.get(UserOnboardingState, user_id)  # type: ignore[return-value]


def mark_step_complete(
    session: Session,
    user_id: uuid.UUID,
    step: OnboardingStep,
    *,
    now: datetime | None = None,
) -> UserOnboardingState:
    """Stamp a single step's ``*_completed_at`` column.

    Idempotent — re-calling for a step that's already complete leaves
    the original timestamp in place.

    Args:
        session: Active SQLAlchemy session.
        user_id: UUID of the user.
        step: Which of the four steps just finished or was skipped.
        now: Optional override for the stamped timestamp, for tests.

    Returns:
        The updated onboarding state row.
    """
    state = get_or_create_state(session, user_id)
    column = _STEP_COLUMN[step]
    if getattr(state, column) is None:
        setattr(state, column, now or datetime.now(UTC))
        session.flush()
    return state


def mark_skipped_entirely(
    session: Session,
    user_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> UserOnboardingState:
    """Flag that the user skipped every step and opt them into the banner.

    Args:
        session: Active SQLAlchemy session.
        user_id: UUID of the user.
        now: Optional override for the stamped timestamp, for tests.

    Returns:
        The updated onboarding state row.
    """
    state = get_or_create_state(session, user_id)
    if state.skipped_entirely_at is None:
        state.skipped_entirely_at = now or datetime.now(UTC)
        session.flush()
    return state


def dismiss_banner(
    session: Session,
    user_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> UserOnboardingState:
    """Stamp ``banner_dismissed_at`` so the skip banner stops showing.

    Args:
        session: Active SQLAlchemy session.
        user_id: UUID of the user.
        now: Optional override for the stamped timestamp, for tests.

    Returns:
        The updated onboarding state row.
    """
    state = get_or_create_state(session, user_id)
    if state.banner_dismissed_at is None:
        state.banner_dismissed_at = now or datetime.now(UTC)
        session.flush()
    return state


def increment_browse_sessions(
    session: Session, user_id: uuid.UUID
) -> UserOnboardingState:
    """Bump ``browse_sessions_since_skipped`` by one.

    Call once per tab session from browse pages. No-op for users who
    never skipped — the counter only matters when the banner is live.

    Args:
        session: Active SQLAlchemy session.
        user_id: UUID of the user.

    Returns:
        The updated onboarding state row.
    """
    state = get_or_create_state(session, user_id)
    if state.skipped_entirely_at is not None:
        state.browse_sessions_since_skipped += 1
        session.flush()
    return state


def is_onboarding_complete(state: UserOnboardingState) -> bool:
    """Return True when every step has a completion timestamp.

    Args:
        state: Onboarding state row.

    Returns:
        True if all four step timestamps are set.
    """
    return all(getattr(state, column) is not None for column in _STEP_COLUMN.values())


def list_states_for_users(
    session: Session, user_ids: list[uuid.UUID]
) -> dict[uuid.UUID, UserOnboardingState]:
    """Batch-fetch onboarding rows for a set of users.

    Args:
        session: Active SQLAlchemy session.
        user_ids: UUIDs to load state for.

    Returns:
        Dict keyed by user id. Missing users are not present in the
        result.
    """
    if not user_ids:
        return {}
    stmt = select(UserOnboardingState).where(UserOnboardingState.user_id.in_(user_ids))
    rows = session.execute(stmt).scalars().all()
    return {row.user_id: row for row in rows}

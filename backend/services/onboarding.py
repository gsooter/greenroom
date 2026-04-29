"""Business logic for the four-step ``/welcome`` onboarding flow.

Orchestrates the state-machine semantics — step completion, the
"skipped entirely" banner, auto-completion on passkey auth — and owns
the serialization of :class:`UserOnboardingState` for the authenticated
``/me/onboarding`` endpoint. The service layer is what routes call; no
route touches :mod:`backend.data.repositories.onboarding` directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, get_args

from backend.core.exceptions import ValidationError
from backend.data.repositories import onboarding as onboarding_repo
from backend.data.repositories.onboarding import OnboardingStep

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from backend.data.models.onboarding import UserOnboardingState
    from backend.data.models.users import User


_STEP_VALUES: tuple[OnboardingStep, ...] = get_args(OnboardingStep)


def get_state(session: Session, user: User) -> UserOnboardingState:
    """Return the onboarding state row for the authenticated user.

    Lazily inserts a row on first read so callers never see ``None``
    and brand-new users created outside the migration back-fill get
    the same default shape as everyone else.

    Args:
        session: Active SQLAlchemy session.
        user: The authenticated user.

    Returns:
        The user's :class:`UserOnboardingState` row.
    """
    return onboarding_repo.get_or_create_state(session, user.id)


def mark_step_complete(session: Session, user: User, step: str) -> UserOnboardingState:
    """Stamp a single step's completion timestamp.

    Accepts the same step whether the user finished it or skipped it —
    skip semantics are "mark done, save no data", and the preference
    data (genres, follows, music connections, passkey) is written by
    separate endpoints before this one fires.

    Args:
        session: Active SQLAlchemy session.
        user: The authenticated user.
        step: The step identifier. Must be one of the
            :data:`OnboardingStep` literal values.

    Returns:
        The updated onboarding state row.

    Raises:
        ValidationError: If ``step`` is not a recognized step identifier.
    """
    if step not in _STEP_VALUES:
        allowed = ", ".join(_STEP_VALUES)
        raise ValidationError(f"Unknown onboarding step '{step}'. Allowed: {allowed}.")
    return onboarding_repo.mark_step_complete(session, user.id, step)


def mark_skipped_entirely(session: Session, user: User) -> UserOnboardingState:
    """Record that a user bailed out of the whole flow on step 1.

    Also stamps every per-step ``*_completed_at`` so the ``/welcome``
    gate never re-traps the caller. The banner on browse pages reads
    ``skipped_entirely_at`` to decide whether to show.

    Args:
        session: Active SQLAlchemy session.
        user: The authenticated user.

    Returns:
        The updated onboarding state row.
    """
    for step in _STEP_VALUES:
        onboarding_repo.mark_step_complete(session, user.id, step)
    return onboarding_repo.mark_skipped_entirely(session, user.id)


def dismiss_banner(session: Session, user: User) -> UserOnboardingState:
    """Stop showing the skip banner for a user who dismissed it.

    Args:
        session: Active SQLAlchemy session.
        user: The authenticated user.

    Returns:
        The updated onboarding state row.
    """
    return onboarding_repo.dismiss_banner(session, user.id)


def increment_browse_sessions(session: Session, user: User) -> UserOnboardingState:
    """Bump the browse-session counter for banner auto-hide.

    No-op for users who never hit "skip all" — the counter only drives
    the banner's seven-session auto-hide rule.

    Args:
        session: Active SQLAlchemy session.
        user: The authenticated user.

    Returns:
        The updated onboarding state row.
    """
    return onboarding_repo.increment_browse_sessions(session, user.id)


_BANNER_AUTO_HIDE_AFTER_SESSIONS = 7


def serialize_state(state: UserOnboardingState) -> dict[str, Any]:
    """Serialize an onboarding-state row for the API response.

    Returns per-step booleans the UI uses to decide which step to show
    next, plus the banner-eligibility signal so browse pages can render
    it without a second call.

    Args:
        state: The :class:`UserOnboardingState` to serialize.

    Returns:
        Dictionary with ``steps``, ``completed`` flag, ``banner``
        visibility signals, and ISO timestamps for audit.
    """
    steps = {
        "taste": state.taste_completed_at is not None,
        "venues": state.venues_completed_at is not None,
        "music_services": state.music_services_completed_at is not None,
        "passkey": state.passkey_completed_at is not None,
    }
    all_done = all(steps.values())
    banner_visible = (
        state.skipped_entirely_at is not None
        and state.banner_dismissed_at is None
        and state.browse_sessions_since_skipped < _BANNER_AUTO_HIDE_AFTER_SESSIONS
    )
    return {
        "steps": steps,
        "completed": all_done,
        "skipped_entirely_at": (
            state.skipped_entirely_at.isoformat() if state.skipped_entirely_at else None
        ),
        "banner": {
            "visible": banner_visible,
            "dismissed_at": (
                state.banner_dismissed_at.isoformat()
                if state.banner_dismissed_at
                else None
            ),
            "browse_sessions_since_skipped": state.browse_sessions_since_skipped,
        },
    }

"""Repository tests for ``notification_preferences``.

These exercise the queries the email pipeline depends on:

* :func:`list_active_weekly_digest_subscribers` — input to the
  hourly weekly-digest dispatcher. Filters out paused users and any
  user who has flipped the per-type weekly toggle off.

Tests run against the real ``greenroom_test`` Postgres database via
the shared session fixture; each case rolls back on teardown.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from backend.data.models.users import User
from backend.data.repositories import notification_preferences as prefs_repo


def test_list_active_weekly_digest_subscribers_filters_paused(
    session: Session,
    make_user: Callable[..., User],
) -> None:
    """A paused user is excluded even when ``weekly_digest`` is True."""
    active = make_user()
    paused = make_user()

    active_prefs = prefs_repo.get_or_create_for_user(session, active.id)
    active_prefs.weekly_digest = True

    paused_prefs = prefs_repo.get_or_create_for_user(session, paused.id)
    paused_prefs.weekly_digest = True
    paused_prefs.paused_at = datetime(2026, 1, 1, tzinfo=UTC)

    session.flush()

    subscribers = prefs_repo.list_active_weekly_digest_subscribers(session)
    user_ids = {p.user_id for p in subscribers}
    assert active.id in user_ids
    assert paused.id not in user_ids


def test_list_active_weekly_digest_subscribers_filters_unsubscribed(
    session: Session,
    make_user: Callable[..., User],
) -> None:
    """A user with ``weekly_digest=False`` is excluded from the list."""
    opted_in = make_user()
    opted_out = make_user()

    in_prefs = prefs_repo.get_or_create_for_user(session, opted_in.id)
    in_prefs.weekly_digest = True

    out_prefs = prefs_repo.get_or_create_for_user(session, opted_out.id)
    out_prefs.weekly_digest = False

    session.flush()

    subscribers = prefs_repo.list_active_weekly_digest_subscribers(session)
    user_ids = {p.user_id for p in subscribers}
    assert opted_in.id in user_ids
    assert opted_out.id not in user_ids


def test_list_active_weekly_digest_subscribers_returns_empty_list_with_no_rows(
    session: Session,
) -> None:
    """No matching prefs rows → empty list, not None."""
    assert prefs_repo.list_active_weekly_digest_subscribers(session) == []

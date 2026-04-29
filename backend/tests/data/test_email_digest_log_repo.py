"""Repository tests for ``email_digest_log``.

These exercise the queries the send pipeline depends on:

* :func:`count_recent_for_user` — input to the weekly-cap guard.
* :func:`get_most_recent_for_type` — input to the per-type
  idempotency guard so the dispatcher doesn't double-send within
  the same hour.
* :func:`create_log` — happy-path insert with the fields the Resend
  webhook handler will later look up by ``provider_message_id``.

Tests run against the real ``greenroom_test`` Postgres database via
the shared session fixture; each case rolls back on teardown.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from backend.data.models.users import User
from backend.data.repositories import email_digest_log as digest_log_repo


def test_count_recent_for_user_counts_only_inside_window(
    session: Session,
    make_user: Callable[..., User],
) -> None:
    """Rows with ``sent_at < since`` are excluded from the count."""
    user = make_user()
    now = datetime(2026, 4, 26, 12, 0, tzinfo=UTC)

    digest_log_repo.create_log(
        session,
        user_id=user.id,
        digest_type="weekly",
        event_count=5,
        sent_at=now - timedelta(days=1),
    )
    digest_log_repo.create_log(
        session,
        user_id=user.id,
        digest_type="weekly",
        event_count=4,
        sent_at=now - timedelta(days=8),  # outside 7-day window
    )

    count = digest_log_repo.count_recent_for_user(
        session, user.id, now - timedelta(days=7)
    )
    assert count == 1


def test_count_recent_for_user_scopes_by_user(
    session: Session,
    make_user: Callable[..., User],
) -> None:
    """Another user's rows must not bleed into the count."""
    user_a = make_user()
    user_b = make_user()
    now = datetime(2026, 4, 26, 12, 0, tzinfo=UTC)

    digest_log_repo.create_log(
        session,
        user_id=user_a.id,
        digest_type="weekly",
        event_count=3,
        sent_at=now - timedelta(hours=1),
    )
    digest_log_repo.create_log(
        session,
        user_id=user_b.id,
        digest_type="weekly",
        event_count=3,
        sent_at=now - timedelta(hours=1),
    )

    assert (
        digest_log_repo.count_recent_for_user(
            session, user_a.id, now - timedelta(days=7)
        )
        == 1
    )
    assert (
        digest_log_repo.count_recent_for_user(
            session, user_b.id, now - timedelta(days=7)
        )
        == 1
    )


def test_count_recent_for_user_returns_zero_for_no_rows(
    session: Session,
    make_user: Callable[..., User],
) -> None:
    """No rows → zero, not None."""
    user = make_user()
    assert (
        digest_log_repo.count_recent_for_user(
            session, user.id, datetime.now(UTC) - timedelta(days=7)
        )
        == 0
    )


def test_get_most_recent_for_type_returns_latest(
    session: Session,
    make_user: Callable[..., User],
) -> None:
    """Of three weekly rows, the most recent ``sent_at`` wins."""
    user = make_user()
    now = datetime(2026, 4, 26, 12, 0, tzinfo=UTC)

    digest_log_repo.create_log(
        session,
        user_id=user.id,
        digest_type="weekly",
        event_count=1,
        sent_at=now - timedelta(days=14),
    )
    digest_log_repo.create_log(
        session,
        user_id=user.id,
        digest_type="weekly",
        event_count=2,
        sent_at=now - timedelta(days=1),
    )
    digest_log_repo.create_log(
        session,
        user_id=user.id,
        digest_type="weekly",
        event_count=3,
        sent_at=now - timedelta(days=7),
    )

    latest = digest_log_repo.get_most_recent_for_type(session, user.id, "weekly")
    assert latest is not None
    assert latest.event_count == 2


def test_get_most_recent_for_type_filters_by_type(
    session: Session,
    make_user: Callable[..., User],
) -> None:
    """A staff_picks log doesn't count when looking up 'weekly'."""
    user = make_user()
    now = datetime(2026, 4, 26, 12, 0, tzinfo=UTC)

    digest_log_repo.create_log(
        session,
        user_id=user.id,
        digest_type="staff_picks",
        event_count=2,
        sent_at=now - timedelta(hours=1),
    )

    assert digest_log_repo.get_most_recent_for_type(session, user.id, "weekly") is None


def test_create_log_persists_metadata_and_message_id(
    session: Session,
    make_user: Callable[..., User],
) -> None:
    """Provider message id and metadata round-trip through the row."""
    user = make_user()
    now = datetime(2026, 4, 26, 12, 0, tzinfo=UTC)

    row = digest_log_repo.create_log(
        session,
        user_id=user.id,
        digest_type="weekly",
        event_count=4,
        sent_at=now,
        provider_message_id="msg_abc",
        metadata_json={"top_event_id": "abc-123"},
    )

    assert row.id is not None
    assert row.user_id == user.id
    assert row.digest_type == "weekly"
    assert row.provider_message_id == "msg_abc"
    assert row.metadata_json == {"top_event_id": "abc-123"}

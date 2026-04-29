"""Repository tests for :mod:`backend.data.repositories.feedback`.

Exercise create + paginated list + the resolve-toggle path against a
real Postgres so the CHECK constraint and indexes are honored.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.orm import Session

from backend.data.models.feedback import FeedbackKind
from backend.data.models.users import User
from backend.data.repositories import feedback as feedback_repo


def test_create_feedback_anonymous_persists_with_defaults(
    session: Session,
) -> None:
    """An anonymous submission persists without user_id or email."""
    row = feedback_repo.create_feedback(
        session,
        message="Site looks great",
        kind=FeedbackKind.GENERAL,
    )
    assert row.id is not None
    assert row.user_id is None
    assert row.email is None
    assert row.kind == FeedbackKind.GENERAL
    assert row.is_resolved is False
    assert row.created_at is not None


def test_create_feedback_with_user_links_to_user_id(
    session: Session,
    make_user: Callable[..., User],
) -> None:
    """A signed-in submission stores user_id and the supplied email."""
    user = make_user()
    row = feedback_repo.create_feedback(
        session,
        message="Calendar is broken on Safari",
        kind=FeedbackKind.BUG,
        user_id=user.id,
        email=user.email,
        page_url="https://greenroom.example/events",
        user_agent="Mozilla/5.0",
    )
    assert row.user_id == user.id
    assert row.email == user.email
    assert row.kind == FeedbackKind.BUG


def test_list_feedback_returns_newest_first_with_total(
    session: Session,
) -> None:
    """list_feedback orders newest first and reports the total count."""
    from datetime import UTC, datetime, timedelta

    base = datetime.now(UTC) - timedelta(hours=1)
    rows = []
    for i, (msg, kind) in enumerate(
        [
            ("first", FeedbackKind.GENERAL),
            ("second", FeedbackKind.BUG),
            ("third", FeedbackKind.FEATURE),
        ]
    ):
        row = feedback_repo.create_feedback(session, message=msg, kind=kind)
        row.created_at = base + timedelta(minutes=i)
        rows.append(row)
    session.flush()

    listed, total = feedback_repo.list_feedback(session, page=1, per_page=10)
    assert total == 3
    assert [r.message for r in listed] == ["third", "second", "first"]


def test_list_feedback_filters_by_kind(session: Session) -> None:
    """A kind filter restricts the query to that kind."""
    feedback_repo.create_feedback(session, message="b1", kind=FeedbackKind.BUG)
    feedback_repo.create_feedback(session, message="g1", kind=FeedbackKind.GENERAL)
    feedback_repo.create_feedback(session, message="b2", kind=FeedbackKind.BUG)

    rows, total = feedback_repo.list_feedback(session, kind=FeedbackKind.BUG)
    assert total == 2
    assert all(r.kind == FeedbackKind.BUG for r in rows)


def test_list_feedback_filters_by_resolved_state(session: Session) -> None:
    """An is_resolved filter scopes to open or resolved rows."""
    open_row = feedback_repo.create_feedback(
        session, message="open", kind=FeedbackKind.GENERAL
    )
    closed_row = feedback_repo.create_feedback(
        session, message="closed", kind=FeedbackKind.GENERAL
    )
    feedback_repo.set_resolved(session, closed_row.id, is_resolved=True)

    open_rows, open_total = feedback_repo.list_feedback(session, is_resolved=False)
    assert open_total == 1
    assert open_rows[0].id == open_row.id

    closed_rows, closed_total = feedback_repo.list_feedback(session, is_resolved=True)
    assert closed_total == 1
    assert closed_rows[0].id == closed_row.id


def test_list_feedback_paginates(session: Session) -> None:
    """Pagination returns slices keyed by page/per_page."""
    for n in range(5):
        feedback_repo.create_feedback(
            session, message=f"msg-{n}", kind=FeedbackKind.GENERAL
        )

    page1, total = feedback_repo.list_feedback(session, page=1, per_page=2)
    page2, _ = feedback_repo.list_feedback(session, page=2, per_page=2)
    page3, _ = feedback_repo.list_feedback(session, page=3, per_page=2)
    assert total == 5
    assert len(page1) == 2
    assert len(page2) == 2
    assert len(page3) == 1


def test_set_resolved_toggles_flag(session: Session) -> None:
    """set_resolved flips the boolean both ways."""
    row = feedback_repo.create_feedback(
        session, message="hi", kind=FeedbackKind.GENERAL
    )
    assert row.is_resolved is False

    updated = feedback_repo.set_resolved(session, row.id, is_resolved=True)
    assert updated is not None
    assert updated.is_resolved is True

    rolled_back = feedback_repo.set_resolved(session, row.id, is_resolved=False)
    assert rolled_back is not None
    assert rolled_back.is_resolved is False


def test_set_resolved_returns_none_when_missing(session: Session) -> None:
    """An unknown id yields None instead of raising."""
    import uuid as _uuid

    result = feedback_repo.set_resolved(session, _uuid.uuid4(), is_resolved=True)
    assert result is None

"""Repository functions for in-app beta feedback submissions.

Backs the persistent feedback widget. Writes are tiny (one freeform
message + ``kind`` toggle); the read paths exist to power the admin
dashboard's paginated list with kind / resolved filters.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, select

from backend.data.models.feedback import Feedback, FeedbackKind

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.orm import Session


def create_feedback(
    session: Session,
    *,
    message: str,
    kind: FeedbackKind,
    user_id: uuid.UUID | None = None,
    email: str | None = None,
    page_url: str | None = None,
    user_agent: str | None = None,
) -> Feedback:
    """Insert a new feedback row and return it.

    The session is flushed but not committed — the caller controls the
    transaction boundary. Repository functions never commit because the
    service layer may need to bundle additional writes with the same
    submission (none today, but the pattern is consistent).

    Args:
        session: Active SQLAlchemy session.
        message: Freeform feedback body. Length validation lives in the
            service layer.
        kind: Submission category from the widget toggle.
        user_id: Submitter's user id when signed in. None for anonymous
            submissions.
        email: Reply-to email captured on submit. May be the account
            email (auto-filled) or a manual entry; nullable.
        page_url: URL the user was viewing when they opened the widget.
        user_agent: Browser UA string at submit time.

    Returns:
        The freshly persisted :class:`Feedback` row with its database
        defaults populated (id, created_at).
    """
    row = Feedback(
        message=message,
        kind=kind,
        user_id=user_id,
        email=email,
        page_url=page_url,
        user_agent=user_agent,
    )
    session.add(row)
    session.flush()
    return row


def list_feedback(
    session: Session,
    *,
    kind: FeedbackKind | None = None,
    is_resolved: bool | None = None,
    page: int = 1,
    per_page: int = 25,
) -> tuple[list[Feedback], int]:
    """Return a paginated list of submissions, newest first.

    Args:
        session: Active SQLAlchemy session.
        kind: Optional kind filter. None means all kinds.
        is_resolved: Optional resolved filter. None means both.
        page: 1-indexed page number.
        per_page: Page size; capped at 100 by the service layer.

    Returns:
        Tuple of (rows on this page, total count across all pages).
    """
    base = select(Feedback)
    if kind is not None:
        base = base.where(Feedback.kind == kind)
    if is_resolved is not None:
        base = base.where(Feedback.is_resolved.is_(is_resolved))

    total = session.execute(
        select(func.count()).select_from(base.subquery())
    ).scalar_one()

    stmt = (
        base.order_by(Feedback.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    rows = list(session.execute(stmt).scalars().all())
    return rows, total


def get_feedback(
    session: Session,
    feedback_id: uuid.UUID,
) -> Feedback | None:
    """Fetch a single feedback row by id.

    Args:
        session: Active SQLAlchemy session.
        feedback_id: UUID of the submission.

    Returns:
        The matching :class:`Feedback` row, or None when not found.
    """
    stmt = select(Feedback).where(Feedback.id == feedback_id)
    return session.execute(stmt).scalar_one_or_none()


def set_resolved(
    session: Session,
    feedback_id: uuid.UUID,
    *,
    is_resolved: bool,
) -> Feedback | None:
    """Toggle the ``is_resolved`` flag on a submission.

    Args:
        session: Active SQLAlchemy session.
        feedback_id: UUID of the submission to update.
        is_resolved: New value for the flag.

    Returns:
        The updated row, or None if no submission with that id exists.
    """
    row = get_feedback(session, feedback_id)
    if row is None:
        return None
    row.is_resolved = is_resolved
    session.flush()
    return row

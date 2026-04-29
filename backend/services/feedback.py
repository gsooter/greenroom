"""Service layer for the in-app beta feedback widget.

Orchestrates the create flow: validate input → write the row → fire a
Slack notification so ops sees the submission in real time. The Slack
delivery is fire-and-forget — a webhook outage must not block or fail
the user's submission.

Read paths used by the admin dashboard live here too. They wrap the
repository functions with input validation and an outbound serializer
so route handlers stay thin.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.core.exceptions import FEEDBACK_NOT_FOUND, NotFoundError, ValidationError
from backend.core.logging import get_logger
from backend.data.models.feedback import Feedback, FeedbackKind
from backend.data.repositories import feedback as feedback_repo
from backend.scraper import notifier

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.orm import Session

    from backend.data.models.users import User

logger = get_logger(__name__)

MAX_MESSAGE_LENGTH = 4000
MAX_EMAIL_LENGTH = 320
MAX_PAGE_URL_LENGTH = 2048
MAX_USER_AGENT_LENGTH = 512
MAX_PER_PAGE = 100

_VALID_KINDS: frozenset[str] = frozenset(k.value for k in FeedbackKind)


def submit_feedback(
    session: Session,
    *,
    message: str,
    kind: str,
    user: User | None,
    email: str | None,
    page_url: str | None = None,
    user_agent: str | None = None,
) -> Feedback:
    """Validate, persist, and Slack-notify a new feedback submission.

    The Slack delivery is best-effort. If the webhook is misconfigured
    or the network call raises, the submission still succeeds — ops
    can always read the row from the admin dashboard later.

    When a logged-in ``user`` is supplied the account email overrides
    any ``email`` value the form sent, so we can trust the address. For
    anonymous submissions the form's email value is used as-is.

    Args:
        session: Active SQLAlchemy session.
        message: Freeform feedback body submitted by the user.
        kind: Raw kind string from the form ("bug" / "feature" /
            "general"). Validated against :class:`FeedbackKind`.
        user: Authenticated submitter, or None for an anonymous post.
        email: Reply-to email from the form (anonymous path) or
            ignored when ``user`` is provided.
        page_url: URL the user was viewing when they opened the widget.
        user_agent: Browser UA string captured at submit time.

    Returns:
        The freshly persisted :class:`Feedback` row.

    Raises:
        ValidationError: If ``message`` is empty or too long, ``kind``
            is not one of the allowed values, or any optional string
            exceeds its column limit.
    """
    cleaned_message = (message or "").strip()
    if not cleaned_message:
        raise ValidationError("Feedback message must not be empty.")
    if len(cleaned_message) > MAX_MESSAGE_LENGTH:
        raise ValidationError(
            f"Feedback message must be {MAX_MESSAGE_LENGTH} characters or fewer."
        )
    if kind not in _VALID_KINDS:
        raise ValidationError("Feedback kind must be one of: bug, feature, general.")

    resolved_email = (user.email if user is not None else email) or None
    if resolved_email is not None and len(resolved_email) > MAX_EMAIL_LENGTH:
        raise ValidationError(f"Email must be {MAX_EMAIL_LENGTH} characters or fewer.")
    if page_url is not None and len(page_url) > MAX_PAGE_URL_LENGTH:
        page_url = page_url[:MAX_PAGE_URL_LENGTH]
    if user_agent is not None and len(user_agent) > MAX_USER_AGENT_LENGTH:
        user_agent = user_agent[:MAX_USER_AGENT_LENGTH]

    row = feedback_repo.create_feedback(
        session,
        message=cleaned_message,
        kind=FeedbackKind(kind),
        user_id=user.id if user is not None else None,
        email=resolved_email,
        page_url=page_url,
        user_agent=user_agent,
    )
    session.commit()

    _fire_slack_notification(row)
    return row


def list_feedback(
    session: Session,
    *,
    kind: str | None = None,
    is_resolved: str | None = None,
    page: int = 1,
    per_page: int = 25,
) -> tuple[list[Feedback], int]:
    """Return a paginated, filterable list of submissions for admins.

    Args:
        session: Active SQLAlchemy session.
        kind: Optional kind filter as a raw string. Validated against
            :class:`FeedbackKind`. None means all kinds.
        is_resolved: Optional ``"true"`` / ``"false"`` filter from the
            query string. Anything else is treated as None ("both").
        page: 1-indexed page number, clamped to >= 1.
        per_page: Page size, clamped to ``MAX_PER_PAGE``.

    Returns:
        Tuple of (rows, total count across all pages).

    Raises:
        ValidationError: If ``kind`` is not one of the allowed values.
    """
    parsed_kind: FeedbackKind | None
    if kind is None:
        parsed_kind = None
    elif kind in _VALID_KINDS:
        parsed_kind = FeedbackKind(kind)
    else:
        raise ValidationError("Feedback kind must be one of: bug, feature, general.")

    parsed_resolved: bool | None
    if is_resolved is None:
        parsed_resolved = None
    elif is_resolved.lower() == "true":
        parsed_resolved = True
    elif is_resolved.lower() == "false":
        parsed_resolved = False
    else:
        parsed_resolved = None

    page = max(1, page)
    per_page = max(1, min(per_page, MAX_PER_PAGE))

    return feedback_repo.list_feedback(
        session,
        kind=parsed_kind,
        is_resolved=parsed_resolved,
        page=page,
        per_page=per_page,
    )


def set_resolved(
    session: Session,
    feedback_id: uuid.UUID,
    *,
    is_resolved: bool,
) -> Feedback:
    """Toggle the ``is_resolved`` flag for an admin triage action.

    Args:
        session: Active SQLAlchemy session.
        feedback_id: UUID of the submission to update.
        is_resolved: New value for the flag.

    Returns:
        The updated :class:`Feedback` row.

    Raises:
        NotFoundError: If no submission with that id exists.
    """
    row = feedback_repo.set_resolved(session, feedback_id, is_resolved=is_resolved)
    if row is None:
        raise NotFoundError(
            code=FEEDBACK_NOT_FOUND,
            message=f"No feedback found with id {feedback_id}",
        )
    session.commit()
    return row


def serialize_feedback(row: Feedback) -> dict[str, Any]:
    """Render a feedback row for the admin dashboard JSON response.

    Args:
        row: Feedback ORM instance to serialize.

    Returns:
        Dictionary safe to drop into a JSON response body.
    """
    return {
        "id": str(row.id),
        "user_id": str(row.user_id) if row.user_id else None,
        "email": row.email,
        "message": row.message,
        "kind": row.kind.value if isinstance(row.kind, FeedbackKind) else row.kind,
        "page_url": row.page_url,
        "user_agent": row.user_agent,
        "is_resolved": row.is_resolved,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _fire_slack_notification(row: Feedback) -> None:
    """Best-effort Slack post for a freshly-created feedback row.

    Wraps :func:`backend.scraper.notifier.send_alert`. The notifier
    already swallows webhook errors and falls back to email; this
    wrapper additionally swallows any exception so a misconfiguration
    of either channel never breaks the user's submit flow.

    The Slack title is shaped to read well in channel: a leading kind
    emoji helps ops triage at a glance without expanding the
    attachment.

    Args:
        row: The freshly persisted feedback row.
    """
    title_emoji = {
        FeedbackKind.BUG: "🐞",
        FeedbackKind.FEATURE: "✨",
        FeedbackKind.GENERAL: "💬",
    }.get(row.kind, "💬")

    title = f"{title_emoji} New {row.kind.value} feedback"
    body_preview = row.message if len(row.message) <= 400 else (row.message[:400] + "…")

    details: dict[str, Any] = {
        "kind": row.kind.value,
        "from": row.email or "anonymous",
        "user_id": str(row.user_id) if row.user_id else "—",
        "page_url": row.page_url or "—",
    }

    try:
        notifier.send_alert(
            title=title,
            message=body_preview,
            severity="info",
            details=details,
            alert_key=None,
            category="feedback",
        )
    except Exception:
        logger.exception("Failed to deliver Slack notification for feedback %s", row.id)

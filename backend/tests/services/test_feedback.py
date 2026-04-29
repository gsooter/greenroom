"""Unit tests for :mod:`backend.services.feedback`.

DB and Slack are mocked. The tests cover input validation, the
auto-fill-from-account-email rule, the Slack fire-and-forget contract,
and the read paths that back the admin dashboard.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.core.exceptions import NotFoundError, ValidationError
from backend.data.models.feedback import FeedbackKind
from backend.services import feedback as service


@dataclass
class _FakeUser:
    """Minimal stand-in for the User ORM in service tests."""

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    email: str = "user@example.com"


@dataclass
class _FakeFeedback:
    """Minimal stand-in for the Feedback ORM with the fields we read."""

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    user_id: uuid.UUID | None = None
    email: str | None = None
    message: str = "hi"
    kind: FeedbackKind = FeedbackKind.GENERAL
    page_url: str | None = None
    user_agent: str | None = None
    is_resolved: bool = False
    created_at: datetime | None = field(default_factory=lambda: datetime.now(UTC))


# ---------------------------------------------------------------------------
# submit_feedback — validation
# ---------------------------------------------------------------------------


def test_submit_feedback_rejects_blank_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty or whitespace-only message raises ValidationError."""
    session = MagicMock()
    with pytest.raises(ValidationError):
        service.submit_feedback(
            session,
            message="   ",
            kind="general",
            user=None,
            email=None,
        )


def test_submit_feedback_rejects_overlong_message() -> None:
    """A message longer than 4000 chars is rejected."""
    session = MagicMock()
    with pytest.raises(ValidationError):
        service.submit_feedback(
            session,
            message="x" * 4001,
            kind="general",
            user=None,
            email=None,
        )


def test_submit_feedback_rejects_unknown_kind() -> None:
    """A kind outside the enum raises ValidationError."""
    session = MagicMock()
    with pytest.raises(ValidationError):
        service.submit_feedback(
            session,
            message="hi",
            kind="rant",
            user=None,
            email=None,
        )


def test_submit_feedback_rejects_overlong_email() -> None:
    """An email longer than the column limit is rejected."""
    session = MagicMock()
    with pytest.raises(ValidationError):
        service.submit_feedback(
            session,
            message="hi",
            kind="general",
            user=None,
            email="a" * 400,
        )


# ---------------------------------------------------------------------------
# submit_feedback — happy paths
# ---------------------------------------------------------------------------


def test_submit_feedback_anonymous_uses_form_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Anonymous submissions store the form-supplied email verbatim."""
    session = MagicMock()
    captured: dict[str, Any] = {}

    def fake_create(_session: Any, **kwargs: Any) -> _FakeFeedback:
        captured.update(kwargs)
        return _FakeFeedback(
            **{
                k: v
                for k, v in kwargs.items()
                if k
                in {"user_id", "email", "message", "kind", "page_url", "user_agent"}
            }
        )

    fired: list[_FakeFeedback] = []
    monkeypatch.setattr(service.feedback_repo, "create_feedback", fake_create)
    monkeypatch.setattr(service, "_fire_slack_notification", fired.append)

    row = service.submit_feedback(
        session,
        message="  app crashes on Safari  ",
        kind="bug",
        user=None,
        email="anon@example.com",
        page_url="https://greenroom.example/events",
        user_agent="ua",
    )

    assert captured["message"] == "app crashes on Safari"
    assert captured["kind"] == FeedbackKind.BUG
    assert captured["user_id"] is None
    assert captured["email"] == "anon@example.com"
    session.commit.assert_called_once()
    assert fired == [row]


def test_submit_feedback_signed_in_overrides_email_with_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``user`` is provided, the account email wins over the form."""
    session = MagicMock()
    user = _FakeUser(email="account@example.com")
    captured: dict[str, Any] = {}

    def fake_create(_session: Any, **kwargs: Any) -> _FakeFeedback:
        captured.update(kwargs)
        return _FakeFeedback()

    monkeypatch.setattr(service.feedback_repo, "create_feedback", fake_create)
    monkeypatch.setattr(service, "_fire_slack_notification", lambda _row: None)

    service.submit_feedback(
        session,
        message="hi",
        kind="general",
        user=user,  # type: ignore[arg-type]
        email="form@example.com",
    )

    assert captured["user_id"] == user.id
    assert captured["email"] == "account@example.com"


def test_submit_feedback_truncates_overlong_optional_strings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Page URL / user agent get truncated to their column limits."""
    session = MagicMock()
    captured: dict[str, Any] = {}

    def fake_create(_session: Any, **kwargs: Any) -> _FakeFeedback:
        captured.update(kwargs)
        return _FakeFeedback()

    monkeypatch.setattr(service.feedback_repo, "create_feedback", fake_create)
    monkeypatch.setattr(service, "_fire_slack_notification", lambda _row: None)

    service.submit_feedback(
        session,
        message="hi",
        kind="general",
        user=None,
        email=None,
        page_url="x" * 3000,
        user_agent="y" * 1000,
    )
    assert len(captured["page_url"]) == service.MAX_PAGE_URL_LENGTH
    assert len(captured["user_agent"]) == service.MAX_USER_AGENT_LENGTH


def test_submit_feedback_slack_failure_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A raised exception inside the Slack helper is swallowed."""
    session = MagicMock()
    monkeypatch.setattr(
        service.feedback_repo,
        "create_feedback",
        lambda _s, **kwargs: _FakeFeedback(),
    )

    def boom(*_args: Any, **_kwargs: Any) -> bool:
        raise RuntimeError("slack down")

    monkeypatch.setattr(service.notifier, "send_alert", boom)

    # Should not raise.
    row = service.submit_feedback(
        session,
        message="hi",
        kind="general",
        user=None,
        email=None,
    )
    assert isinstance(row, _FakeFeedback)


def test_submit_feedback_routes_slack_to_feedback_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Slack send is tagged ``category="feedback"`` for channel routing."""
    session = MagicMock()
    monkeypatch.setattr(
        service.feedback_repo,
        "create_feedback",
        lambda _s, **kwargs: _FakeFeedback(),
    )

    captured: dict[str, Any] = {}

    def fake_send(*_args: Any, **kwargs: Any) -> bool:
        captured.update(kwargs)
        return True

    monkeypatch.setattr(service.notifier, "send_alert", fake_send)

    service.submit_feedback(
        session,
        message="hi",
        kind="general",
        user=None,
        email=None,
    )
    assert captured["category"] == "feedback"


# ---------------------------------------------------------------------------
# list_feedback
# ---------------------------------------------------------------------------


def test_list_feedback_validates_kind() -> None:
    """An invalid kind query raises ValidationError."""
    session = MagicMock()
    with pytest.raises(ValidationError):
        service.list_feedback(session, kind="rant")


def test_list_feedback_translates_resolved_query_strings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``"true"`` / ``"false"`` map to bool; anything else degrades to None."""
    session = MagicMock()
    seen: list[bool | None] = []

    def fake_list(
        _s: Any, *, kind: Any, is_resolved: Any, page: int, per_page: int
    ) -> tuple[list[Any], int]:
        seen.append(is_resolved)
        return [], 0

    monkeypatch.setattr(service.feedback_repo, "list_feedback", fake_list)

    service.list_feedback(session, is_resolved="true")
    service.list_feedback(session, is_resolved="FALSE")
    service.list_feedback(session, is_resolved="banana")
    service.list_feedback(session, is_resolved=None)

    assert seen == [True, False, None, None]


def test_list_feedback_clamps_pagination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """page < 1 → 1, per_page > MAX_PER_PAGE → MAX_PER_PAGE."""
    session = MagicMock()
    seen: dict[str, int] = {}

    def fake_list(
        _s: Any, *, kind: Any, is_resolved: Any, page: int, per_page: int
    ) -> tuple[list[Any], int]:
        seen["page"] = page
        seen["per_page"] = per_page
        return [], 0

    monkeypatch.setattr(service.feedback_repo, "list_feedback", fake_list)
    service.list_feedback(session, page=0, per_page=10_000)
    assert seen == {"page": 1, "per_page": service.MAX_PER_PAGE}


# ---------------------------------------------------------------------------
# set_resolved
# ---------------------------------------------------------------------------


def test_set_resolved_raises_on_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unknown id raises NotFoundError with FEEDBACK_NOT_FOUND."""
    session = MagicMock()
    monkeypatch.setattr(
        service.feedback_repo,
        "set_resolved",
        lambda *_a, **_kw: None,
    )
    with pytest.raises(NotFoundError):
        service.set_resolved(session, uuid.uuid4(), is_resolved=True)


def test_set_resolved_commits_and_returns_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful toggle commits the session and returns the row."""
    session = MagicMock()
    row = _FakeFeedback(is_resolved=True)
    monkeypatch.setattr(
        service.feedback_repo,
        "set_resolved",
        lambda *_a, **_kw: row,
    )
    out = service.set_resolved(session, uuid.uuid4(), is_resolved=True)
    assert out is row
    session.commit.assert_called_once()


# ---------------------------------------------------------------------------
# serialize_feedback
# ---------------------------------------------------------------------------


def test_serialize_feedback_renders_iso_dates_and_ids() -> None:
    """The serializer stringifies UUIDs and ISO-formats created_at."""
    row = _FakeFeedback(
        user_id=uuid.uuid4(),
        email="a@b.test",
        kind=FeedbackKind.BUG,
        is_resolved=True,
    )
    serialized = service.serialize_feedback(row)  # type: ignore[arg-type]
    assert serialized["id"] == str(row.id)
    assert serialized["user_id"] == str(row.user_id)
    assert serialized["kind"] == "bug"
    assert serialized["is_resolved"] is True
    assert isinstance(serialized["created_at"], str)

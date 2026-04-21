"""Unit tests for :mod:`backend.services.venue_comments`.

All database interactions are mocked via monkeypatch; this file
exercises the service's input validation, spam gating, and shaping
logic without touching Postgres.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.core.exceptions import (
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
    ValidationError,
)
from backend.data.models.venue_comments import VenueCommentCategory
from backend.services import venue_comments as service


@dataclass
class _FakeUser:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    created_at: datetime = field(
        default_factory=lambda: datetime.now(UTC) - timedelta(days=30)
    )


@dataclass
class _FakeVenue:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    slug: str = "black-cat"


@dataclass
class _FakeComment:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    venue_id: uuid.UUID = field(default_factory=uuid.uuid4)
    user_id: uuid.UUID | None = field(default_factory=uuid.uuid4)
    category: VenueCommentCategory = VenueCommentCategory.VIBES
    body: str = "Nice spot"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


# ---------------------------------------------------------------------------
# hash_request_ip
# ---------------------------------------------------------------------------


def test_hash_request_ip_is_deterministic_and_salted() -> None:
    """Same IP + same salt → same digest; different IP → different digest."""
    first = service.hash_request_ip("1.2.3.4")
    again = service.hash_request_ip("1.2.3.4")
    other = service.hash_request_ip("5.6.7.8")
    assert first == again
    assert first != other
    assert len(first) == 64  # sha256 hex length


# ---------------------------------------------------------------------------
# list_comments
# ---------------------------------------------------------------------------


def test_list_comments_404s_on_unknown_slug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        service.venues_repo, "get_venue_by_slug", lambda _s, _slug: None
    )
    with pytest.raises(NotFoundError):
        service.list_comments(MagicMock(), "nope")


def test_list_comments_passes_category_and_sort_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    venue = _FakeVenue()
    monkeypatch.setattr(
        service.venues_repo, "get_venue_by_slug", lambda _s, _slug: venue
    )
    captured: dict[str, Any] = {}

    def fake_list(
        _session: Any,
        venue_id: uuid.UUID,
        *,
        category: VenueCommentCategory | None,
        sort: str,
        limit: int,
    ) -> list[Any]:
        captured["venue_id"] = venue_id
        captured["category"] = category
        captured["sort"] = sort
        captured["limit"] = limit
        return []

    monkeypatch.setattr(service.comments_repo, "list_comments_by_venue", fake_list)
    result = service.list_comments(
        MagicMock(), venue.slug, category="tickets", sort="new", limit=5
    )
    assert result == []
    assert captured == {
        "venue_id": venue.id,
        "category": VenueCommentCategory.TICKETS,
        "sort": "new",
        "limit": 5,
    }


def test_list_comments_unknown_sort_falls_back_to_top(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    venue = _FakeVenue()
    monkeypatch.setattr(
        service.venues_repo, "get_venue_by_slug", lambda _s, _slug: venue
    )
    captured: dict[str, Any] = {}

    def fake_list(_s: Any, _v: Any, **kw: Any) -> list[Any]:
        captured.update(kw)
        return []

    monkeypatch.setattr(service.comments_repo, "list_comments_by_venue", fake_list)
    service.list_comments(MagicMock(), venue.slug, sort="nonsense")
    assert captured["sort"] == "top"


def test_list_comments_serializes_rows_and_viewer_votes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    venue = _FakeVenue()
    comment_a = _FakeComment(body="a", category=VenueCommentCategory.TICKETS)
    comment_b = _FakeComment(body="b", category=VenueCommentCategory.VIBES)
    monkeypatch.setattr(
        service.venues_repo, "get_venue_by_slug", lambda _s, _slug: venue
    )
    monkeypatch.setattr(
        service.comments_repo,
        "list_comments_by_venue",
        lambda _s, _v, **_kw: [(comment_a, 3, 1), (comment_b, 0, 0)],
    )
    monkeypatch.setattr(
        service.comments_repo,
        "get_voter_values_for_comments",
        lambda _s, ids, **_kw: {comment_a.id: 1},
    )

    result = service.list_comments(MagicMock(), venue.slug, viewer_user_id=uuid.uuid4())
    assert len(result) == 2
    assert result[0]["id"] == str(comment_a.id)
    assert result[0]["category"] == "tickets"
    assert result[0]["likes"] == 3
    assert result[0]["dislikes"] == 1
    assert result[0]["viewer_vote"] == 1
    assert result[1]["viewer_vote"] is None


def test_list_comments_rejects_unknown_category(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    venue = _FakeVenue()
    monkeypatch.setattr(
        service.venues_repo, "get_venue_by_slug", lambda _s, _slug: venue
    )
    with pytest.raises(ValidationError):
        service.list_comments(MagicMock(), venue.slug, category="bogus")


# ---------------------------------------------------------------------------
# submit_comment
# ---------------------------------------------------------------------------


def test_submit_comment_honeypot_is_silently_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValidationError):
        service.submit_comment(
            MagicMock(),
            venue_slug="black-cat",
            user=_FakeUser(),
            category="vibes",
            body="real body",
            honeypot="spam",
            ip_hash=None,
        )


def test_submit_comment_rejects_too_new_accounts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    brand_new = _FakeUser(created_at=datetime.now(UTC))
    with pytest.raises(ValidationError):
        service.submit_comment(
            MagicMock(),
            venue_slug="black-cat",
            user=brand_new,
            category="vibes",
            body="hello world",
            honeypot=None,
            ip_hash=None,
        )


def test_submit_comment_unauthenticated_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(UnauthorizedError):
        service.submit_comment(
            MagicMock(),
            venue_slug="black-cat",
            user=None,  # type: ignore[arg-type]
            category="vibes",
            body="hello",
            honeypot=None,
            ip_hash=None,
        )


def test_submit_comment_404s_on_unknown_venue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        service.venues_repo, "get_venue_by_slug", lambda _s, _slug: None
    )
    with pytest.raises(NotFoundError):
        service.submit_comment(
            MagicMock(),
            venue_slug="nope",
            user=_FakeUser(),
            category="vibes",
            body="hello",
            honeypot=None,
            ip_hash=None,
        )


def test_submit_comment_validates_body_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    venue = _FakeVenue()
    monkeypatch.setattr(
        service.venues_repo, "get_venue_by_slug", lambda _s, _slug: venue
    )
    with pytest.raises(ValidationError):
        service.submit_comment(
            MagicMock(),
            venue_slug=venue.slug,
            user=_FakeUser(),
            category="vibes",
            body="  ",
            honeypot=None,
            ip_hash=None,
        )
    with pytest.raises(ValidationError):
        service.submit_comment(
            MagicMock(),
            venue_slug=venue.slug,
            user=_FakeUser(),
            category="vibes",
            body="x" * (service.MAX_BODY_LEN + 1),
            honeypot=None,
            ip_hash=None,
        )


def test_submit_comment_happy_path_returns_serialized_dict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    venue = _FakeVenue()
    user = _FakeUser()
    monkeypatch.setattr(
        service.venues_repo, "get_venue_by_slug", lambda _s, _slug: venue
    )
    captured: dict[str, Any] = {}

    def fake_create(_s: Any, **kwargs: Any) -> _FakeComment:
        captured.update(kwargs)
        return _FakeComment(
            venue_id=kwargs["venue_id"],
            user_id=kwargs["user_id"],
            category=kwargs["category"],
            body=kwargs["body"],
        )

    monkeypatch.setattr(service.comments_repo, "create_comment", fake_create)

    result = service.submit_comment(
        MagicMock(),
        venue_slug=venue.slug,
        user=user,
        category="tickets",
        body="  Box office opens at 6  ",
        honeypot=None,
        ip_hash="deadbeef",
    )
    assert captured["venue_id"] == venue.id
    assert captured["user_id"] == user.id
    assert captured["category"] == VenueCommentCategory.TICKETS
    assert captured["body"] == "Box office opens at 6"
    assert captured["ip_hash"] == "deadbeef"
    assert result["category"] == "tickets"
    assert result["likes"] == 0
    assert result["viewer_vote"] is None


# ---------------------------------------------------------------------------
# delete_comment
# ---------------------------------------------------------------------------


def test_delete_comment_requires_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(UnauthorizedError):
        service.delete_comment(
            MagicMock(),
            comment_id=uuid.uuid4(),
            user=None,  # type: ignore[arg-type]
        )


def test_delete_comment_404s_on_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        service.comments_repo, "get_comment_by_id", lambda _s, _cid: None
    )
    with pytest.raises(NotFoundError):
        service.delete_comment(MagicMock(), comment_id=uuid.uuid4(), user=_FakeUser())


def test_delete_comment_rejects_other_users_comment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    comment = _FakeComment(user_id=uuid.uuid4())
    monkeypatch.setattr(
        service.comments_repo, "get_comment_by_id", lambda _s, _cid: comment
    )
    with pytest.raises(ForbiddenError):
        service.delete_comment(MagicMock(), comment_id=comment.id, user=_FakeUser())


def test_delete_comment_author_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _FakeUser()
    comment = _FakeComment(user_id=user.id)
    monkeypatch.setattr(
        service.comments_repo, "get_comment_by_id", lambda _s, _cid: comment
    )
    delete_mock = MagicMock()
    monkeypatch.setattr(service.comments_repo, "delete_comment", delete_mock)
    service.delete_comment(MagicMock(), comment_id=comment.id, user=user)
    delete_mock.assert_called_once()


# ---------------------------------------------------------------------------
# cast_vote
# ---------------------------------------------------------------------------


def test_cast_vote_requires_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(UnauthorizedError):
        service.cast_vote(
            MagicMock(),
            comment_id=uuid.uuid4(),
            value=1,
            user=None,
            session_id=None,
        )


def test_cast_vote_rejects_invalid_value(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError):
        service.cast_vote(
            MagicMock(),
            comment_id=uuid.uuid4(),
            value=2,
            user=_FakeUser(),
            session_id=None,
        )


def test_cast_vote_404s_on_missing_comment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        service.comments_repo, "get_comment_by_id", lambda _s, _cid: None
    )
    with pytest.raises(NotFoundError):
        service.cast_vote(
            MagicMock(),
            comment_id=uuid.uuid4(),
            value=1,
            user=_FakeUser(),
            session_id=None,
        )


def test_cast_vote_zero_clears_vote_and_updates_totals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    comment = _FakeComment()
    monkeypatch.setattr(
        service.comments_repo, "get_comment_by_id", lambda _s, _cid: comment
    )
    clear_mock = MagicMock()
    monkeypatch.setattr(service.comments_repo, "clear_vote", clear_mock)
    monkeypatch.setattr(
        service.comments_repo, "count_votes_for_comment", lambda _s, _cid: (0, 0)
    )
    result = service.cast_vote(
        MagicMock(),
        comment_id=comment.id,
        value=0,
        user=_FakeUser(),
        session_id=None,
    )
    clear_mock.assert_called_once()
    assert result == {"likes": 0, "dislikes": 0, "viewer_vote": None}


def test_cast_vote_positive_upserts_and_reports_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    comment = _FakeComment()
    user = _FakeUser()
    monkeypatch.setattr(
        service.comments_repo, "get_comment_by_id", lambda _s, _cid: comment
    )
    upsert_mock = MagicMock()
    monkeypatch.setattr(service.comments_repo, "upsert_vote", upsert_mock)
    monkeypatch.setattr(
        service.comments_repo, "count_votes_for_comment", lambda _s, _cid: (4, 1)
    )
    result = service.cast_vote(
        MagicMock(), comment_id=comment.id, value=1, user=user, session_id=None
    )
    upsert_mock.assert_called_once()
    kwargs = upsert_mock.call_args.kwargs
    assert kwargs["user_id"] == user.id
    assert kwargs["session_id"] is None
    assert kwargs["value"] == 1
    assert result == {"likes": 4, "dislikes": 1, "viewer_vote": 1}


def test_cast_vote_guest_uses_session_id_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A signed-out voter passes session_id; user_id is not forwarded."""
    comment = _FakeComment()
    monkeypatch.setattr(
        service.comments_repo, "get_comment_by_id", lambda _s, _cid: comment
    )
    captured: dict[str, Any] = {}

    def fake_upsert(_s: Any, **kwargs: Any) -> Any:
        captured.update(kwargs)
        return MagicMock()

    monkeypatch.setattr(service.comments_repo, "upsert_vote", fake_upsert)
    monkeypatch.setattr(
        service.comments_repo, "count_votes_for_comment", lambda _s, _cid: (1, 0)
    )
    service.cast_vote(
        MagicMock(),
        comment_id=comment.id,
        value=1,
        user=None,
        session_id="g1",
    )
    assert captured["user_id"] is None
    assert captured["session_id"] == "g1"

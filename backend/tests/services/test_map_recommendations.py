"""Unit tests for :mod:`backend.services.map_recommendations`.

Database and Apple Maps interactions are mocked via monkeypatch; this
file exercises the service's input validation, place verification
hand-off, auto-suppression trigger, and serialization without
touching Postgres or the real Apple API.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.core.exceptions import (
    PLACE_NOT_VERIFIED,
    AppError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
    ValidationError,
)
from backend.data.models.map_recommendations import MapRecommendationCategory
from backend.services import map_recommendations as service
from backend.services.apple_maps import VerifiedPlace


@dataclass
class _FakeUser:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    created_at: datetime = field(
        default_factory=lambda: datetime.now(UTC) - timedelta(days=30)
    )


@dataclass
class _FakeRec:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    submitter_user_id: uuid.UUID | None = field(default_factory=uuid.uuid4)
    session_id: str | None = None
    place_name: str = "Black Cat"
    place_address: str | None = "1811 14th St NW, Washington, DC"
    latitude: float = 38.9152
    longitude: float = -77.0316
    similarity_score: float = 0.97
    category: MapRecommendationCategory = MapRecommendationCategory.DRINKS
    body: str = "Great spot"
    suppressed_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


def _verified(**overrides: Any) -> VerifiedPlace:
    base = {
        "name": "Black Cat",
        "address": "1811 14th St NW, Washington, DC",
        "latitude": 38.9152,
        "longitude": -77.0316,
        "similarity": 0.97,
    }
    base.update(overrides)
    return VerifiedPlace(**base)


# ---------------------------------------------------------------------------
# hash_request_ip
# ---------------------------------------------------------------------------


def test_hash_request_ip_is_deterministic_and_salted() -> None:
    first = service.hash_request_ip("1.2.3.4")
    again = service.hash_request_ip("1.2.3.4")
    other = service.hash_request_ip("5.6.7.8")
    assert first == again
    assert first != other
    assert len(first) == 64


# ---------------------------------------------------------------------------
# list_recommendations
# ---------------------------------------------------------------------------


def test_list_recommendations_rejects_inverted_bbox() -> None:
    with pytest.raises(ValidationError):
        service.list_recommendations(
            MagicMock(),
            sw_lat=39.0,
            sw_lng=-77.0,
            ne_lat=38.9,
            ne_lng=-76.9,
        )


def test_list_recommendations_rejects_unknown_category(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValidationError):
        service.list_recommendations(
            MagicMock(),
            sw_lat=38.9,
            sw_lng=-77.05,
            ne_lat=38.93,
            ne_lng=-77.01,
            category="bogus",
        )


def test_list_recommendations_passes_filters_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_list(_s: Any, **kwargs: Any) -> list[Any]:
        captured.update(kwargs)
        return []

    monkeypatch.setattr(service.rec_repo, "list_recommendations_in_bounds", fake_list)
    result = service.list_recommendations(
        MagicMock(),
        sw_lat=38.9,
        sw_lng=-77.05,
        ne_lat=38.93,
        ne_lng=-77.01,
        category="food",
        sort="new",
        limit=5,
    )
    assert result == []
    assert captured["category"] == MapRecommendationCategory.FOOD
    assert captured["sort"] == "new"
    assert captured["limit"] == 5


def test_list_recommendations_unknown_sort_falls_back_to_top(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_list(_s: Any, **kw: Any) -> list[Any]:
        captured.update(kw)
        return []

    monkeypatch.setattr(service.rec_repo, "list_recommendations_in_bounds", fake_list)
    service.list_recommendations(
        MagicMock(),
        sw_lat=38.9,
        sw_lng=-77.05,
        ne_lat=38.93,
        ne_lng=-77.01,
        sort="nonsense",
    )
    assert captured["sort"] == "top"


def test_list_recommendations_serializes_rows_and_viewer_votes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rec_a = _FakeRec(body="a", category=MapRecommendationCategory.FOOD)
    rec_b = _FakeRec(body="b", category=MapRecommendationCategory.DRINKS)
    monkeypatch.setattr(
        service.rec_repo,
        "list_recommendations_in_bounds",
        lambda _s, **_kw: [(rec_a, 3, 1), (rec_b, 0, 0)],
    )
    monkeypatch.setattr(
        service.rec_repo,
        "get_voter_values_for_recommendations",
        lambda _s, _ids, **_kw: {rec_a.id: 1},
    )

    result = service.list_recommendations(
        MagicMock(),
        sw_lat=38.9,
        sw_lng=-77.05,
        ne_lat=38.93,
        ne_lng=-77.01,
        viewer_user_id=uuid.uuid4(),
    )
    assert len(result) == 2
    assert result[0]["id"] == str(rec_a.id)
    assert result[0]["category"] == "food"
    assert result[0]["likes"] == 3
    assert result[0]["dislikes"] == 1
    assert result[0]["viewer_vote"] == 1
    assert result[0]["suppressed"] is False
    assert result[1]["viewer_vote"] is None


# ---------------------------------------------------------------------------
# submit_recommendation — input / spam gates
# ---------------------------------------------------------------------------


def test_submit_recommendation_honeypot_is_silently_rejected() -> None:
    with pytest.raises(ValidationError):
        service.submit_recommendation(
            MagicMock(),
            user=_FakeUser(),
            session_id=None,
            query="Black Cat",
            by="name",
            near_latitude=38.9,
            near_longitude=-77.0,
            category="drinks",
            body="Upstairs has the jukebox",
            honeypot="spam",
            ip_hash=None,
        )


def test_submit_recommendation_rejects_too_new_accounts() -> None:
    brand_new = _FakeUser(created_at=datetime.now(UTC))
    with pytest.raises(ValidationError):
        service.submit_recommendation(
            MagicMock(),
            user=brand_new,
            session_id=None,
            query="Black Cat",
            by="name",
            near_latitude=38.9,
            near_longitude=-77.0,
            category="drinks",
            body="body",
            honeypot=None,
            ip_hash=None,
        )


def test_submit_recommendation_requires_identity() -> None:
    with pytest.raises(UnauthorizedError):
        service.submit_recommendation(
            MagicMock(),
            user=None,
            session_id=None,
            query="Black Cat",
            by="name",
            near_latitude=38.9,
            near_longitude=-77.0,
            category="drinks",
            body="hello",
            honeypot=None,
            ip_hash=None,
        )


def test_submit_recommendation_validates_body_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        service.apple_maps, "verify_place_by_name", lambda **_kw: _verified()
    )
    with pytest.raises(ValidationError):
        service.submit_recommendation(
            MagicMock(),
            user=_FakeUser(),
            session_id=None,
            query="Black Cat",
            by="name",
            near_latitude=38.9,
            near_longitude=-77.0,
            category="drinks",
            body="  ",
            honeypot=None,
            ip_hash=None,
        )
    with pytest.raises(ValidationError):
        service.submit_recommendation(
            MagicMock(),
            user=_FakeUser(),
            session_id=None,
            query="Black Cat",
            by="name",
            near_latitude=38.9,
            near_longitude=-77.0,
            category="drinks",
            body="x" * (service.MAX_BODY_LEN + 1),
            honeypot=None,
            ip_hash=None,
        )


def test_submit_recommendation_rejects_unknown_category() -> None:
    with pytest.raises(ValidationError):
        service.submit_recommendation(
            MagicMock(),
            user=_FakeUser(),
            session_id=None,
            query="Black Cat",
            by="name",
            near_latitude=38.9,
            near_longitude=-77.0,
            category="bogus",
            body="hello",
            honeypot=None,
            ip_hash=None,
        )


def test_submit_recommendation_rejects_unknown_by() -> None:
    with pytest.raises(ValidationError):
        service.submit_recommendation(
            MagicMock(),
            user=_FakeUser(),
            session_id=None,
            query="Black Cat",
            by="phone",
            near_latitude=38.9,
            near_longitude=-77.0,
            category="drinks",
            body="hello",
            honeypot=None,
            ip_hash=None,
        )


def test_submit_recommendation_name_requires_anchor() -> None:
    with pytest.raises(ValidationError):
        service.submit_recommendation(
            MagicMock(),
            user=_FakeUser(),
            session_id=None,
            query="Black Cat",
            by="name",
            near_latitude=None,
            near_longitude=None,
            category="drinks",
            body="hello",
            honeypot=None,
            ip_hash=None,
        )


def test_submit_recommendation_404s_on_apple_no_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service.apple_maps, "verify_place_by_name", lambda **_kw: None)
    with pytest.raises(AppError) as exc_info:
        service.submit_recommendation(
            MagicMock(),
            user=_FakeUser(),
            session_id=None,
            query="Nowhere",
            by="name",
            near_latitude=38.9,
            near_longitude=-77.0,
            category="drinks",
            body="hello",
            honeypot=None,
            ip_hash=None,
        )
    assert exc_info.value.code == PLACE_NOT_VERIFIED
    assert exc_info.value.status_code == 404


def test_submit_recommendation_happy_path_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _FakeUser()
    monkeypatch.setattr(
        service.apple_maps, "verify_place_by_name", lambda **_kw: _verified()
    )
    captured: dict[str, Any] = {}

    def fake_create(_s: Any, **kwargs: Any) -> _FakeRec:
        captured.update(kwargs)
        return _FakeRec(
            submitter_user_id=kwargs["submitter_user_id"],
            session_id=kwargs["session_id"],
            place_name=kwargs["place_name"],
            place_address=kwargs["place_address"],
            latitude=kwargs["latitude"],
            longitude=kwargs["longitude"],
            similarity_score=kwargs["similarity_score"],
            category=kwargs["category"],
            body=kwargs["body"],
        )

    monkeypatch.setattr(service.rec_repo, "create_recommendation", fake_create)

    result = service.submit_recommendation(
        MagicMock(),
        user=user,
        session_id=None,
        query="  Black Cat  ",
        by="name",
        near_latitude=38.9,
        near_longitude=-77.0,
        category="drinks",
        body="  Great jukebox  ",
        honeypot=None,
        ip_hash="deadbeef",
    )
    assert captured["submitter_user_id"] == user.id
    assert captured["session_id"] is None
    assert captured["place_name"] == "Black Cat"
    assert captured["similarity_score"] == pytest.approx(0.97)
    assert captured["category"] == MapRecommendationCategory.DRINKS
    assert captured["body"] == "Great jukebox"
    assert captured["ip_hash"] == "deadbeef"
    assert result["likes"] == 0
    assert result["viewer_vote"] is None
    assert result["suppressed"] is False


def test_submit_recommendation_happy_path_guest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guest submits forward session_id and omit user_id."""
    monkeypatch.setattr(
        service.apple_maps,
        "verify_place_by_address",
        lambda **_kw: _verified(similarity=0.88),
    )
    captured: dict[str, Any] = {}

    def fake_create(_s: Any, **kwargs: Any) -> _FakeRec:
        captured.update(kwargs)
        return _FakeRec(
            submitter_user_id=None,
            session_id=kwargs["session_id"],
        )

    monkeypatch.setattr(service.rec_repo, "create_recommendation", fake_create)
    service.submit_recommendation(
        MagicMock(),
        user=None,
        session_id="guest-1",
        query="1811 14th St NW",
        by="address",
        near_latitude=None,
        near_longitude=None,
        category="drinks",
        body="address-only submit",
        honeypot=None,
        ip_hash="abc",
    )
    assert captured["submitter_user_id"] is None
    assert captured["session_id"] == "guest-1"


# ---------------------------------------------------------------------------
# delete_recommendation
# ---------------------------------------------------------------------------


def test_delete_recommendation_requires_auth() -> None:
    with pytest.raises(UnauthorizedError):
        service.delete_recommendation(
            MagicMock(), recommendation_id=uuid.uuid4(), user=None
        )


def test_delete_recommendation_404s_on_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        service.rec_repo, "get_recommendation_by_id", lambda _s, _rid: None
    )
    with pytest.raises(NotFoundError):
        service.delete_recommendation(
            MagicMock(), recommendation_id=uuid.uuid4(), user=_FakeUser()
        )


def test_delete_recommendation_rejects_non_author(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rec = _FakeRec(submitter_user_id=uuid.uuid4())
    monkeypatch.setattr(
        service.rec_repo, "get_recommendation_by_id", lambda _s, _rid: rec
    )
    with pytest.raises(ForbiddenError):
        service.delete_recommendation(
            MagicMock(), recommendation_id=rec.id, user=_FakeUser()
        )


def test_delete_recommendation_author_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _FakeUser()
    rec = _FakeRec(submitter_user_id=user.id)
    monkeypatch.setattr(
        service.rec_repo, "get_recommendation_by_id", lambda _s, _rid: rec
    )
    delete_mock = MagicMock()
    monkeypatch.setattr(service.rec_repo, "delete_recommendation", delete_mock)
    service.delete_recommendation(MagicMock(), recommendation_id=rec.id, user=user)
    delete_mock.assert_called_once()


# ---------------------------------------------------------------------------
# cast_vote — identity, validation, auto-suppression
# ---------------------------------------------------------------------------


def test_cast_vote_requires_identity() -> None:
    with pytest.raises(UnauthorizedError):
        service.cast_vote(
            MagicMock(),
            recommendation_id=uuid.uuid4(),
            value=1,
            user=None,
            session_id=None,
        )


def test_cast_vote_rejects_invalid_value() -> None:
    with pytest.raises(ValidationError):
        service.cast_vote(
            MagicMock(),
            recommendation_id=uuid.uuid4(),
            value=2,
            user=_FakeUser(),
            session_id=None,
        )


def test_cast_vote_404s_on_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        service.rec_repo, "get_recommendation_by_id", lambda _s, _rid: None
    )
    with pytest.raises(NotFoundError):
        service.cast_vote(
            MagicMock(),
            recommendation_id=uuid.uuid4(),
            value=1,
            user=_FakeUser(),
            session_id=None,
        )


def test_cast_vote_zero_clears(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _FakeRec()
    monkeypatch.setattr(
        service.rec_repo, "get_recommendation_by_id", lambda _s, _rid: rec
    )
    clear_mock = MagicMock()
    monkeypatch.setattr(service.rec_repo, "clear_vote", clear_mock)
    monkeypatch.setattr(
        service.rec_repo,
        "count_votes_for_recommendation",
        lambda _s, _rid: (0, 0),
    )
    suppress_mock = MagicMock()
    monkeypatch.setattr(service.rec_repo, "suppress_recommendation", suppress_mock)
    result = service.cast_vote(
        MagicMock(),
        recommendation_id=rec.id,
        value=0,
        user=_FakeUser(),
        session_id=None,
    )
    clear_mock.assert_called_once()
    suppress_mock.assert_not_called()
    assert result == {
        "likes": 0,
        "dislikes": 0,
        "viewer_vote": None,
        "suppressed": False,
    }


def test_cast_vote_positive_upserts_and_reports_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rec = _FakeRec()
    user = _FakeUser()
    monkeypatch.setattr(
        service.rec_repo, "get_recommendation_by_id", lambda _s, _rid: rec
    )
    upsert_mock = MagicMock()
    monkeypatch.setattr(service.rec_repo, "upsert_vote", upsert_mock)
    monkeypatch.setattr(
        service.rec_repo,
        "count_votes_for_recommendation",
        lambda _s, _rid: (4, 1),
    )
    monkeypatch.setattr(service.rec_repo, "suppress_recommendation", MagicMock())
    result = service.cast_vote(
        MagicMock(),
        recommendation_id=rec.id,
        value=1,
        user=user,
        session_id=None,
    )
    kwargs = upsert_mock.call_args.kwargs
    assert kwargs["user_id"] == user.id
    assert kwargs["session_id"] is None
    assert kwargs["value"] == 1
    assert result["likes"] == 4
    assert result["dislikes"] == 1
    assert result["viewer_vote"] == 1
    assert result["suppressed"] is False


def test_cast_vote_triggers_auto_suppression_at_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A downvote that drives net <= threshold flips suppressed_at."""
    rec = _FakeRec(suppressed_at=None)
    monkeypatch.setattr(
        service.rec_repo, "get_recommendation_by_id", lambda _s, _rid: rec
    )
    monkeypatch.setattr(service.rec_repo, "upsert_vote", MagicMock())
    monkeypatch.setattr(
        service.rec_repo,
        "count_votes_for_recommendation",
        lambda _s, _rid: (0, -service.AUTO_SUPPRESS_NET_THRESHOLD),
    )

    def fake_suppress(_s: Any, row: Any) -> Any:
        row.suppressed_at = datetime.now(UTC)
        return row

    monkeypatch.setattr(service.rec_repo, "suppress_recommendation", fake_suppress)

    result = service.cast_vote(
        MagicMock(),
        recommendation_id=rec.id,
        value=-1,
        user=_FakeUser(),
        session_id=None,
    )
    assert result["suppressed"] is True


def test_cast_vote_does_not_re_suppress_already_hidden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rec = _FakeRec(suppressed_at=datetime.now(UTC))
    monkeypatch.setattr(
        service.rec_repo, "get_recommendation_by_id", lambda _s, _rid: rec
    )
    monkeypatch.setattr(service.rec_repo, "upsert_vote", MagicMock())
    monkeypatch.setattr(
        service.rec_repo,
        "count_votes_for_recommendation",
        lambda _s, _rid: (0, 10),
    )
    suppress_mock = MagicMock()
    monkeypatch.setattr(service.rec_repo, "suppress_recommendation", suppress_mock)
    service.cast_vote(
        MagicMock(),
        recommendation_id=rec.id,
        value=-1,
        user=_FakeUser(),
        session_id=None,
    )
    suppress_mock.assert_not_called()


def test_cast_vote_guest_uses_session_id_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rec = _FakeRec()
    monkeypatch.setattr(
        service.rec_repo, "get_recommendation_by_id", lambda _s, _rid: rec
    )
    captured: dict[str, Any] = {}

    def fake_upsert(_s: Any, **kwargs: Any) -> Any:
        captured.update(kwargs)
        return MagicMock()

    monkeypatch.setattr(service.rec_repo, "upsert_vote", fake_upsert)
    monkeypatch.setattr(
        service.rec_repo,
        "count_votes_for_recommendation",
        lambda _s, _rid: (1, 0),
    )
    monkeypatch.setattr(service.rec_repo, "suppress_recommendation", MagicMock())
    service.cast_vote(
        MagicMock(),
        recommendation_id=rec.id,
        value=1,
        user=None,
        session_id="g1",
    )
    assert captured["user_id"] is None
    assert captured["session_id"] == "g1"

"""Unit tests for :mod:`backend.services.recommendations`."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.services import recommendations as recs_service


@dataclass
class _FakeUser:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    spotify_top_artists: list[dict[str, Any]] | None = None
    spotify_recent_artists: list[dict[str, Any]] | None = None


@dataclass
class _FakeRec:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    score: float = 0.9
    generated_at: datetime | None = field(
        default_factory=lambda: datetime(2026, 4, 18, tzinfo=timezone.utc)
    )
    is_dismissed: bool = False
    score_breakdown: dict[str, Any] | None = field(
        default_factory=lambda: {
            "artist_match": {"score": 0.9, "matched_artists": []},
            "_match_reasons": [
                {
                    "scorer": "artist_match",
                    "kind": "spotify_id",
                    "label": "You listen to A",
                    "artist_name": "A",
                }
            ],
        }
    )
    event: Any = None


def test_list_recommendations_returns_persisted_page_without_regen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-empty list short-circuits the lazy-generate path."""
    recs = [_FakeRec()]
    monkeypatch.setattr(
        recs_service.users_repo,
        "list_recommendations",
        lambda _s, _u, *, page, per_page: (recs, 1),
    )
    generate_mock = MagicMock()
    monkeypatch.setattr(
        recs_service.rec_engine, "generate_for_user", generate_mock
    )
    result, total = recs_service.list_recommendations_for_user(
        MagicMock(), _FakeUser()  # type: ignore[arg-type]
    )
    assert result is recs
    assert total == 1
    generate_mock.assert_not_called()


def test_list_recommendations_lazy_generates_when_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty list + cached artists triggers a regeneration."""
    session = MagicMock()
    user = _FakeUser(spotify_top_artists=[{"name": "A"}])
    calls: list[str] = []

    def fake_list(
        _s: Any, _u: uuid.UUID, *, page: int, per_page: int
    ) -> tuple[list[Any], int]:
        calls.append("list")
        return ([_FakeRec()], 1) if len(calls) > 1 else ([], 0)

    def fake_generate(_s: Any, _u: _FakeUser) -> int:
        calls.append("generate")
        return 5

    monkeypatch.setattr(
        recs_service.users_repo, "list_recommendations", fake_list
    )
    monkeypatch.setattr(
        recs_service.rec_engine, "generate_for_user", fake_generate
    )
    result, total = recs_service.list_recommendations_for_user(
        session, user  # type: ignore[arg-type]
    )
    assert calls == ["list", "generate", "list"]
    session.commit.assert_called_once()
    assert total == 1
    assert len(result) == 1


def test_list_recommendations_skips_regen_when_no_cached_artists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """User without Spotify data gets an empty page, not a regen."""
    user = _FakeUser(spotify_top_artists=None)
    monkeypatch.setattr(
        recs_service.users_repo,
        "list_recommendations",
        lambda _s, _u, *, page, per_page: ([], 0),
    )
    generate_mock = MagicMock()
    monkeypatch.setattr(
        recs_service.rec_engine, "generate_for_user", generate_mock
    )
    result, total = recs_service.list_recommendations_for_user(
        MagicMock(), user  # type: ignore[arg-type]
    )
    assert result == []
    assert total == 0
    generate_mock.assert_not_called()


def test_list_recommendations_lazy_generate_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """lazy_generate=False never triggers regeneration even if empty."""
    user = _FakeUser(spotify_top_artists=[{"name": "A"}])
    monkeypatch.setattr(
        recs_service.users_repo,
        "list_recommendations",
        lambda _s, _u, *, page, per_page: ([], 0),
    )
    generate_mock = MagicMock()
    monkeypatch.setattr(
        recs_service.rec_engine, "generate_for_user", generate_mock
    )
    recs_service.list_recommendations_for_user(
        MagicMock(), user, lazy_generate=False  # type: ignore[arg-type]
    )
    generate_mock.assert_not_called()


def test_refresh_recommendations_commits_and_returns_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock()
    monkeypatch.setattr(
        recs_service.rec_engine, "generate_for_user", lambda _s, _u: 42
    )
    count = recs_service.refresh_recommendations_for_user(
        session, _FakeUser()  # type: ignore[arg-type]
    )
    assert count == 42
    session.commit.assert_called_once()


def test_serialize_recommendation_flattens_match_reasons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reasons come up to a top-level field; breakdown omits the private key."""
    rec = _FakeRec()
    monkeypatch.setattr(
        recs_service.events_service,
        "serialize_event_summary",
        lambda _event: {"id": "summary"},
    )
    payload = recs_service.serialize_recommendation(rec)  # type: ignore[arg-type]
    assert payload["match_reasons"][0]["label"] == "You listen to A"
    assert "_match_reasons" not in payload["score_breakdown"]
    assert payload["event"] == {"id": "summary"}
    assert payload["generated_at"] == rec.generated_at.isoformat()


def test_serialize_recommendation_tolerates_missing_breakdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rec = _FakeRec(score_breakdown=None, generated_at=None)
    monkeypatch.setattr(
        recs_service.events_service,
        "serialize_event_summary",
        lambda _event: {"id": "summary"},
    )
    payload = recs_service.serialize_recommendation(rec)  # type: ignore[arg-type]
    assert payload["match_reasons"] == []
    assert payload["score_breakdown"] == {}
    assert payload["generated_at"] is None

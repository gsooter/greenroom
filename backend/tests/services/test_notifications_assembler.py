"""Tests for the weekly-digest content assembler.

The assembler turns a (user, prefs) pair into the body of a single
weekly-digest email: a ranked list of show cards plus the heading,
preheader, and CTA link the renderer interpolates into the template.

Tests pin three pieces of behaviour that matter end-to-end:

* Shape — the returned :class:`WeeklyDigest` dataclass exposes the
  exact keys :func:`render_email` will look up in the template
  context. A renamed key here silently breaks the template.
* Ranking — events with a higher persisted recommendation score sort
  ahead of lower-scoring (or unscored) events. Otherwise the digest
  collapses to "next week in DC" by date and the personalised value of
  the email goes away.
* Cold-start — a user with no recs still gets a chronological digest
  with a "connect Spotify or follow some artists" nudge intro.

These tests use MagicMock-backed sessions and stub the events repo and
recommendation engine; real DB integration is covered by repo-level
tests.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.services import notifications


def _event(
    *,
    title: str,
    starts_at: datetime,
    spotify_artist_ids: list[str] | None = None,
    venue_name: str = "9:30 Club",
    image_url: str | None = "https://example.com/img.jpg",
    slug: str | None = None,
    event_id: uuid.UUID | None = None,
) -> MagicMock:
    """Build a MagicMock Event with the columns the assembler reads."""
    venue = MagicMock(name="Venue")
    venue.name = venue_name
    event = MagicMock(name="Event")
    event.id = event_id or uuid.uuid4()
    event.title = title
    event.slug = slug or f"slug-{uuid.uuid4().hex[:6]}"
    event.starts_at = starts_at
    event.spotify_artist_ids = spotify_artist_ids
    event.image_url = image_url
    event.venue = venue
    event.min_price = None
    event.ticket_url = None
    return event


def _user(
    *,
    city_id: uuid.UUID | None = None,
    display_name: str = "Riley",
    email: str = "riley@example.test",
) -> MagicMock:
    user = MagicMock(name="User")
    user.id = uuid.uuid4()
    user.email = email
    user.display_name = display_name
    user.city_id = city_id or uuid.uuid4()
    return user


def _stub_recs(
    monkeypatch: pytest.MonkeyPatch,
    *,
    score_by_event_id: dict[uuid.UUID, float] | None = None,
) -> None:
    """Stub the rec engine + users_repo.list_recommendations.

    Args:
        monkeypatch: pytest fixture.
        score_by_event_id: Map from event_id to score for any
            recommendation rows we want the assembler to see. Pass
            ``None`` for the cold-start path (no rows returned).
    """
    monkeypatch.setattr(
        notifications.rec_engine, "generate_for_user", lambda *_a, **_k: 0
    )

    rows = []
    for event_id, score in (score_by_event_id or {}).items():
        rec = MagicMock(name="Recommendation")
        rec.event_id = event_id
        rec.score = score
        rows.append(rec)

    monkeypatch.setattr(
        notifications.users_repo,
        "list_recommendations",
        lambda *_a, **_k: (rows, len(rows)),
    )


# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------


def test_assembler_returns_none_when_no_upcoming_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty city + week → no email worth sending."""
    user = _user()
    monkeypatch.setattr(
        notifications.events_repo, "list_events", lambda *_a, **_k: ([], 0)
    )
    _stub_recs(monkeypatch)

    digest = notifications.assemble_weekly_digest(
        MagicMock(), user, prefs=MagicMock(timezone="America/New_York")
    )
    assert digest is None


def test_assembler_returns_dataclass_with_template_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The returned digest exposes shows, heading, intro, cta_url."""
    user = _user(display_name="Riley")
    now = datetime(2026, 4, 26, 12, 0, tzinfo=UTC)
    event = _event(title="Phoebe Bridgers", starts_at=now + timedelta(days=2))
    monkeypatch.setattr(
        notifications.events_repo, "list_events", lambda *_a, **_k: ([event], 1)
    )
    _stub_recs(monkeypatch, score_by_event_id={event.id: 0.9})

    digest = notifications.assemble_weekly_digest(
        MagicMock(),
        user,
        prefs=MagicMock(timezone="America/New_York"),
        now=now,
    )
    assert digest is not None
    ctx = digest.template_context()
    assert "shows" in ctx and len(ctx["shows"]) == 1
    assert "heading" in ctx
    assert "intro" in ctx
    assert "cta_url" in ctx
    assert "preheader" in ctx


def test_show_card_carries_url_built_from_slug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each show card resolves to the public /events/<slug> URL."""
    user = _user()
    event = _event(
        title="Show A",
        starts_at=datetime(2026, 5, 1, 20, 0, tzinfo=UTC),
        slug="show-a-slug",
    )
    monkeypatch.setattr(
        notifications.events_repo, "list_events", lambda *_a, **_k: ([event], 1)
    )
    _stub_recs(monkeypatch, score_by_event_id={event.id: 0.5})

    digest = notifications.assemble_weekly_digest(
        MagicMock(),
        user,
        prefs=MagicMock(timezone="America/New_York"),
    )
    assert digest is not None
    card = digest.template_context()["shows"][0]
    assert card["url"].endswith("/events/show-a-slug")
    assert card["headliner"] == "Show A"
    assert card["venue"] == "9:30 Club"


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


def test_recommended_events_rank_above_unscored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A recommended event later in the week beats an unscored sooner one."""
    user = _user()
    now = datetime(2026, 4, 26, 12, 0, tzinfo=UTC)
    matched = _event(title="Matched", starts_at=now + timedelta(days=5))
    unmatched = _event(title="Unmatched", starts_at=now + timedelta(days=1))
    monkeypatch.setattr(
        notifications.events_repo,
        "list_events",
        lambda *_a, **_k: ([unmatched, matched], 2),
    )
    _stub_recs(monkeypatch, score_by_event_id={matched.id: 0.9})

    digest = notifications.assemble_weekly_digest(
        MagicMock(),
        user,
        prefs=MagicMock(timezone="America/New_York"),
        now=now,
    )
    assert digest is not None
    titles = [c["headliner"] for c in digest.template_context()["shows"]]
    assert titles == ["Matched", "Unmatched"]


def test_higher_scoring_recs_rank_above_lower_scoring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two recommended events sort by score descending."""
    user = _user()
    now = datetime(2026, 4, 26, 12, 0, tzinfo=UTC)
    strong = _event(title="Strong", starts_at=now + timedelta(days=5))
    weak = _event(title="Weak", starts_at=now + timedelta(days=1))
    monkeypatch.setattr(
        notifications.events_repo,
        "list_events",
        lambda *_a, **_k: ([weak, strong], 2),
    )
    _stub_recs(monkeypatch, score_by_event_id={strong.id: 0.95, weak.id: 0.4})

    digest = notifications.assemble_weekly_digest(
        MagicMock(),
        user,
        prefs=MagicMock(timezone="America/New_York"),
        now=now,
    )
    assert digest is not None
    titles = [c["headliner"] for c in digest.template_context()["shows"]]
    assert titles == ["Strong", "Weak"]


def test_unscored_events_sort_by_date_ascending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cold-start: no recs → assembler falls back to chronological order."""
    user = _user()
    now = datetime(2026, 4, 26, 12, 0, tzinfo=UTC)
    later = _event(title="Later", starts_at=now + timedelta(days=4))
    sooner = _event(title="Sooner", starts_at=now + timedelta(days=1))
    monkeypatch.setattr(
        notifications.events_repo,
        "list_events",
        lambda *_a, **_k: ([later, sooner], 2),
    )
    _stub_recs(monkeypatch)

    digest = notifications.assemble_weekly_digest(
        MagicMock(),
        user,
        prefs=MagicMock(timezone="America/New_York"),
        now=now,
    )
    assert digest is not None
    titles = [c["headliner"] for c in digest.template_context()["shows"]]
    assert titles == ["Sooner", "Later"]


def test_cold_start_intro_nudges_user_to_personalize(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without recs, the intro guides the user toward Spotify / follows."""
    user = _user(display_name="Riley")
    now = datetime(2026, 4, 26, 12, 0, tzinfo=UTC)
    event = _event(title="Some Show", starts_at=now + timedelta(days=2))
    monkeypatch.setattr(
        notifications.events_repo, "list_events", lambda *_a, **_k: ([event], 1)
    )
    _stub_recs(monkeypatch)

    digest = notifications.assemble_weekly_digest(
        MagicMock(),
        user,
        prefs=MagicMock(timezone="America/New_York"),
        now=now,
    )
    assert digest is not None
    assert "Connect Spotify" in digest.intro or "follow" in digest.intro.lower()


def test_assembler_caps_show_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The assembler returns at most ``_MAX_DIGEST_SHOWS`` shows."""
    user = _user()
    now = datetime(2026, 4, 26, 12, 0, tzinfo=UTC)
    events = [
        _event(title=f"Show {i}", starts_at=now + timedelta(days=i, hours=1))
        for i in range(20)
    ]
    monkeypatch.setattr(
        notifications.events_repo, "list_events", lambda *_a, **_k: (events, 20)
    )
    _stub_recs(monkeypatch)

    digest = notifications.assemble_weekly_digest(
        MagicMock(),
        user,
        prefs=MagicMock(timezone="America/New_York"),
        now=now,
    )
    assert digest is not None
    assert len(digest.template_context()["shows"]) == notifications._MAX_DIGEST_SHOWS


def test_assembler_passes_users_city_and_week_window_to_repo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The repo call scopes by city_id and a 7-day window from ``now``."""
    user = _user()
    captured: dict[str, Any] = {}
    now = datetime(2026, 4, 26, 12, 0, tzinfo=UTC)

    def fake_list(_session: Any, **kwargs: Any) -> tuple[list[Any], int]:
        captured.update(kwargs)
        return ([], 0)

    monkeypatch.setattr(notifications.events_repo, "list_events", fake_list)
    _stub_recs(monkeypatch)
    notifications.assemble_weekly_digest(
        MagicMock(),
        user,
        prefs=MagicMock(timezone="America/New_York"),
        now=now,
    )
    assert captured["city_id"] == user.city_id
    assert captured["date_from"] == now.date()
    assert captured["date_to"] == (now + timedelta(days=7)).date()
    assert captured["available_only"] is True

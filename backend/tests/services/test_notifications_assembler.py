"""Tests for the weekly-digest content assembler.

The assembler turns a (user, prefs) pair into the body of a single
weekly-digest email: a ranked list of show cards plus the heading,
preheader, and CTA link the renderer interpolates into the template.

Tests pin two pieces of behaviour that matter end-to-end:

* Shape — the returned :class:`WeeklyDigest` dataclass exposes the
  exact keys :func:`render_email` will look up in the template
  context. A renamed key here silently breaks the template.
* Ranking — events whose ``spotify_artist_ids`` overlap with the
  user's tracked artist IDs sort ahead of unmatched events.
  Otherwise the digest just lists "next week in DC" by date and
  the personalised value of the email collapses.

These tests use MagicMock-backed sessions and stub the events repo;
real DB integration is covered by the repo-level tests.
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
) -> MagicMock:
    """Build a MagicMock Event with the columns the assembler reads."""
    venue = MagicMock(name="Venue")
    venue.name = venue_name
    event = MagicMock(name="Event")
    event.id = uuid.uuid4()
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
    spotify_top: list[str] | None = None,
    spotify_recent: list[str] | None = None,
    display_name: str = "Riley",
    email: str = "riley@example.test",
) -> MagicMock:
    user = MagicMock(name="User")
    user.id = uuid.uuid4()
    user.email = email
    user.display_name = display_name
    user.city_id = city_id or uuid.uuid4()
    user.spotify_top_artist_ids = spotify_top
    user.spotify_recent_artist_ids = spotify_recent
    return user


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
    events = [
        _event(
            title="Phoebe Bridgers",
            starts_at=now + timedelta(days=2),
        ),
    ]
    monkeypatch.setattr(
        notifications.events_repo, "list_events", lambda *_a, **_k: (events, 1)
    )

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
    events = [
        _event(
            title="Show A",
            starts_at=datetime(2026, 5, 1, 20, 0, tzinfo=UTC),
            slug="show-a-slug",
        ),
    ]
    monkeypatch.setattr(
        notifications.events_repo, "list_events", lambda *_a, **_k: (events, 1)
    )

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


def test_spotify_matched_events_rank_above_unmatched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A matched event on Saturday beats an unmatched event on Tuesday."""
    user = _user(spotify_top=["sp_artist_X"])
    now = datetime(2026, 4, 26, 12, 0, tzinfo=UTC)
    matched = _event(
        title="Matched",
        starts_at=now + timedelta(days=5),  # Saturday
        spotify_artist_ids=["sp_artist_X"],
    )
    unmatched = _event(
        title="Unmatched",
        starts_at=now + timedelta(days=1),  # Tuesday
        spotify_artist_ids=["sp_artist_Y"],
    )
    monkeypatch.setattr(
        notifications.events_repo,
        "list_events",
        lambda *_a, **_k: ([unmatched, matched], 2),
    )

    digest = notifications.assemble_weekly_digest(
        MagicMock(),
        user,
        prefs=MagicMock(timezone="America/New_York"),
        now=now,
    )
    assert digest is not None
    titles = [c["headliner"] for c in digest.template_context()["shows"]]
    assert titles == ["Matched", "Unmatched"]


def test_unmatched_events_sort_by_date_ascending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without Spotify matches, the assembler sorts by date."""
    user = _user(spotify_top=None)
    now = datetime(2026, 4, 26, 12, 0, tzinfo=UTC)
    later = _event(title="Later", starts_at=now + timedelta(days=4))
    sooner = _event(title="Sooner", starts_at=now + timedelta(days=1))
    monkeypatch.setattr(
        notifications.events_repo,
        "list_events",
        lambda *_a, **_k: ([later, sooner], 2),
    )

    digest = notifications.assemble_weekly_digest(
        MagicMock(),
        user,
        prefs=MagicMock(timezone="America/New_York"),
        now=now,
    )
    assert digest is not None
    titles = [c["headliner"] for c in digest.template_context()["shows"]]
    assert titles == ["Sooner", "Later"]


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

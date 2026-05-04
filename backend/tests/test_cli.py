"""Tests for the Greenroom CLI hydration commands.

The CLI is a thin wrapper around the hydration service; these tests
cover argument parsing, the interactive-confirmation skip flag, and
the hydration-stats output format. The underlying service behavior
(depth/threshold/cap enforcement) is covered by
``test_artist_hydration.py``.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from backend.cli import cli
from backend.services import artist_hydration
from backend.services.artist_hydration import (
    HydrationCandidate,
    HydrationPreview,
    HydrationResult,
)


@pytest.fixture
def fake_session_factory(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Stub out ``get_session_factory`` with a context-manager mock.

    Returns:
        The :class:`MagicMock` standing in for the session.
    """
    session_mock = MagicMock()
    factory_mock = MagicMock()
    factory_mock.return_value.__enter__.return_value = session_mock
    factory_mock.return_value.__exit__.return_value = None

    monkeypatch.setattr("backend.cli.get_session_factory", lambda: factory_mock)
    return session_mock


def _stub_artist(name: str = "Caamp") -> MagicMock:
    """Build a MagicMock that stands in for an :class:`Artist` row.

    Args:
        name: Display name to attach.

    Returns:
        Configured MagicMock.
    """
    artist = MagicMock()
    artist.id = uuid.uuid4()
    artist.name = name
    artist.normalized_name = name.lower()
    artist.hydration_depth = 0
    artist.hydration_source = None
    artist.hydrated_from_artist_id = None
    artist.hydrated_at = None
    return artist


def _stub_preview(
    artist: MagicMock, eligible_names: list[str], cap_remaining: int = 100
) -> HydrationPreview:
    """Build a HydrationPreview with all candidates eligible.

    Args:
        artist: Source artist mock.
        eligible_names: Display names to mark eligible.
        cap_remaining: Remaining daily cap.

    Returns:
        Configured :class:`HydrationPreview`.
    """
    return HydrationPreview(
        source_artist=artist,
        candidates=[
            HydrationCandidate(
                similar_artist_name=name,
                similar_artist_mbid=None,
                similarity_score=0.9,
                status="eligible",
                existing_artist_id=None,
            )
            for name in eligible_names
        ],
        eligible_count=len(eligible_names),
        would_add_count=min(len(eligible_names), 5, cap_remaining),
        daily_cap_remaining=cap_remaining,
        can_proceed=len(eligible_names) > 0 and cap_remaining > 0,
        blocking_reason=None,
    )


def test_hydrate_requires_artist_selector() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["hydrate"])
    assert result.exit_code != 0
    assert "artist-id" in result.output


def test_hydrate_runs_with_yes_flag(
    monkeypatch: pytest.MonkeyPatch, fake_session_factory: MagicMock
) -> None:
    artist = _stub_artist()
    preview = _stub_preview(artist, ["Mt. Joy", "Wild Rivers"])
    monkeypatch.setattr("backend.cli._resolve_artist", lambda *_a, **_kw: artist)
    monkeypatch.setattr("backend.cli.preview_hydration", lambda _s, _id: preview)

    new_artist = _stub_artist("Mt. Joy")
    result_obj = HydrationResult(
        source_artist_id=artist.id,
        added_artists=[new_artist],
        added_count=1,
        skipped_count=0,
        filtered_count=0,
        daily_cap_hit=False,
        blocking_reason=None,
    )
    captured: dict[str, Any] = {}

    def fake_execute(_s: Any, sid: uuid.UUID, **kw: Any) -> HydrationResult:
        captured["source"] = sid
        captured.update(kw)
        return result_obj

    monkeypatch.setattr("backend.cli.execute_hydration", fake_execute)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "hydrate",
            "--artist-name",
            "Caamp",
            "--operator",
            "ops@greenroom.test",
            "--yes",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Added 1 artist" in result.output
    assert captured["admin_email"] == "ops@greenroom.test"
    assert "Mt. Joy" in captured["confirmed_candidates"]


def test_hydrate_aborts_when_user_says_no(
    monkeypatch: pytest.MonkeyPatch, fake_session_factory: MagicMock
) -> None:
    artist = _stub_artist()
    preview = _stub_preview(artist, ["Mt. Joy"])
    monkeypatch.setattr("backend.cli._resolve_artist", lambda *_a, **_kw: artist)
    monkeypatch.setattr("backend.cli.preview_hydration", lambda _s, _id: preview)
    execute_called = False

    def fake_execute(*_a: Any, **_kw: Any) -> HydrationResult:
        nonlocal execute_called
        execute_called = True
        raise AssertionError("execute_hydration must not be called when user aborts")

    monkeypatch.setattr("backend.cli.execute_hydration", fake_execute)

    runner = CliRunner()
    result = runner.invoke(cli, ["hydrate", "--artist-name", "Caamp"], input="n\n")
    assert result.exit_code != 0  # click.confirm(abort=True) exits non-zero
    assert execute_called is False


def test_hydration_stats_prints_leaderboards(
    monkeypatch: pytest.MonkeyPatch, fake_session_factory: MagicMock
) -> None:
    monkeypatch.setattr("backend.cli.get_daily_hydration_count", lambda _s: 25)
    from backend.services.admin_dashboard import (
        HydrationCandidateArtist,
        LeaderboardArtist,
    )

    monkeypatch.setattr(
        "backend.cli.most_hydrated_leaderboard",
        lambda _s, **_kw: [
            LeaderboardArtist(
                artist_id=uuid.uuid4(), artist_name="Caamp", hydration_count=5
            )
        ],
    )
    monkeypatch.setattr(
        "backend.cli.best_hydration_candidates",
        lambda _s, **_kw: [
            HydrationCandidateArtist(
                artist_id=uuid.uuid4(),
                artist_name="Phoebe Bridgers",
                candidate_count=4,
                top_candidate_name="Lucy Dacus",
            )
        ],
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["hydration-stats"])
    assert result.exit_code == 0, result.output
    assert "Caamp" in result.output
    assert "Phoebe Bridgers" in result.output
    assert "Lucy Dacus" in result.output
    assert "75/100" in result.output  # 100 - 25


def test_hydrate_blocks_when_preview_cannot_proceed(
    monkeypatch: pytest.MonkeyPatch, fake_session_factory: MagicMock
) -> None:
    artist = _stub_artist()
    artist.hydration_depth = artist_hydration.MAX_HYDRATION_DEPTH
    preview = HydrationPreview(
        source_artist=artist,
        candidates=[],
        eligible_count=0,
        would_add_count=0,
        daily_cap_remaining=100,
        can_proceed=False,
        blocking_reason=(
            f"Source artist is at hydration depth "
            f"{artist_hydration.MAX_HYDRATION_DEPTH}; cannot hydrate beyond "
            f"depth {artist_hydration.MAX_HYDRATION_DEPTH}."
        ),
    )
    monkeypatch.setattr("backend.cli._resolve_artist", lambda *_a, **_kw: artist)
    monkeypatch.setattr("backend.cli.preview_hydration", lambda _s, _id: preview)

    runner = CliRunner()
    result = runner.invoke(cli, ["hydrate", "--artist-name", "Caamp", "--yes"])
    assert result.exit_code != 0
    assert "depth" in result.output.lower()

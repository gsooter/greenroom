"""Unit tests for :mod:`backend.scripts.backfill_artist_enrichment`.

The backfill logic is exercised with a MagicMock session and
monkey-patched repo calls — the underlying SQL is covered by the repo
tests in :mod:`backend.tests.data.test_events_repo` and
:mod:`backend.tests.data.test_artists_repo`.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.scripts import backfill_artist_enrichment as backfill
from backend.scripts.backfill_artist_enrichment import (
    BackfillSummary,
    backfill_artists_from_events,
    main,
)


def test_backfill_creates_new_rows_for_unseen_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A name whose normalized key is missing is upserted and counted."""
    monkeypatch.setattr(
        backfill.events_repo,
        "list_all_event_artist_names",
        lambda _s: ["Phoebe Bridgers", "Julien Baker"],
    )
    monkeypatch.setattr(
        backfill.artists_repo,
        "get_artist_by_normalized_name",
        lambda _s, _key: None,
    )
    upsert_mock = MagicMock()
    monkeypatch.setattr(backfill.artists_repo, "upsert_artist_by_name", upsert_mock)

    summary = backfill_artists_from_events(MagicMock())

    assert summary == BackfillSummary(
        scanned=2, created=2, already_present=0, skipped_blank=0
    )
    assert upsert_mock.call_count == 2


def test_backfill_counts_existing_rows_without_recreating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Names whose normalized key already exists hit the ``already_present`` bucket."""
    monkeypatch.setattr(
        backfill.events_repo,
        "list_all_event_artist_names",
        lambda _s: ["Phoebe Bridgers"],
    )
    monkeypatch.setattr(
        backfill.artists_repo,
        "get_artist_by_normalized_name",
        lambda _s, _key: object(),  # any truthy "row exists" sentinel
    )
    upsert_mock = MagicMock()
    monkeypatch.setattr(backfill.artists_repo, "upsert_artist_by_name", upsert_mock)

    summary = backfill_artists_from_events(MagicMock())

    assert summary.created == 0
    assert summary.already_present == 1
    upsert_mock.assert_not_called()


def test_backfill_dedupes_by_normalized_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Casing/diacritic variants collapse to a single upsert."""
    monkeypatch.setattr(
        backfill.events_repo,
        "list_all_event_artist_names",
        lambda _s: ["Beyoncé", "BEYONCE", "  beyonce  "],
    )
    monkeypatch.setattr(
        backfill.artists_repo,
        "get_artist_by_normalized_name",
        lambda _s, _key: None,
    )
    upsert_mock = MagicMock()
    monkeypatch.setattr(backfill.artists_repo, "upsert_artist_by_name", upsert_mock)

    summary = backfill_artists_from_events(MagicMock())

    assert summary.scanned == 1
    assert summary.created == 1
    assert upsert_mock.call_count == 1


def test_backfill_skips_names_that_normalize_to_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Whitespace-only raw names are recorded under ``skipped_blank``."""
    monkeypatch.setattr(
        backfill.events_repo,
        "list_all_event_artist_names",
        lambda _s: ["   ", "\t"],
    )
    upsert_mock = MagicMock()
    monkeypatch.setattr(backfill.artists_repo, "upsert_artist_by_name", upsert_mock)

    summary = backfill_artists_from_events(MagicMock())

    assert summary == BackfillSummary(
        scanned=0, created=0, already_present=0, skipped_blank=2
    )
    upsert_mock.assert_not_called()


def test_backfill_dry_run_reports_but_never_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``dry_run=True`` counts the would-be writes without calling upsert."""
    monkeypatch.setattr(
        backfill.events_repo,
        "list_all_event_artist_names",
        lambda _s: ["Phoebe", "Julien"],
    )
    monkeypatch.setattr(
        backfill.artists_repo,
        "get_artist_by_normalized_name",
        lambda _s, _key: None,
    )
    upsert_mock = MagicMock()
    monkeypatch.setattr(backfill.artists_repo, "upsert_artist_by_name", upsert_mock)

    summary = backfill_artists_from_events(MagicMock(), dry_run=True)

    assert summary.created == 2
    upsert_mock.assert_not_called()


# ---------------------------------------------------------------------------
# main — CLI entry
# ---------------------------------------------------------------------------


class _CtxSession:
    """Minimal session supporting ``with`` + commit/rollback tracking."""

    def __init__(self) -> None:
        self.commit = MagicMock()
        self.rollback = MagicMock()

    def __enter__(self) -> _CtxSession:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


def test_main_commits_on_success(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    session = _CtxSession()
    monkeypatch.setattr(backfill, "get_session_factory", lambda: lambda: session)

    def fake_run(_s: Any, *, dry_run: bool) -> BackfillSummary:
        return BackfillSummary(scanned=3, created=2, already_present=1, skipped_blank=0)

    monkeypatch.setattr(backfill, "backfill_artists_from_events", fake_run)

    rc = main([])
    assert rc == 0
    session.commit.assert_called_once()
    session.rollback.assert_not_called()
    out = capsys.readouterr().out
    assert "created=2" in out
    assert "already_present=1" in out


def test_main_dry_run_rolls_back_and_labels_output(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    session = _CtxSession()
    monkeypatch.setattr(backfill, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(
        backfill,
        "backfill_artists_from_events",
        lambda _s, *, dry_run: BackfillSummary(
            scanned=1, created=1, already_present=0, skipped_blank=0
        ),
    )

    rc = main(["--dry-run"])
    assert rc == 0
    session.rollback.assert_called_once()
    session.commit.assert_not_called()
    assert "[dry-run]" in capsys.readouterr().out


def test_main_rolls_back_on_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _CtxSession()
    monkeypatch.setattr(backfill, "get_session_factory", lambda: lambda: session)

    def boom(_s: Any, *, dry_run: bool) -> BackfillSummary:
        raise RuntimeError("scan failed")

    monkeypatch.setattr(backfill, "backfill_artists_from_events", boom)

    with pytest.raises(RuntimeError):
        main([])
    session.rollback.assert_called_once()
    session.commit.assert_not_called()

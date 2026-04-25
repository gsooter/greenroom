"""Unit tests for :mod:`backend.services.pricing_tasks`.

The Celery layer is thin: it owns the session, fans out the
orchestrator, and aggregates the per-event summary. Tests stub the
session factory, the events repository, and the orchestrator so the
focus stays on the aggregation contract.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.services import pricing_tasks as tasks
from backend.services.tickets import RefreshResult


@dataclass
class _FakeEvent:
    """Minimal event row — only the field the task reads to log on failure."""

    id: uuid.UUID = field(default_factory=uuid.uuid4)


class _CtxSession:
    """Session stub that supports ``with`` plus commit/rollback mocks."""

    def __init__(self) -> None:
        """Initialize commit/rollback mocks."""
        self.commit = MagicMock()
        self.rollback = MagicMock()

    def __enter__(self) -> _CtxSession:
        """Return self so ``with session_factory() as session`` works.

        Returns:
            This session instance.
        """
        return self

    def __exit__(self, *_exc: object) -> None:
        """Ignore the context exit — the task drives commit/rollback.

        Args:
            *_exc: Ignored exception triple.
        """
        return None


def _result(
    event_id: uuid.UUID,
    *,
    quotes: int = 1,
    links: int = 1,
    errors: tuple[str, ...] = (),
    cooldown: bool = False,
) -> RefreshResult:
    """Build a :class:`RefreshResult` with sensible defaults.

    Args:
        event_id: UUID for the result.
        quotes: Number of snapshots the orchestrator persisted.
        links: Number of pricing-link rows touched.
        errors: Provider names that failed.
        cooldown: Whether the run short-circuited on the cooldown gate.

    Returns:
        A frozen :class:`RefreshResult` for use in test stubs.
    """
    return RefreshResult(
        event_id=event_id,
        refreshed_at=datetime.now(UTC),
        cooldown_active=cooldown,
        quotes_persisted=quotes,
        links_upserted=links,
        provider_errors=errors,
    )


def test_refresh_all_returns_zero_summary_when_nothing_upcoming(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty catalog returns the zero summary without calling refresh.

    No events to sweep means nothing to commit either; the orchestrator
    must not be called and the session must not be marked dirty.
    """
    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(
        tasks.events_repo, "list_events_for_pricing_sweep", lambda _s, limit: []
    )
    refresh_mock = MagicMock()
    monkeypatch.setattr(tasks, "refresh_event_pricing", refresh_mock)

    result = tasks.refresh_all_event_pricing()

    assert result == {
        "processed": 0,
        "succeeded": 0,
        "errors": 0,
        "quotes_persisted": 0,
        "links_upserted": 0,
        "provider_errors": [],
    }
    refresh_mock.assert_not_called()
    session.commit.assert_not_called()


def test_refresh_all_aggregates_per_event_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Quote and link counts sum across the batch; provider errors dedupe.

    The summary is what shows up in the daily digest; aggregation
    has to be lossless on the counts and stable on the error list so
    the on-call channel can spot when one provider has been failing
    on every event.
    """
    session = _CtxSession()
    events = [_FakeEvent() for _ in range(3)]

    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(
        tasks.events_repo, "list_events_for_pricing_sweep", lambda _s, limit: events
    )

    results = [
        _result(events[0].id, quotes=2, links=3, errors=("seatgeek",)),
        _result(events[1].id, quotes=1, links=1, errors=("seatgeek", "tickpick")),
        _result(events[2].id, quotes=4, links=2, errors=()),
    ]
    iterator = iter(results)
    monkeypatch.setattr(
        tasks,
        "refresh_event_pricing",
        lambda _s, _e, force: next(iterator),
    )

    summary = tasks.refresh_all_event_pricing()

    assert summary["processed"] == 3
    assert summary["succeeded"] == 3
    assert summary["errors"] == 0
    assert summary["quotes_persisted"] == 7
    assert summary["links_upserted"] == 6
    assert summary["provider_errors"] == ["seatgeek", "tickpick"]
    session.commit.assert_called_once()


def test_refresh_all_isolates_per_event_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One event raising does not stop the rest of the batch.

    A bad event row (missing venue, malformed metadata) shouldn't
    abort the sweep — the rest of the catalog still needs fresh
    prices. Failed events count toward ``errors`` and the session
    rolls back between failures so the next event has a usable session.
    """
    session = _CtxSession()
    events = [_FakeEvent(), _FakeEvent(), _FakeEvent()]

    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(
        tasks.events_repo, "list_events_for_pricing_sweep", lambda _s, limit: events
    )

    call_count = {"n": 0}

    def fake_refresh(_s: Any, event: _FakeEvent, force: bool) -> RefreshResult:
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("bad event row")
        return _result(event.id, quotes=1, links=1)

    monkeypatch.setattr(tasks, "refresh_event_pricing", fake_refresh)

    summary = tasks.refresh_all_event_pricing()

    assert summary["processed"] == 3
    assert summary["succeeded"] == 2
    assert summary["errors"] == 1
    # The two successful events still contribute their counts.
    assert summary["quotes_persisted"] == 2
    assert summary["links_upserted"] == 2
    # Mid-batch rollback so the failed event's partial state doesn't
    # leak into the next iteration.
    assert session.rollback.call_count == 1
    session.commit.assert_called_once()


def test_refresh_all_passes_force_true_to_orchestrator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cron always forces — the cooldown is for human refresh-spam.

    If the cron didn't pass ``force=True``, a sweep that ran inside
    the cooldown of a manual refresh would silently skip that event.
    """
    session = _CtxSession()
    event = _FakeEvent()

    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(
        tasks.events_repo, "list_events_for_pricing_sweep", lambda _s, limit: [event]
    )

    captured: dict[str, Any] = {}

    def fake_refresh(_s: Any, _e: _FakeEvent, force: bool = False) -> RefreshResult:
        captured["force"] = force
        return _result(event.id)

    monkeypatch.setattr(tasks, "refresh_event_pricing", fake_refresh)

    tasks.refresh_all_event_pricing()

    assert captured["force"] is True


def test_refresh_all_rolls_back_on_outer_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An outer failure (e.g., the listing query itself) rolls back.

    Per-event errors are caught inside the loop; an exception escaping
    that loop is genuinely unexpected and must roll the session back so
    Celery's retry doesn't replay against a poisoned session.
    """
    session = _CtxSession()
    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)

    def boom(_s: Any, limit: int) -> list[Any]:
        raise RuntimeError("query exploded")

    monkeypatch.setattr(tasks.events_repo, "list_events_for_pricing_sweep", boom)

    with pytest.raises(RuntimeError):
        tasks.refresh_all_event_pricing()

    session.rollback.assert_called_once()


def test_refresh_all_uses_configured_batch_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The repo helper is called with :data:`BATCH_SIZE`.

    The batch size is the upstream-budget cap; any change should be
    a deliberate edit to the constant rather than a quietly-passed
    override at the call site.
    """
    session = _CtxSession()
    captured: dict[str, Any] = {}

    def fake_list(_s: Any, *, limit: int) -> list[Any]:
        captured["limit"] = limit
        return []

    monkeypatch.setattr(tasks, "get_session_factory", lambda: lambda: session)
    monkeypatch.setattr(tasks.events_repo, "list_events_for_pricing_sweep", fake_list)

    tasks.refresh_all_event_pricing()

    assert captured["limit"] == tasks.BATCH_SIZE

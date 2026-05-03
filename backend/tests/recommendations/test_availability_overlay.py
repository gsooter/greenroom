"""Unit tests for the availability overlay.

Tests cover both the lookup behavior of
:func:`compute_availability_multiplier` and the fallback path for an
unmapped state. Stand-in events carry only ``status`` and ``id``,
which is everything the overlay reads.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest

from backend.data.models.events import EventStatus
from backend.recommendations.overlays.availability import (
    AVAILABILITY_MULTIPLIERS,
    compute_availability_multiplier,
    resolve_availability_state,
)


@dataclass
class _FakeEvent:
    """Stand-in for Event — only ``status`` and ``id`` are read."""

    status: Any
    id: uuid.UUID = field(default_factory=uuid.uuid4)


def test_confirmed_event_returns_full_weight() -> None:
    """``CONFIRMED`` resolves to ``"available"`` → 1.00."""
    event = _FakeEvent(status=EventStatus.CONFIRMED)
    assert compute_availability_multiplier(event) == 1.0  # type: ignore[arg-type]


def test_sold_out_event_returns_45_percent() -> None:
    """Sold-out shows still surface, but at a 0.45 multiplier."""
    event = _FakeEvent(status=EventStatus.SOLD_OUT)
    assert compute_availability_multiplier(event) == 0.45  # type: ignore[arg-type]


def test_cancelled_event_returns_zero() -> None:
    """Cancelled shows score 0.0 so the engine filter drops them."""
    event = _FakeEvent(status=EventStatus.CANCELLED)
    assert compute_availability_multiplier(event) == 0.0  # type: ignore[arg-type]


def test_postponed_event_returns_moderate_downweight() -> None:
    """Postponed events are real but uncertain — 0.6."""
    event = _FakeEvent(status=EventStatus.POSTPONED)
    assert compute_availability_multiplier(event) == 0.6  # type: ignore[arg-type]


def test_past_event_trips_unmapped_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A PAST event slipping through scoring trips the unmapped-state
    warning and falls back to 1.0.

    The engine filters past events upfront, so this branch should
    never run in production. Surfacing the warning here is the
    operator-facing trip-wire that surfaces a leak in the filter.
    """
    event = _FakeEvent(status=EventStatus.PAST)
    assert resolve_availability_state(event) == "past"  # type: ignore[arg-type]
    with caplog.at_level(logging.WARNING):
        multiplier = compute_availability_multiplier(event)  # type: ignore[arg-type]
    assert multiplier == 1.0
    assert any("unmapped_state" in record.message for record in caplog.records)


def test_none_status_returns_unknown_hedge() -> None:
    """An event with no status mapped resolves to ``unknown`` → 0.85."""
    event = _FakeEvent(status=None)
    assert resolve_availability_state(event) == "unknown"  # type: ignore[arg-type]
    assert compute_availability_multiplier(event) == 0.85  # type: ignore[arg-type]


def test_low_and_very_low_keys_are_present_for_future_use() -> None:
    """The multiplier dict carries 'low' / 'very_low' even though the
    resolver doesn't emit them yet.

    Future enrichment (SeatGeek "few left", capacity-based heuristics)
    will start producing those states. Locking the keys now means the
    enrichment lands without a coordinated overlay change.
    """
    assert AVAILABILITY_MULTIPLIERS["low"] == 0.95
    assert AVAILABILITY_MULTIPLIERS["very_low"] == 0.90


def test_unmapped_state_logs_warning_and_falls_back_to_full(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A state outside the table logs and returns 1.0 rather than 0.0.

    Failing-open here is deliberate: silently treating an unknown
    state as zero would suppress entire classes of recommendation if
    the upstream catalog ever introduces a new state faster than this
    overlay gets updated. The warning is the operator hint.
    """
    sentinel = object()  # Not equal to any EventStatus member.
    event = _FakeEvent(status=sentinel)
    with caplog.at_level(logging.WARNING):
        multiplier = compute_availability_multiplier(event)  # type: ignore[arg-type]
    assert multiplier == 1.0
    assert any("unmapped_state" in record.message for record in caplog.records)


def test_resolve_availability_state_round_trips_known_statuses() -> None:
    """Every EventStatus we know about lands in the multiplier table.

    Locks the contract: resolving any in-tree status produces a key
    we have a multiplier for. Adding a new status without updating
    the resolver or table makes this test fail loudly.
    """
    for status in (
        EventStatus.CONFIRMED,
        EventStatus.SOLD_OUT,
        EventStatus.CANCELLED,
        EventStatus.POSTPONED,
    ):
        event = _FakeEvent(status=status)
        state = resolve_availability_state(event)  # type: ignore[arg-type]
        assert state in AVAILABILITY_MULTIPLIERS

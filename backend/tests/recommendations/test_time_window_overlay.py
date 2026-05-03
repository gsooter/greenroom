"""Unit tests for the time-window overlay.

The overlay reads only ``event.starts_at`` and a reference ``now``,
so tests use a tiny stand-in event and a frozen ``now`` to make the
curve breakpoints unambiguous.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from backend.recommendations.overlays.time_window import (
    PAST_EVENT_MULTIPLIER,
    compute_time_window_multiplier,
)


@dataclass
class _FakeEvent:
    """Stand-in for Event — only ``starts_at`` is read."""

    starts_at: datetime


_NOW = datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)


def _at(*, days: float = 0, hours: float = 0) -> datetime:
    """Return ``_NOW`` shifted by ``days`` and ``hours`` (can be negative).

    Args:
        days: Whole or fractional days to add to the reference now.
        hours: Whole or fractional hours to add.

    Returns:
        A timezone-aware datetime offset from the test's frozen now.
    """
    return _NOW + timedelta(days=days, hours=hours)


def test_event_tonight_returns_full_weight() -> None:
    """A show happening tonight gets 1.00."""
    event = _FakeEvent(starts_at=_at(hours=4))
    assert compute_time_window_multiplier(event, _NOW) == 1.0  # type: ignore[arg-type]


def test_event_in_one_week_returns_full_weight() -> None:
    """A show one week out is squarely in the flat 1.0 zone."""
    event = _FakeEvent(starts_at=_at(days=7))
    assert compute_time_window_multiplier(event, _NOW) == 1.0  # type: ignore[arg-type]


def test_event_in_two_months_returns_full_weight() -> None:
    """Two months out — peak ticket-buying window, full weight."""
    event = _FakeEvent(starts_at=_at(days=60))
    assert compute_time_window_multiplier(event, _NOW) == 1.0  # type: ignore[arg-type]


def test_event_just_inside_three_months_returns_full_weight() -> None:
    """The curve's flat zone runs to 3 months inclusive."""
    event = _FakeEvent(starts_at=_at(days=90))
    assert compute_time_window_multiplier(event, _NOW) == 1.0  # type: ignore[arg-type]


def test_event_at_five_months_returns_mid_window_weight() -> None:
    """Five months out — past the flat zone, light downweight."""
    event = _FakeEvent(starts_at=_at(days=150))
    assert compute_time_window_multiplier(event, _NOW) == 0.85  # type: ignore[arg-type]


def test_event_at_nine_months_returns_announcement_weight() -> None:
    """Nine months out — on-sale awareness band."""
    event = _FakeEvent(starts_at=_at(days=270))
    assert compute_time_window_multiplier(event, _NOW) == 0.65  # type: ignore[arg-type]


def test_event_at_eighteen_months_returns_far_future_weight() -> None:
    """Beyond a year — strong downweight, never zero."""
    event = _FakeEvent(starts_at=_at(days=540))
    assert compute_time_window_multiplier(event, _NOW) == 0.4  # type: ignore[arg-type]


def test_past_event_returns_zero() -> None:
    """Past events score 0.0 so the engine filter drops them."""
    event = _FakeEvent(starts_at=_at(days=-2))
    assert (
        compute_time_window_multiplier(event, _NOW) == PAST_EVENT_MULTIPLIER  # type: ignore[arg-type]
    )


def test_event_starting_right_now_treated_as_in_progress() -> None:
    """An event starting at exactly ``now`` is still upcoming.

    A user opening For-You at the same instant a doors-time tick
    fires shouldn't lose the recommendation. The overlay treats
    "delta = 0" as in-window.
    """
    event = _FakeEvent(starts_at=_NOW)
    assert compute_time_window_multiplier(event, _NOW) == 1.0  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "days,expected",
    [
        (1, 1.0),
        (45, 1.0),
        (89, 1.0),
        (91, 0.85),
        (179, 0.85),
        (181, 0.65),
        (364, 0.65),
        (366, 0.4),
    ],
)
def test_curve_breakpoints_align_with_documented_buckets(
    days: float, expected: float
) -> None:
    """Buckets line up with the docstring: 90 / 180 / 365 day cuts.

    Locks the curve so a future "small tweak" can't silently shift a
    breakpoint and quietly change ranking for thousands of users.
    """
    event = _FakeEvent(starts_at=_at(days=days))
    assert compute_time_window_multiplier(event, _NOW) == expected  # type: ignore[arg-type]

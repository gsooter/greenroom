"""Unit tests for the actionability overlay.

The overlay is a pure function of an event, the user's preferred
city, and the user's preferred-city region. Tests use lightweight
stand-ins (no database) so the matrix of cases stays cheap to run
and easy to read.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

import pytest

from backend.recommendations.overlays.actionability import (
    CITY_MATCH_MULTIPLIER,
    DIFFERENT_REGION_MULTIPLIER,
    NO_CITY_PREFERENCE_MULTIPLIER,
    SAME_REGION_MULTIPLIER,
    compute_actionability_multiplier,
)


@dataclass
class _FakeCity:
    """Stand-in for :class:`backend.data.models.cities.City`."""

    id: uuid.UUID
    region_id: uuid.UUID | None


@dataclass
class _FakeVenue:
    """Stand-in for :class:`backend.data.models.venues.Venue`."""

    city: _FakeCity | None


@dataclass
class _FakeEvent:
    """Stand-in for :class:`backend.data.models.events.Event`.

    Carries only the fields the overlay reads — id, venue, venue.city.
    """

    id: uuid.UUID
    venue: _FakeVenue | None


def _make_event(city: _FakeCity | None) -> _FakeEvent:
    """Build a fake event whose venue lives in ``city``.

    Args:
        city: City the event's venue belongs to, or ``None`` to
            simulate a venue with no city loaded (an anomaly worth
            covering separately from the city-with-no-region case).

    Returns:
        A fully wired stand-in event.
    """
    venue = _FakeVenue(city=city)
    return _FakeEvent(id=uuid.uuid4(), venue=venue)


def test_user_with_no_preference_gets_neutral_multiplier() -> None:
    """When the user has no preferred city, every event scores 0.95."""
    dmv_region = uuid.uuid4()
    city = _FakeCity(id=uuid.uuid4(), region_id=dmv_region)
    event = _make_event(city)
    multiplier = compute_actionability_multiplier(
        event,  # type: ignore[arg-type]
        user_preferred_city_id=None,
        user_preferred_city_region_id=None,
    )
    assert multiplier == NO_CITY_PREFERENCE_MULTIPLIER


def test_event_in_preferred_city_returns_full_multiplier() -> None:
    """Same city → 1.00."""
    dc = _FakeCity(id=uuid.uuid4(), region_id=uuid.uuid4())
    event = _make_event(dc)
    multiplier = compute_actionability_multiplier(
        event,  # type: ignore[arg-type]
        user_preferred_city_id=dc.id,
        user_preferred_city_region_id=dc.region_id,
    )
    assert multiplier == CITY_MATCH_MULTIPLIER


def test_baltimore_event_for_dc_user_returns_same_region_multiplier() -> None:
    """Different city, same region → 0.85."""
    dmv_region = uuid.uuid4()
    dc = _FakeCity(id=uuid.uuid4(), region_id=dmv_region)
    baltimore = _FakeCity(id=uuid.uuid4(), region_id=dmv_region)
    event = _make_event(baltimore)
    multiplier = compute_actionability_multiplier(
        event,  # type: ignore[arg-type]
        user_preferred_city_id=dc.id,
        user_preferred_city_region_id=dmv_region,
    )
    assert multiplier == SAME_REGION_MULTIPLIER


def test_richmond_event_for_dc_user_returns_same_region_multiplier() -> None:
    """Richmond is in the DMV — DC users get the same-region multiplier.

    Mirrors the spec's explicit case: the DMV region intentionally
    spans more than just the DC commuter shed so users can see RVA
    shows alongside DC ones.
    """
    dmv_region = uuid.uuid4()
    dc = _FakeCity(id=uuid.uuid4(), region_id=dmv_region)
    richmond = _FakeCity(id=uuid.uuid4(), region_id=dmv_region)
    event = _make_event(richmond)
    multiplier = compute_actionability_multiplier(
        event,  # type: ignore[arg-type]
        user_preferred_city_id=dc.id,
        user_preferred_city_region_id=dmv_region,
    )
    assert multiplier == SAME_REGION_MULTIPLIER


def test_nyc_event_for_dc_user_returns_different_region_multiplier() -> None:
    """Different region entirely → 0.40."""
    dmv_region = uuid.uuid4()
    nyc_region = uuid.uuid4()
    dc = _FakeCity(id=uuid.uuid4(), region_id=dmv_region)
    nyc = _FakeCity(id=uuid.uuid4(), region_id=nyc_region)
    event = _make_event(nyc)
    multiplier = compute_actionability_multiplier(
        event,  # type: ignore[arg-type]
        user_preferred_city_id=dc.id,
        user_preferred_city_region_id=dmv_region,
    )
    assert multiplier == DIFFERENT_REGION_MULTIPLIER


def test_user_preferred_region_unset_with_event_in_other_region() -> None:
    """User's region UUID is None, event is somewhere with a region.

    With no region context for the user, the overlay can't claim
    same-region status, so it falls through to the strong downweight
    — same outcome as an explicit cross-region match.
    """
    user_city = uuid.uuid4()
    event_city = _FakeCity(id=uuid.uuid4(), region_id=uuid.uuid4())
    event = _make_event(event_city)
    multiplier = compute_actionability_multiplier(
        event,  # type: ignore[arg-type]
        user_preferred_city_id=user_city,
        user_preferred_city_region_id=None,
    )
    assert multiplier == DIFFERENT_REGION_MULTIPLIER


def test_event_city_without_region_logs_warning_and_returns_different_region(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A city with no region_id is an anomaly — log + fall back to 0.40."""
    dc_user_city = uuid.uuid4()
    dmv_region = uuid.uuid4()
    orphan_city = _FakeCity(id=uuid.uuid4(), region_id=None)
    event = _make_event(orphan_city)
    with caplog.at_level(logging.WARNING):
        multiplier = compute_actionability_multiplier(
            event,  # type: ignore[arg-type]
            user_preferred_city_id=dc_user_city,
            user_preferred_city_region_id=dmv_region,
        )
    assert multiplier == DIFFERENT_REGION_MULTIPLIER
    assert any("city_missing_region" in record.message for record in caplog.records)


def test_event_with_no_venue_logs_warning_and_returns_different_region(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An event without a loaded venue logs and falls back to 0.40.

    The engine should always pre-load venue.city, so reaching this
    branch indicates a wiring bug. Test guards that the overlay never
    raises in production even when fed a degenerate input.
    """
    user_city = uuid.uuid4()
    event = _FakeEvent(id=uuid.uuid4(), venue=None)
    with caplog.at_level(logging.WARNING):
        multiplier = compute_actionability_multiplier(
            event,  # type: ignore[arg-type]
            user_preferred_city_id=user_city,
            user_preferred_city_region_id=uuid.uuid4(),
        )
    assert multiplier == DIFFERENT_REGION_MULTIPLIER
    assert any("event_missing_city" in record.message for record in caplog.records)


def test_multiplier_constants_are_ordered_as_documented() -> None:
    """Multiplier constants stay in the documented strict order.

    Locks the doc-string promise: city > same-region > no-preference >
    different-region. Any change here demands a refresh of the
    callers' assumptions and the DECISIONS_ARCHIVE rationale.
    """
    assert CITY_MATCH_MULTIPLIER == 1.0
    assert SAME_REGION_MULTIPLIER < CITY_MATCH_MULTIPLIER
    assert NO_CITY_PREFERENCE_MULTIPLIER < CITY_MATCH_MULTIPLIER
    assert DIFFERENT_REGION_MULTIPLIER < SAME_REGION_MULTIPLIER
    assert DIFFERENT_REGION_MULTIPLIER < NO_CITY_PREFERENCE_MULTIPLIER

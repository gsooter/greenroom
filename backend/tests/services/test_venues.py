"""Unit tests for :mod:`backend.services.venues`."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.core.exceptions import NotFoundError, ValidationError
from backend.services import venues as venues_service


@dataclass
class _FakeCity:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    name: str = "Washington"
    slug: str = "washington-dc"
    state: str = "DC"
    region: str = "DMV"


@dataclass
class _FakeVenue:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    city_id: uuid.UUID = field(default_factory=uuid.uuid4)
    city: _FakeCity | None = field(default_factory=_FakeCity)
    name: str = "Black Cat"
    slug: str = "black-cat"
    address: str | None = "1811 14th St NW"
    latitude: float | None = 38.915
    longitude: float | None = -77.032
    capacity: int | None = 700
    website_url: str | None = "https://blackcatdc.com"
    description: str | None = "A cornerstone DC venue"
    image_url: str | None = None
    tags: list[str] = field(default_factory=lambda: ["indie"])
    is_active: bool = True
    created_at: datetime = field(
        default_factory=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc)
    )
    updated_at: datetime = field(
        default_factory=lambda: datetime(2026, 1, 2, tzinfo=timezone.utc)
    )


def test_get_venue_raises_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        venues_service.venues_repo, "get_venue_by_id", lambda _s, _i: None
    )
    with pytest.raises(NotFoundError):
        venues_service.get_venue(MagicMock(), uuid.uuid4())


def test_get_venue_returns_row(monkeypatch: pytest.MonkeyPatch) -> None:
    venue = _FakeVenue()
    monkeypatch.setattr(
        venues_service.venues_repo, "get_venue_by_id", lambda _s, _i: venue
    )
    assert venues_service.get_venue(MagicMock(), venue.id) is venue  # type: ignore[arg-type]


def test_get_venue_by_slug_raises_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        venues_service.venues_repo, "get_venue_by_slug", lambda _s, _v: None
    )
    with pytest.raises(NotFoundError):
        venues_service.get_venue_by_slug(MagicMock(), "missing")


def test_get_venue_by_slug_returns_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    venue = _FakeVenue()
    monkeypatch.setattr(
        venues_service.venues_repo, "get_venue_by_slug", lambda _s, _v: venue
    )
    assert (
        venues_service.get_venue_by_slug(MagicMock(), venue.slug) is venue  # type: ignore[arg-type]
    )


def test_list_venues_requires_city_or_region() -> None:
    """A global list without either filter is a validation error."""
    with pytest.raises(ValidationError):
        venues_service.list_venues(MagicMock())


def test_list_venues_rejects_oversized_per_page() -> None:
    with pytest.raises(ValidationError):
        venues_service.list_venues(
            MagicMock(), region="DMV", per_page=101
        )


def test_list_venues_forwards_filters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_list(_session: Any, **kwargs: Any) -> tuple[list[Any], int]:
        captured.update(kwargs)
        return [], 0

    monkeypatch.setattr(venues_service.venues_repo, "list_venues", fake_list)
    cid = uuid.uuid4()
    venues_service.list_venues(
        MagicMock(), city_id=cid, active_only=False, page=3, per_page=25
    )
    assert captured["city_id"] == cid
    assert captured["active_only"] is False
    assert captured["page"] == 3
    assert captured["per_page"] == 25


def test_serialize_venue_full_payload() -> None:
    venue = _FakeVenue()
    payload = venues_service.serialize_venue(venue)  # type: ignore[arg-type]
    assert payload["id"] == str(venue.id)
    assert payload["name"] == "Black Cat"
    assert payload["city"]["region"] == "DMV"
    assert payload["tags"] == ["indie"]


def test_serialize_venue_handles_city_unloaded() -> None:
    venue = _FakeVenue(city=None)
    payload = venues_service.serialize_venue(venue)  # type: ignore[arg-type]
    assert payload["city"] is None


def test_serialize_venue_summary_is_compact() -> None:
    venue = _FakeVenue()
    payload = venues_service.serialize_venue_summary(venue)  # type: ignore[arg-type]
    assert "description" not in payload
    assert "capacity" not in payload
    assert payload["name"] == "Black Cat"
    assert payload["city"]["slug"] == "washington-dc"

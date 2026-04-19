"""Unit tests for :mod:`backend.services.cities`."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from unittest.mock import MagicMock

import pytest

from backend.core.exceptions import NotFoundError
from backend.services import cities as cities_service


@dataclass
class _FakeCity:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    name: str = "Washington"
    slug: str = "washington-dc"
    state: str = "DC"
    region: str = "DMV"
    timezone: str = "America/New_York"
    description: str | None = "The capital."
    is_active: bool = True


def test_list_cities_forwards_region_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_list(_session: object, *, region: str | None) -> list[object]:
        captured["region"] = region
        return [_FakeCity()]

    monkeypatch.setattr(cities_service.cities_repo, "list_active_cities", fake_list)
    result = cities_service.list_cities(MagicMock(), region="DMV")
    assert captured["region"] == "DMV"
    assert len(result) == 1


def test_get_city_by_slug_raises_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cities_service.cities_repo, "get_city_by_slug", lambda _s, _v: None
    )
    with pytest.raises(NotFoundError):
        cities_service.get_city_by_slug(MagicMock(), "nowhere")


def test_get_city_by_slug_returns_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    city = _FakeCity()
    monkeypatch.setattr(
        cities_service.cities_repo,
        "get_city_by_slug",
        lambda _s, _v: city,
    )
    assert cities_service.get_city_by_slug(MagicMock(), "x") is city  # type: ignore[arg-type]


def test_serialize_city_emits_all_fields() -> None:
    city = _FakeCity()
    payload = cities_service.serialize_city(city)  # type: ignore[arg-type]
    assert payload["id"] == str(city.id)
    assert payload["region"] == "DMV"
    assert payload["is_active"] is True

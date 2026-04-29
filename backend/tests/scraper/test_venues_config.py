"""Validation tests for :mod:`backend.scraper.config.venues`.

The venue config is a single source of truth — any drift between it and
the seed file (cities, metadata) silently produces broken scrapers.
These tests lock in the structural invariants so that future edits fail
loudly instead of quietly.
"""

from __future__ import annotations

import dataclasses
import importlib

import pytest

from backend.scraper.config.venues import (
    VENUE_CONFIGS,
    VenueScraperConfig,
    get_configs_by_city,
    get_configs_by_region,
    get_enabled_configs,
    get_venue_config,
)
from backend.scripts.seed_dmv import DMV_CITY_SEEDS, VENUE_METADATA


def test_no_duplicate_venue_slugs() -> None:
    """Every venue_slug appears exactly once across VENUE_CONFIGS."""
    slugs = [c.venue_slug for c in VENUE_CONFIGS]
    duplicates = {s for s in slugs if slugs.count(s) > 1}
    assert duplicates == set(), f"duplicate venue slugs: {sorted(duplicates)}"


def test_every_city_slug_has_a_seed() -> None:
    """Every venue's city_slug must be present in DMV_CITY_SEEDS.

    Otherwise the seed script silently drops the venue with a logged
    error and the scraper never runs against a live row.
    """
    seeded = {seed.slug for seed in DMV_CITY_SEEDS}
    referenced = {c.city_slug for c in VENUE_CONFIGS}
    missing = referenced - seeded
    assert missing == set(), (
        f"city_slugs referenced by VENUE_CONFIGS but missing from "
        f"DMV_CITY_SEEDS: {sorted(missing)}"
    )


def test_every_venue_has_metadata() -> None:
    """Every venue_slug must have an entry in VENUE_METADATA.

    Without metadata the seeded venue row has no address, lat/lng, or
    website, which breaks the venue card and map.
    """
    referenced = {c.venue_slug for c in VENUE_CONFIGS}
    missing = referenced - set(VENUE_METADATA.keys())
    assert missing == set(), (
        f"venues missing metadata in VENUE_METADATA: {sorted(missing)}"
    )


def test_every_scraper_class_is_importable() -> None:
    """Every scraper_class must resolve to a real importable class.

    A typo in the dotted path silently breaks one venue's scraper at
    runtime; this test catches it before the next nightly run.
    """
    for cfg in VENUE_CONFIGS:
        module_path, _, class_name = cfg.scraper_class.rpartition(".")
        module = importlib.import_module(module_path)
        assert hasattr(module, class_name), (
            f"{cfg.venue_slug}: {cfg.scraper_class} not found"
        )


def test_ticketmaster_configs_carry_venue_id() -> None:
    """TM-backed venues must carry a Discovery API venue_id in platform_config."""
    for cfg in VENUE_CONFIGS:
        if cfg.scraper_class.endswith("TicketmasterScraper"):
            assert cfg.platform_config.get("venue_id"), (
                f"{cfg.venue_slug}: Ticketmaster config missing venue_id"
            )


def test_dice_configs_carry_url_and_external_id() -> None:
    """Dice-backed venues must carry both venue_external_id and dice_venue_url."""
    for cfg in VENUE_CONFIGS:
        if cfg.scraper_class.endswith("DiceScraper"):
            assert cfg.platform_config.get("venue_external_id"), (
                f"{cfg.venue_slug}: Dice config missing venue_external_id"
            )
            assert cfg.platform_config.get("dice_venue_url"), (
                f"{cfg.venue_slug}: Dice config missing dice_venue_url"
            )


# ---------------------------------------------------------------------------
# 2026-04-25 audit lock-ins — the venues added after the Discovery-API
# audit are pinned here so we don't accidentally drop them in a refactor.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "venue_slug,venue_id,city_slug,region",
    [
        ("wolf-trap-filene-center", "KovZpZAEetJA", "vienna-va", "DMV"),
        (
            "the-theater-mgm-national-harbor",
            "KovZ917A5LV",
            "national-harbor-md",
            "DMV",
        ),
        ("music-center-strathmore", "KovZpZA1eeIA", "north-bethesda-md", "DMV"),
        ("kennedy-center-concert-hall", "KovZpZA1JAFA", "washington-dc", "DMV"),
        ("tally-ho-theater", "KovZ917AJfx", "leesburg-va", "DMV"),
        ("state-theatre-falls-church", "KovZpZA1tInA", "falls-church-va", "DMV"),
        ("ember-music-hall", "Z7r9jZaAqh", "richmond-va", "RVA"),
        ("innsbrook-pavilion", "ZFr9jZdaAk", "glen-allen-va", "RVA"),
    ],
)
def test_audit_added_venue_registered(
    venue_slug: str, venue_id: str, city_slug: str, region: str
) -> None:
    """Venues added in the 2026-04-25 audit are present and correctly wired."""
    cfg = get_venue_config(venue_slug)
    assert cfg is not None, f"{venue_slug} missing from VENUE_CONFIGS"
    assert cfg.platform_config.get("venue_id") == venue_id
    assert cfg.city_slug == city_slug
    assert cfg.region == region
    assert cfg.scraper_class.endswith("TicketmasterScraper")


def test_rams_head_live_disabled_pending_investigation() -> None:
    """Rams Head Live! returned 0 upcoming events in the 2026-04-25 audit.

    The venue's status (closed, rebranded, or moved off Ticketmaster) is
    still under investigation, so the scraper is parked rather than
    deleted to preserve the historical config.
    """
    cfg = get_venue_config("rams-head-live")
    assert cfg is not None
    assert cfg.enabled is False, (
        "rams-head-live should be disabled until the zero-event status is investigated"
    )
    assert cfg not in get_enabled_configs(), (
        "disabled venue must not appear in get_enabled_configs"
    )


def test_venue_config_is_frozen_dataclass() -> None:
    """VenueScraperConfig is frozen — guards against accidental mutation
    of the shared global config list at runtime."""
    cfg = VENUE_CONFIGS[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.enabled = False  # type: ignore[misc]
    assert isinstance(cfg, VenueScraperConfig)


def test_get_venue_config_returns_none_for_unknown_slug() -> None:
    """Unknown slugs resolve to None rather than raising."""
    assert get_venue_config("does-not-exist") is None


def test_get_enabled_configs_filters_by_region_and_city() -> None:
    """Filters compose: region narrows, city further narrows the result."""
    rva = get_enabled_configs(region="RVA")
    assert rva, "expected at least one enabled RVA venue"
    assert all(c.region == "RVA" for c in rva)

    richmond = get_enabled_configs(region="RVA", city_slug="richmond-va")
    assert all(c.city_slug == "richmond-va" for c in richmond)
    assert {c.venue_slug for c in richmond} <= {c.venue_slug for c in rva}


def test_get_configs_by_region_groups_every_venue() -> None:
    """The grouped view contains the full venue list (enabled or not)."""
    by_region = get_configs_by_region()
    flat = [c for cfgs in by_region.values() for c in cfgs]
    assert len(flat) == len(VENUE_CONFIGS)


def test_get_configs_by_city_groups_every_venue() -> None:
    """City grouping is a partition — every venue appears exactly once."""
    by_city = get_configs_by_city()
    flat = [c for cfgs in by_city.values() for c in cfgs]
    assert len(flat) == len(VENUE_CONFIGS)
    assert {c.venue_slug for c in flat} == {c.venue_slug for c in VENUE_CONFIGS}

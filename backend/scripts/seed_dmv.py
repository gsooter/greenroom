"""Seed DMV (DC / Maryland / Virginia) cities and venues.

Idempotent: re-running will not duplicate rows. Looks up each city and
venue by slug and creates it only if absent. Venue rows are sourced
from `backend.scraper.config.venues.VENUE_CONFIGS`, so adding a new
venue there and re-running this script is the normal workflow.

Usage:
    python -m backend.scripts.seed_dmv
"""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from backend.core.database import get_session_factory
from backend.core.logging import get_logger
from backend.data.models.cities import City
from backend.data.models.venues import Venue
from backend.data.repositories import cities as cities_repo
from backend.data.repositories import venues as venues_repo
from backend.scraper.config.venues import VENUE_CONFIGS, VenueScraperConfig

logger = get_logger(__name__)


@dataclass(frozen=True)
class CitySeed:
    """Seed data for a single DMV city.

    Attributes:
        name: Display name of the city.
        slug: URL-safe identifier.
        state: US state or district abbreviation.
        region: Marketing region grouping.
        timezone: IANA timezone string.
        description: Optional SEO description.
    """

    name: str
    slug: str
    state: str
    region: str
    timezone: str = "America/New_York"
    description: str | None = None


# ----------------------------------------------------------------------------
# Cities referenced by VENUE_CONFIGS.city_slug values.
# ----------------------------------------------------------------------------

DMV_CITY_SEEDS: list[CitySeed] = [
    CitySeed(
        name="Washington",
        slug="washington-dc",
        state="DC",
        region="DMV",
        description="Live music, concerts, and events in Washington, DC.",
    ),
    CitySeed(
        name="Columbia",
        slug="columbia-md",
        state="MD",
        region="DMV",
        description="Live music and concerts in Columbia, Maryland.",
    ),
    CitySeed(
        name="Silver Spring",
        slug="silver-spring-md",
        state="MD",
        region="DMV",
        description="Live music and concerts in Silver Spring, Maryland.",
    ),
    CitySeed(
        name="Baltimore",
        slug="baltimore-md",
        state="MD",
        region="DMV",
        description="Live music and concerts in Baltimore, Maryland.",
    ),
    CitySeed(
        name="Tysons",
        slug="tysons-va",
        state="VA",
        region="DMV",
        description="Live music and concerts in Tysons, Virginia.",
    ),
    CitySeed(
        name="Alexandria",
        slug="alexandria-va",
        state="VA",
        region="DMV",
        description="Live music and concerts in Alexandria, Virginia.",
    ),
    CitySeed(
        name="Vienna",
        slug="vienna-va",
        state="VA",
        region="DMV",
        description="Live music and concerts in Vienna, Virginia.",
    ),
]


def _upsert_city(session: Session, seed: CitySeed) -> tuple[City, bool]:
    """Create a city if absent, otherwise refresh core fields in place.

    Overwrites name, state, region, timezone, and description so the
    DB reflects the current seed definition. Slug and id stay stable.

    Args:
        session: Active SQLAlchemy session.
        seed: Seed data for the city.

    Returns:
        Tuple of (City, created) where created is True on insert.
    """
    existing = cities_repo.get_city_by_slug(session, seed.slug)
    if existing is not None:
        updates: dict[str, str | None] = {}
        if existing.name != seed.name:
            updates["name"] = seed.name
        if existing.state != seed.state:
            updates["state"] = seed.state
        if existing.region != seed.region:
            updates["region"] = seed.region
        if existing.timezone != seed.timezone:
            updates["timezone"] = seed.timezone
        if existing.description != seed.description:
            updates["description"] = seed.description
        if updates:
            cities_repo.update_city(session, existing, **updates)
            logger.info(
                "Updated city '%s' (%s): %s",
                existing.slug,
                seed.slug,
                sorted(updates.keys()),
            )
        return existing, False

    city = cities_repo.create_city(
        session,
        name=seed.name,
        slug=seed.slug,
        state=seed.state,
        region=seed.region,
        timezone=seed.timezone,
        description=seed.description,
    )
    return city, True


def _upsert_venue(
    session: Session,
    config: VenueScraperConfig,
    city: City,
) -> tuple[Venue, str]:
    """Create or refresh a venue row from its scraper config.

    Pulls the Ticketmaster venue_id out of platform_config when present
    so the venue row carries its external ID for future lookups. On
    re-runs, backfills display_name for rows that were seeded before
    display_name existed (name == slug).

    Args:
        session: Active SQLAlchemy session.
        config: The venue's scraper configuration.
        city: The City row this venue is assigned to.

    Returns:
        Tuple of (Venue, outcome) where outcome is "created", "updated",
        or "skipped".
    """
    external_ids: dict[str, str] = {}
    tm_id = config.platform_config.get("venue_id")
    if config.scraper_class.endswith("TicketmasterScraper") and tm_id:
        external_ids["ticketmaster"] = tm_id

    existing = venues_repo.get_venue_by_slug(session, config.venue_slug)
    if existing is not None:
        updates: dict[str, object] = {}
        if existing.name == existing.slug and config.display_name:
            updates["name"] = config.display_name
        if external_ids and existing.external_ids != external_ids:
            updates["external_ids"] = external_ids
        if updates:
            venues_repo.update_venue(session, existing, **updates)
            return existing, "updated"
        return existing, "skipped"

    venue = venues_repo.create_venue(
        session,
        city_id=city.id,
        name=config.display_name or config.venue_slug,
        slug=config.venue_slug,
        external_ids=external_ids,
    )
    return venue, "created"


def seed() -> dict[str, int]:
    """Run the full DMV seed.

    Inserts cities and venues that don't exist yet. Commits once at
    the end so a failure leaves the DB unchanged.

    Returns:
        Dictionary with counts of cities and venues created and skipped.
    """
    factory = get_session_factory()
    session = factory()

    cities_created = 0
    cities_skipped = 0
    venues_created = 0
    venues_updated = 0
    venues_skipped = 0
    venues_missing_city = 0

    try:
        city_by_slug: dict[str, City] = {}
        for seed_row in DMV_CITY_SEEDS:
            city, created = _upsert_city(session, seed_row)
            city_by_slug[seed_row.slug] = city
            if created:
                cities_created += 1
                logger.info("Created city '%s' (%s).", city.name, city.slug)
            else:
                cities_skipped += 1

        for config in VENUE_CONFIGS:
            city = city_by_slug.get(config.city_slug)
            if city is None:
                logger.error(
                    "Venue '%s' references unknown city_slug '%s'. Skipping.",
                    config.venue_slug,
                    config.city_slug,
                )
                venues_missing_city += 1
                continue

            venue, outcome = _upsert_venue(session, config, city)
            if outcome == "created":
                venues_created += 1
                logger.info(
                    "Created venue '%s' in %s.", venue.name, city.slug
                )
            elif outcome == "updated":
                venues_updated += 1
                logger.info(
                    "Updated venue '%s' in %s.", venue.name, city.slug
                )
            else:
                venues_skipped += 1

        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    summary = {
        "cities_created": cities_created,
        "cities_skipped": cities_skipped,
        "venues_created": venues_created,
        "venues_updated": venues_updated,
        "venues_skipped": venues_skipped,
        "venues_missing_city": venues_missing_city,
    }
    logger.info("Seed complete: %s", summary)
    return summary


if __name__ == "__main__":
    seed()

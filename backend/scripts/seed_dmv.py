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

# ----------------------------------------------------------------------------
# Venue metadata — hand-curated address/lat/lng/website/capacity for every
# DMV venue. Kept here (not in scraper config) because these fields describe
# the venue for display, not for scraping. Re-running the seed backfills
# these columns on existing rows; any venue without an entry here gets no
# metadata and the VenueCard falls back to a tinted name block.
#
# image_url is intentionally left unset for all venues: stable, rights-clean
# marquee photos are hard to source, and the card degrades to a green name
# block, which reads better than a broken <img>.
# ----------------------------------------------------------------------------
# Coordinates resolved via Apple Maps ``/v1/geocode`` on 2026-04-24 and
# recorded at 6 decimal places (~11 cm). The previous 4-dp values drifted
# 45-1086 m per venue — most visibly Echostage (1 km), Merriweather
# (730 m), and 9:30 Club (350 m west, putting the pin 2.5 blocks off on
# the live map).
VENUE_METADATA: dict[str, dict[str, object]] = {
    "930-club": {
        "address": "815 V St NW, Washington, DC 20001",
        "latitude": 38.918047,
        "longitude": -77.023635,
        "website_url": "https://www.930.com/",
        "capacity": 1200,
    },
    "the-anthem": {
        "address": "901 Wharf St SW, Washington, DC 20024",
        "latitude": 38.879985,
        "longitude": -77.025907,
        "website_url": "https://theanthemdc.com/",
        "capacity": 6000,
    },
    "echostage": {
        "address": "2135 Queens Chapel Rd NE, Washington, DC 20018",
        "latitude": 38.919906,
        "longitude": -76.972427,
        "website_url": "https://echostage.com/",
        "capacity": 3000,
    },
    "howard-theatre": {
        "address": "620 T St NW, Washington, DC 20001",
        "latitude": 38.915279,
        "longitude": -77.021101,
        "website_url": "https://thehowardtheatre.com/",
        "capacity": 1100,
    },
    "lincoln-theatre": {
        "address": "1215 U St NW, Washington, DC 20009",
        "latitude": 38.917405,
        "longitude": -77.028987,
        "website_url": "https://thelincolndc.com/",
        "capacity": 1200,
    },
    "union-stage": {
        "address": "740 Water St SW, Washington, DC 20024",
        "latitude": 38.878703,
        "longitude": -77.024093,
        "website_url": "https://www.unionstage.com/",
        "capacity": 450,
    },
    "black-cat": {
        "address": "1811 14th St NW, Washington, DC 20009",
        "latitude": 38.914589,
        "longitude": -77.031553,
        "website_url": "https://blackcatdc.com/",
        "capacity": 700,
    },
    "the-hamilton": {
        "address": "600 14th St NW, Washington, DC 20005",
        "latitude": 38.897694,
        "longitude": -77.032276,
        "website_url": "https://live.thehamiltondc.com/",
        "capacity": 700,
    },
    "pearl-street-warehouse": {
        "address": "33 Pearl St SW, Washington, DC 20024",
        "latitude": 38.878762,
        "longitude": -77.024045,
        "website_url": "https://pearlstreetwarehouse.com/",
        "capacity": 350,
    },
    "bethesda-theater": {
        "address": "7719 Wisconsin Ave, Bethesda, MD 20814",
        "latitude": 38.987268,
        "longitude": -77.094436,
        "website_url": "https://www.bethesdatheater.com/",
        "capacity": 500,
    },
    "dc9": {
        "address": "1940 9th St NW, Washington, DC 20001",
        "latitude": 38.916694,
        "longitude": -77.024275,
        "website_url": "https://dc9.club/",
        "capacity": 250,
    },
    # Coordinates for Dice-ticketed DC venues taken from the Place
    # node each venue publishes in its dice.fm/venue/<slug> JSON-LD.
    "berhta": {
        "address": "1237 W Street NE, Washington, DC 20018",
        "latitude": 38.919114,
        "longitude": -76.987664,
        "website_url": "https://dice.fm/venue/berhta-8emn5",
        "capacity": 400,
    },
    "songbyrd": {
        "address": "540 Penn St NE, Washington, DC 20002",
        "latitude": 38.910198,
        "longitude": -76.996431,
        "website_url": "https://www.songbyrddc.com/",
        "capacity": 250,
    },
    "byrdland": {
        "address": "1264 5th St NE, Washington, DC 20002",
        "latitude": 38.908042,
        "longitude": -76.998940,
        "website_url": "https://byrdlandrecords.com/",
        "capacity": 200,
    },
    "comet-ping-pong": {
        "address": "5037 Connecticut Ave NW, Washington, DC 20008",
        "latitude": 38.956002,
        "longitude": -77.069823,
        "website_url": "https://cometpingpong.com/",
        "capacity": 120,
    },
    "flash": {
        "address": "645 Florida Ave NW, Washington, DC 20001",
        "latitude": 38.916288,
        "longitude": -77.021377,
        "website_url": "https://www.flashdc.com/",
        "capacity": 500,
    },
    "pie-shop": {
        "address": "1339 H St NE, Washington, DC 20002",
        "latitude": 38.899836,
        "longitude": -76.986928,
        "website_url": "https://www.pieshopdc.com/",
        "capacity": 150,
    },
    "merriweather-post-pavilion": {
        "address": "10475 Little Patuxent Pkwy, Columbia, MD 21044",
        "latitude": 39.208841,
        "longitude": -76.862733,
        "website_url": "https://www.merriweathermusic.com/",
        "capacity": 19000,
    },
    "the-fillmore-silver-spring": {
        "address": "8656 Colesville Rd, Silver Spring, MD 20910",
        "latitude": 38.997448,
        "longitude": -77.027583,
        "website_url": "https://www.fillmoresilverspring.com/",
        "capacity": 2000,
    },
    "music-center-strathmore": {
        "address": "5301 Tuckerman Lane, North Bethesda, MD 20852",
        "latitude": 39.028302,
        "longitude": -77.101300,
        "website_url": "https://www.strathmore.org/",
        "capacity": 1976,
    },
    "the-theater-mgm-national-harbor": {
        "address": "101 MGM National Ave, National Harbor, MD 20745",
        "latitude": 38.791707,
        "longitude": -77.003002,
        "website_url": "https://mgmnationalharbor.mgmresorts.com/en/entertainment.html",
        "capacity": 3000,
    },
    "rams-head-live": {
        "address": "20 Market Pl, Baltimore, MD 21202",
        "latitude": 39.289261,
        "longitude": -76.607340,
        "website_url": "https://ramsheadlive.com/",
        "capacity": 1500,
    },
    # Apple's geocoder only resolves this building under the McLean
    # postal address; the Tysons variant returns no match.
    "capital-one-hall": {
        "address": "7750 Capital One Tower Rd, McLean, VA 22102",
        "latitude": 38.925500,
        "longitude": -77.211280,
        "website_url": "https://capitalonehall.com/",
        "capacity": 1600,
    },
    "the-birchmere": {
        "address": "3701 Mt Vernon Ave, Alexandria, VA 22305",
        "latitude": 38.840150,
        "longitude": -77.061057,
        "website_url": "https://www.birchmere.com/",
        "capacity": 500,
    },
    "wolf-trap": {
        "address": "1551 Trap Rd, Vienna, VA 22182",
        "latitude": 38.936459,
        "longitude": -77.264480,
        "website_url": "https://www.wolftrap.org/",
        "capacity": 7000,
    },
    "wolf-trap-filene-center": {
        "address": "1551 Trap Rd, Vienna, VA 22182",
        "latitude": 38.933483,
        "longitude": -77.265745,
        "website_url": "https://www.wolftrap.org/the-filene-center.aspx",
        "capacity": 7028,
    },
    "state-theatre-falls-church": {
        "address": "220 N Washington St, Falls Church, VA 22046",
        "latitude": 38.883060,
        "longitude": -77.169868,
        "website_url": "https://www.thestatetheatre.com/",
        "capacity": 850,
    },
    "tally-ho-theater": {
        "address": "19 W Market St, Leesburg, VA 20176",
        "latitude": 39.115700,
        "longitude": -77.565600,
        "website_url": "https://tallyhotheater.com/",
        "capacity": 700,
    },
    # ------------------------------------------------------------------
    # DC — Ticketmaster-ticketed additions (Discovery-API ids resolved
    # against https://app.ticketmaster.com/discovery/v2/venues/<id>.json
    # on 2026-04-24; coordinates pulled from the same response).
    # ------------------------------------------------------------------
    "the-atlantis": {
        "address": "2047 9th St NW, Washington, DC 20001",
        "latitude": 38.918190,
        "longitude": -77.022410,
        "website_url": "https://theatlantis.com/",
        "capacity": 450,
    },
    "warner-theatre-dc": {
        "address": "513 13th St NW, Washington, DC 20004",
        "latitude": 38.896121,
        "longitude": -77.029610,
        "website_url": "https://www.warnertheatredc.com/",
        "capacity": 1850,
    },
    "dar-constitution-hall": {
        "address": "1776 D Street NW, Washington, DC 20006",
        "latitude": 38.894236,
        "longitude": -77.040922,
        "website_url": "https://dar.org/constitution-hall",
        "capacity": 3702,
    },
    "capital-one-arena": {
        "address": "601 F Street NW, Washington, DC 20004",
        "latitude": 38.897412,
        "longitude": -77.020029,
        "website_url": "https://www.capitalonearena.com/",
        "capacity": 20000,
    },
    "lisner-auditorium": {
        "address": "730 21st Street NW, Washington, DC 20052",
        "latitude": 38.899404,
        "longitude": -77.047142,
        "website_url": "https://lisner.gwu.edu/",
        "capacity": 1490,
    },
    "kennedy-center-concert-hall": {
        "address": "2700 F St NW, Washington, DC 20566",
        "latitude": 38.896060,
        "longitude": -77.055198,
        "website_url": "https://www.kennedy-center.org/",
        "capacity": 2465,
    },
    # ------------------------------------------------------------------
    # Baltimore (own region, not DMV)
    # ------------------------------------------------------------------
    "pier-six-pavilion": {
        "address": "731 Eastern Ave, Baltimore, MD 21202",
        "latitude": 39.284033,
        "longitude": -76.604426,
        "website_url": "https://www.piersixpavilion.com/",
        "capacity": 4400,
    },
    "cfg-bank-arena": {
        "address": "201 W Baltimore St, Baltimore, MD 21201",
        "latitude": 39.288494,
        "longitude": -76.618700,
        "website_url": "https://www.cfgbankarena.com/",
        "capacity": 14000,
    },
    "baltimore-soundstage": {
        "address": "124 Market Place, Baltimore, MD 21202",
        "latitude": 39.287637,
        "longitude": -76.607190,
        "website_url": "https://www.baltimoresoundstage.com/",
        "capacity": 1000,
    },
    "the-lyric-baltimore": {
        "address": "140 W Mount Royal Ave, Baltimore, MD 21201",
        "latitude": 39.305619,
        "longitude": -76.618710,
        "website_url": "https://modell-lyric.com/",
        "capacity": 2564,
    },
    "hippodrome-theatre-baltimore": {
        "address": "12 N Eutaw St, Baltimore, MD 21201",
        "latitude": 39.289634,
        "longitude": -76.621342,
        "website_url": "https://baltimorehippodrome.com/",
        "capacity": 2286,
    },
    # Dice-ticketed Baltimore venues. Coordinates from Ticketmaster
    # Discovery API venue records (both clubs are indexed there even
    # though they sell via Dice).
    "the-8x10": {
        "address": "10 E Cross St, Baltimore, MD 21230",
        "latitude": 39.276931,
        "longitude": -76.613705,
        "website_url": "https://www.the8x10.com/",
        "capacity": 300,
    },
    "metro-baltimore": {
        "address": "1700 N Charles St, Baltimore, MD 21201",
        "latitude": 39.308912,
        "longitude": -76.616746,
        "website_url": "https://metrobmore.com/",
        "capacity": 200,
    },
    "ottobar": {
        "address": "2549 N Howard St, Baltimore, MD 21218",
        "latitude": 39.318851,
        "longitude": -76.619517,
        "website_url": "https://theottobar.com/",
        "capacity": 400,
    },
    # ------------------------------------------------------------------
    # Northern Virginia additions
    # ------------------------------------------------------------------
    "the-barns-at-wolf-trap": {
        "address": "1635 Trap Rd, Vienna, VA 22182",
        "latitude": 38.935398,
        "longitude": -77.272102,
        "website_url": "https://www.wolftrap.org/the-barns",
        "capacity": 380,
    },
    "eaglebank-arena": {
        "address": "4500 Patriot Circle, Fairfax, VA 22030",
        "latitude": 38.833051,
        "longitude": -77.309850,
        "website_url": "https://eaglebankarena.com/",
        "capacity": 10000,
    },
    "jiffy-lube-live": {
        "address": "7800 Cellar Door Drive, Bristow, VA 20136",
        "latitude": 38.786120,
        "longitude": -77.587777,
        "website_url": "https://www.livenation.com/venue/KovZpZAEk6JA/jiffy-lube-live-tickets",
        "capacity": 25000,
    },
    # ------------------------------------------------------------------
    # Richmond (RVA) — separate region from DMV
    # ------------------------------------------------------------------
    "the-national-richmond": {
        "address": "708 East Broad St, Richmond, VA 23219",
        "latitude": 37.541836,
        "longitude": -77.435343,
        "website_url": "https://www.thenationalva.com/",
        "capacity": 1500,
    },
    "canal-club": {
        "address": "1545 East Cary St, Richmond, VA 23219",
        "latitude": 37.537405,
        "longitude": -77.437154,
        "website_url": "https://www.thecanalclub.com/",
        "capacity": 600,
    },
    "allianz-amphitheater": {
        "address": "350 Tredegar St, Richmond, VA 23219",
        "latitude": 37.535730,
        "longitude": -77.445760,
        "website_url": "https://www.allianzamphitheaterrva.com/",
        "capacity": 7500,
    },
    "altria-theater": {
        "address": "6 N Laurel St, Richmond, VA 23220",
        "latitude": 37.546521,
        "longitude": -77.451545,
        "website_url": "https://altriatheater.com/",
        "capacity": 3565,
    },
    "carpenter-theatre": {
        "address": "600 East Grace St, Richmond, VA 23219",
        "latitude": 37.541637,
        "longitude": -77.436915,
        "website_url": "https://www.dominionenergycenter.com/",
        "capacity": 1800,
    },
    "the-broadberry": {
        "address": "2729 W Broad St, Richmond, VA 23220",
        "latitude": 37.549801,
        "longitude": -77.459801,
        "website_url": "https://thebroadberry.com/",
        "capacity": 400,
    },
    "ember-music-hall": {
        "address": "309 E Broad St, Richmond, VA 23219",
        "latitude": 37.541100,
        "longitude": -77.434601,
        "website_url": "https://embermusichall.com/",
        "capacity": 700,
    },
    "the-camel": {
        "address": "1621 W Broad St, Richmond, VA 23220",
        "latitude": 37.554410,
        "longitude": -77.457260,
        "website_url": "https://www.thecamel.org/",
        "capacity": 250,
    },
    "innsbrook-pavilion": {
        "address": "4901 Lake Brook Dr, Glen Allen, VA 23060",
        "latitude": 37.683899,
        "longitude": -77.557999,
        "website_url": "https://innsbrookafterhours.com/",
        "capacity": 6500,
    },
}


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
        region="Baltimore",
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
    CitySeed(
        name="Fairfax",
        slug="fairfax-va",
        state="VA",
        region="DMV",
        description="Live music and concerts in Fairfax, Virginia.",
    ),
    CitySeed(
        name="Bristow",
        slug="bristow-va",
        state="VA",
        region="DMV",
        description="Live music and concerts in Bristow, Virginia.",
    ),
    CitySeed(
        name="Falls Church",
        slug="falls-church-va",
        state="VA",
        region="DMV",
        description="Live music and concerts in Falls Church, Virginia.",
    ),
    CitySeed(
        name="Leesburg",
        slug="leesburg-va",
        state="VA",
        region="DMV",
        description="Live music and concerts in Leesburg, Virginia.",
    ),
    CitySeed(
        name="North Bethesda",
        slug="north-bethesda-md",
        state="MD",
        region="DMV",
        description="Live music and concerts in North Bethesda, Maryland.",
    ),
    CitySeed(
        name="Bethesda",
        slug="bethesda-md",
        state="MD",
        region="DMV",
        description="Live music and concerts in Bethesda, Maryland.",
    ),
    CitySeed(
        name="National Harbor",
        slug="national-harbor-md",
        state="MD",
        region="DMV",
        description="Live music and concerts in National Harbor, Maryland.",
    ),
    CitySeed(
        name="Richmond",
        slug="richmond-va",
        state="VA",
        region="RVA",
        description="Live music and concerts in Richmond, Virginia.",
    ),
    CitySeed(
        name="Glen Allen",
        slug="glen-allen-va",
        state="VA",
        region="RVA",
        description="Live music and concerts in Glen Allen, Virginia.",
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

    metadata = VENUE_METADATA.get(config.venue_slug, {})

    existing = venues_repo.get_venue_by_slug(session, config.venue_slug)
    if existing is not None:
        updates: dict[str, object] = {}
        if existing.name == existing.slug and config.display_name:
            updates["name"] = config.display_name
        if external_ids and existing.external_ids != external_ids:
            updates["external_ids"] = external_ids
        for field_name, new_value in metadata.items():
            if getattr(existing, field_name) != new_value:
                updates[field_name] = new_value
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
        **metadata,  # type: ignore[arg-type]
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
            venue_city = city_by_slug.get(config.city_slug)
            if venue_city is None:
                logger.error(
                    "Venue '%s' references unknown city_slug '%s'. Skipping.",
                    config.venue_slug,
                    config.city_slug,
                )
                venues_missing_city += 1
                continue

            venue, outcome = _upsert_venue(session, config, venue_city)
            if outcome == "created":
                venues_created += 1
                logger.info("Created venue '%s' in %s.", venue.name, venue_city.slug)
            elif outcome == "updated":
                venues_updated += 1
                logger.info("Updated venue '%s' in %s.", venue.name, venue_city.slug)
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

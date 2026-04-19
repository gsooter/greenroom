"""Master venue-to-scraper mapping.

This is the single source of truth for which scraper handles each venue.
Any developer can read this one file and understand the entire scraper fleet.

Venues are organized by region, then by city/state within each region.
Each entry maps a venue slug to its scraper configuration.

To add a new venue:
1. Add the venue row to the database (migration or seed).
2. Add an entry here under the correct region and city.
3. If the venue uses an existing platform scraper, no new code needed.
4. If custom logic is required, add scraper/venues/<slug>.py.
5. Update public/llms.txt venue list.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class VenueScraperConfig:
    """Configuration for a single venue's scraper.

    Attributes:
        venue_slug: The venue's slug in the database.
        display_name: Human-readable venue name. Used by seed scripts
            and admin UIs. Scrapers should not rely on this.
        scraper_class: Dotted import path to the scraper class.
        platform_config: Platform-specific parameters passed to the scraper.
        enabled: Whether this venue should be scraped. Defaults to True.
        city_slug: The city slug this venue belongs to (for documentation).
        region: The region this venue belongs to (for documentation).
    """

    venue_slug: str
    display_name: str
    scraper_class: str
    platform_config: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    city_slug: str = ""
    region: str = ""


# Scraper class paths
_TM = "backend.scraper.platforms.ticketmaster.TicketmasterScraper"
_DICE = "backend.scraper.platforms.dice.DiceScraper"
_EVENTBRITE = "backend.scraper.platforms.eventbrite.EventbriteScraper"
_GENERIC = "backend.scraper.platforms.generic_html.GenericHtmlScraper"


# ============================================================================
# DMV — Washington DC / Maryland / Virginia
# ============================================================================

_DMV_VENUES: list[VenueScraperConfig] = [
    # -----------------------------------------------------------------------
    # Washington, DC
    # -----------------------------------------------------------------------
    VenueScraperConfig(
        venue_slug="930-club",
        display_name="9:30 Club",
        scraper_class=_TM,
        platform_config={"venue_id": "KovZpZA7knFA", "venue_name": "9:30 Club"},
        city_slug="washington-dc",
        region="DMV",
    ),
    VenueScraperConfig(
        venue_slug="the-anthem",
        display_name="The Anthem",
        scraper_class=_TM,
        platform_config={"venue_id": "KovZ917A3Y7", "venue_name": "The Anthem"},
        city_slug="washington-dc",
        region="DMV",
    ),
    VenueScraperConfig(
        venue_slug="echostage",
        display_name="Echostage",
        scraper_class=_TM,
        platform_config={"venue_id": "KovZpZAadt7A", "venue_name": "Echostage"},
        city_slug="washington-dc",
        region="DMV",
    ),
    VenueScraperConfig(
        venue_slug="howard-theatre",
        display_name="Howard Theatre",
        scraper_class=_TM,
        platform_config={
            "venue_id": "KovZpZAFavlA",
            "venue_name": "Howard Theatre",
        },
        city_slug="washington-dc",
        region="DMV",
    ),
    VenueScraperConfig(
        venue_slug="lincoln-theatre",
        display_name="Lincoln Theatre",
        scraper_class=_TM,
        platform_config={
            "venue_id": "KovZpZAFk6EA",
            "venue_name": "Lincoln Theatre",
        },
        city_slug="washington-dc",
        region="DMV",
    ),
    VenueScraperConfig(
        venue_slug="union-stage",
        display_name="Union Stage",
        scraper_class=_TM,
        platform_config={
            "venue_id": "KovZ917AQjV",
            "venue_name": "Union Stage",
        },
        city_slug="washington-dc",
        region="DMV",
    ),
    VenueScraperConfig(
        venue_slug="black-cat",
        display_name="Black Cat",
        scraper_class="backend.scraper.venues.black_cat.BlackCatScraper",
        platform_config={},
        enabled=True,
        city_slug="washington-dc",
        region="DMV",
    ),
    VenueScraperConfig(
        venue_slug="dc9",
        display_name="DC9",
        scraper_class=_DICE,
        platform_config={"url": "https://dc9.club/events/"},
        # DC9 embeds a DICE widget on dc9.club/events, but as of
        # 2026-04-17 the widget <div> + <script> are HTML-commented
        # out — no events are published at the source. Blocked until
        # the venue re-enables the widget OR a DICE API scraper
        # implementation + DICE credentials land. See DECISIONS.md
        # entry on JSON-LD-first strategy for context.
        enabled=False,
        city_slug="washington-dc",
        region="DMV",
    ),
    VenueScraperConfig(
        venue_slug="comet-ping-pong",
        display_name="Comet Ping Pong",
        scraper_class="backend.scraper.venues.comet_ping_pong.CometPingPongScraper",
        platform_config={},
        enabled=True,
        city_slug="washington-dc",
        region="DMV",
    ),
    VenueScraperConfig(
        venue_slug="flash",
        display_name="Flash",
        scraper_class=_GENERIC,
        platform_config={"url": "https://www.flashdc.com/"},
        enabled=True,
        city_slug="washington-dc",
        region="DMV",
    ),
    VenueScraperConfig(
        venue_slug="pie-shop",
        display_name="Pie Shop",
        scraper_class="backend.scraper.venues.pie_shop.PieShopScraper",
        platform_config={},
        enabled=True,
        city_slug="washington-dc",
        region="DMV",
    ),
    # -----------------------------------------------------------------------
    # Maryland
    # -----------------------------------------------------------------------
    VenueScraperConfig(
        venue_slug="merriweather-post-pavilion",
        display_name="Merriweather Post Pavilion",
        scraper_class=_TM,
        platform_config={
            "venue_id": "KovZpZA1JkvA",
            "venue_name": "Merriweather Post Pavilion",
        },
        city_slug="columbia-md",
        region="DMV",
    ),
    VenueScraperConfig(
        venue_slug="the-fillmore-silver-spring",
        display_name="The Fillmore Silver Spring",
        scraper_class=_TM,
        platform_config={
            "venue_id": "KovZpZA6tFlA",
            "venue_name": "The Fillmore Silver Spring",
        },
        city_slug="silver-spring-md",
        region="DMV",
    ),
    VenueScraperConfig(
        venue_slug="rams-head-live",
        display_name="Rams Head Live!",
        scraper_class=_TM,
        platform_config={
            "venue_id": "KovZpZAFk6tA",
            "venue_name": "Rams Head Live!",
        },
        city_slug="baltimore-md",
        region="DMV",
    ),
    # -----------------------------------------------------------------------
    # Virginia
    # -----------------------------------------------------------------------
    VenueScraperConfig(
        venue_slug="capital-one-hall",
        display_name="Capital One Hall",
        scraper_class=_TM,
        platform_config={
            "venue_id": "KovZpZAJ6nlA",
            "venue_name": "Capital One Hall",
        },
        city_slug="tysons-va",
        region="DMV",
    ),
    VenueScraperConfig(
        venue_slug="the-birchmere",
        display_name="The Birchmere",
        scraper_class=_TM,
        platform_config={
            "venue_id": "KovZpa3rme",
            "venue_name": "The Birchmere",
        },
        city_slug="alexandria-va",
        region="DMV",
    ),
    VenueScraperConfig(
        venue_slug="wolf-trap",
        display_name="Wolf Trap",
        scraper_class=_TM,
        platform_config={
            "venue_id": "KovZpZAtvJeA",
            "venue_name": "Wolf Trap",
        },
        city_slug="vienna-va",
        region="DMV",
    ),
]


# ============================================================================
# All venue configs — combined from all regions
# ============================================================================

VENUE_CONFIGS: list[VenueScraperConfig] = [
    *_DMV_VENUES,
]


# ============================================================================
# Lookup helpers
# ============================================================================


def get_venue_config(venue_slug: str) -> VenueScraperConfig | None:
    """Look up the scraper configuration for a venue by slug.

    Args:
        venue_slug: The venue's slug identifier.

    Returns:
        The VenueScraperConfig if found, otherwise None.
    """
    for config in VENUE_CONFIGS:
        if config.venue_slug == venue_slug:
            return config
    return None


def get_enabled_configs(
    *,
    region: str | None = None,
    city_slug: str | None = None,
) -> list[VenueScraperConfig]:
    """Get enabled venue scraper configurations with optional filters.

    Args:
        region: Filter to a specific region (e.g., "DMV").
        city_slug: Filter to a specific city.

    Returns:
        List of matching enabled VenueScraperConfig instances.
    """
    configs = [c for c in VENUE_CONFIGS if c.enabled]
    if region is not None:
        configs = [c for c in configs if c.region == region]
    if city_slug is not None:
        configs = [c for c in configs if c.city_slug == city_slug]
    return configs


def get_configs_by_region() -> dict[str, list[VenueScraperConfig]]:
    """Get all venue configs organized by region.

    Returns:
        Dictionary mapping region names to lists of VenueScraperConfig.
    """
    by_region: dict[str, list[VenueScraperConfig]] = {}
    for config in VENUE_CONFIGS:
        by_region.setdefault(config.region, []).append(config)
    return by_region


def get_configs_by_city() -> dict[str, list[VenueScraperConfig]]:
    """Get all venue configs organized by city slug.

    Returns:
        Dictionary mapping city slugs to lists of VenueScraperConfig.
    """
    by_city: dict[str, list[VenueScraperConfig]] = {}
    for config in VENUE_CONFIGS:
        by_city.setdefault(config.city_slug, []).append(config)
    return by_city

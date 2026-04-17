"""BaseScraper abstract class.

All scrapers must extend this class and implement the scrape method.
"""

from abc import ABC, abstractmethod
from collections.abc import Iterator

from backend.scraper.base.models import RawEvent


class BaseScraper(ABC):
    """Abstract base class for all venue and platform scrapers.

    Subclasses must implement the scrape method which yields RawEvent
    instances. Scrapers never write to the database directly.

    Attributes:
        source_platform: Class-level identifier for the platform this
            scraper ingests from. Used by the runner to set
            ``Event.source_platform`` on newly created events and to
            disambiguate external IDs across sources. Subclasses must
            override this with a stable lowercase string such as
            ``"ticketmaster"`` or ``"black_cat"``.
    """

    source_platform: str = "generic"

    @abstractmethod
    def scrape(self) -> Iterator[RawEvent]:
        """Scrape events from the source.

        Yields:
            RawEvent instances representing discovered events.
        """
        ...

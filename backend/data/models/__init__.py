"""SQLAlchemy ORM models.

Import all models here so they register with Base.metadata
for Alembic autogenerate and relationship resolution.
"""

from backend.data.models.cities import City
from backend.data.models.events import (
    Event,
    EventStatus,
    EventType,
    TicketPricingSnapshot,
)
from backend.data.models.notifications import EmailDigestLog
from backend.data.models.recommendations import Recommendation
from backend.data.models.scraper import ScraperRun, ScraperRunStatus
from backend.data.models.users import (
    DigestFrequency,
    OAuthProvider,
    SavedEvent,
    User,
    UserOAuthProvider,
)
from backend.data.models.venues import Venue

__all__ = [
    "City",
    "DigestFrequency",
    "EmailDigestLog",
    "Event",
    "EventStatus",
    "EventType",
    "OAuthProvider",
    "Recommendation",
    "SavedEvent",
    "ScraperRun",
    "ScraperRunStatus",
    "TicketPricingSnapshot",
    "User",
    "UserOAuthProvider",
    "Venue",
]

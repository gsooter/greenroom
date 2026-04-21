"""SQLAlchemy ORM models.

Import all models here so they register with Base.metadata
for Alembic autogenerate and relationship resolution.
"""

from backend.data.models.artists import Artist
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
    MusicServiceConnection,
    OAuthProvider,
    SavedEvent,
    User,
)
from backend.data.models.venue_comments import (
    VenueComment,
    VenueCommentCategory,
    VenueCommentVote,
)
from backend.data.models.venues import Venue

__all__ = [
    "Artist",
    "City",
    "DigestFrequency",
    "EmailDigestLog",
    "Event",
    "EventStatus",
    "EventType",
    "MusicServiceConnection",
    "OAuthProvider",
    "Recommendation",
    "SavedEvent",
    "ScraperRun",
    "ScraperRunStatus",
    "TicketPricingSnapshot",
    "User",
    "Venue",
    "VenueComment",
    "VenueCommentCategory",
    "VenueCommentVote",
]

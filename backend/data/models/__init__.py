"""SQLAlchemy ORM models.

Import all models here so they register with Base.metadata
for Alembic autogenerate and relationship resolution.
"""

from backend.data.models.artist_similarity import ArtistSimilarity
from backend.data.models.artists import Artist
from backend.data.models.cities import City
from backend.data.models.events import (
    Event,
    EventStatus,
    EventType,
    TicketPricingSnapshot,
)
from backend.data.models.feedback import Feedback, FeedbackKind
from backend.data.models.map_recommendations import (
    MapRecommendation,
    MapRecommendationCategory,
    MapRecommendationVote,
)
from backend.data.models.notifications import EmailDigestLog
from backend.data.models.onboarding import (
    FollowedArtist,
    FollowedVenue,
    UserOnboardingState,
)
from backend.data.models.recommendations import Recommendation
from backend.data.models.scraper import ScraperAlert, ScraperRun, ScraperRunStatus
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
    "ArtistSimilarity",
    "City",
    "DigestFrequency",
    "EmailDigestLog",
    "Event",
    "EventStatus",
    "EventType",
    "Feedback",
    "FeedbackKind",
    "FollowedArtist",
    "FollowedVenue",
    "MapRecommendation",
    "MapRecommendationCategory",
    "MapRecommendationVote",
    "MusicServiceConnection",
    "OAuthProvider",
    "Recommendation",
    "SavedEvent",
    "ScraperAlert",
    "ScraperRun",
    "ScraperRunStatus",
    "TicketPricingSnapshot",
    "User",
    "UserOnboardingState",
    "Venue",
    "VenueComment",
    "VenueCommentCategory",
    "VenueCommentVote",
]

"""BaseScorer abstract class for recommendation scoring strategies."""

from abc import ABC, abstractmethod
from typing import Any
from uuid import UUID


class BaseScorer(ABC):
    """Abstract base class for recommendation scoring strategies.

    Each scorer contributes a partial score for an event-user pair.
    Scores are summed and normalized by the engine.
    """

    @abstractmethod
    def score(
        self,
        user_id: UUID,
        event_id: UUID,
    ) -> dict[str, Any]:
        """Calculate a score for the given user-event pair.

        Args:
            user_id: UUID of the user to score for.
            event_id: UUID of the event to score.

        Returns:
            Dictionary with 'score' (float) and any additional
            breakdown fields for transparency.
        """
        ...

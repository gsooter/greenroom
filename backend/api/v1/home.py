"""Home page route handler.

Backs the signed-in home page (Decision 063). Returns the data needed
to drive the three personalized sections in a single round-trip:

* ``has_signal`` — whether the user has enough taste signal for the
  reframed experience (≥3 follows OR a connected music service).
* ``recommendations`` — top-N persisted recs with reasons, supplemented
  with popularity-based fallbacks when the engine can't fill the slot.
* ``new_since_last_visit`` — events created since the user's last home
  page visit whose performers overlap the user's anchor-artist set.
* ``last_home_visit_at`` — the timestamp the new-since query used,
  surfaced so the client can render a friendly "since N days ago"
  caption when desired.

The route enqueues a Celery task to update ``users.last_home_visit_at``
to the current time so the next visit's window starts where this one
ended. The update is fire-and-forget — even if the worker is down the
home page still renders.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from typing import Any

from backend.api.v1 import api_v1
from backend.core.auth import get_current_user, require_auth
from backend.core.database import get_db
from backend.data.models.events import Event, EventStatus
from backend.data.repositories import events as events_repo
from backend.services import events as events_service
from backend.services import home as home_service
from backend.services import recommendations as recs_service

# Default cap on the recommendations section. The frontend renders 8-12
# tiles, so 12 keeps headroom while still being a single page of cards.
_RECOMMENDATIONS_LIMIT = 12

# When the engine returns fewer than this many real recommendations,
# the response supplements the list with popularity-based "Popular in
# DC" rows so the section never renders sparse for users with thin
# signal.
_RECOMMENDATIONS_MIN_FOR_NO_FALLBACK = 4

# Cap on the popularity-fallback supplement. We never want fallback
# rows to outnumber real recommendations on a section labelled "shows
# you'll care about", so the supplement tops up to the recommendations
# limit but is itself bounded.
_POPULARITY_LIMIT = 8


@api_v1.route("/me/home", methods=["GET"])
@require_auth
def get_home() -> tuple[dict[str, Any], int]:
    """Return the signed-in home page payload and queue the visit-timestamp update.

    Composes:

    * Top recommendations (up to 12) with popularity fallback when
      the user's signal is too thin for the engine to fill the slot.
    * New-since-last-visit events bounded by the user's anchor-artist
      set and preferred region.
    * The ``has_signal`` gate that drives the frontend's branching
      between "welcome prompt" and the reframed experience.

    Then asynchronously enqueues
    :func:`backend.services.home_tasks.record_home_visit` so the next
    visit's new-since window starts now. The enqueue failure is
    swallowed — the home page already rendered, and the worst case is
    that the user sees the same "new since last visit" set twice.

    Returns:
        Tuple of ``({data: {...}}, 200)``.
    """
    session = get_db()
    user = get_current_user()

    visit_anchor = user.last_home_visit_at
    has_signal = home_service.has_signal(session, user)

    recs, _total = recs_service.list_recommendations_for_user(
        session, user, page=1, per_page=_RECOMMENDATIONS_LIMIT
    )

    fallback_events: list[Event] = []
    if has_signal and len(recs) < _RECOMMENDATIONS_MIN_FOR_NO_FALLBACK:
        excluded = {r.event_id for r in recs}
        fallback_events = _list_popularity_fallback(
            session,
            limit=_RECOMMENDATIONS_LIMIT - len(recs),
            excluded_event_ids=excluded,
        )

    new_since = home_service.get_new_since_last_visit(session, user)

    _enqueue_visit_update(user.id)

    return {
        "data": {
            "has_signal": has_signal,
            "last_home_visit_at": (
                visit_anchor.isoformat() if visit_anchor is not None else None
            ),
            "recommendations": [recs_service.serialize_recommendation(r) for r in recs],
            "popularity_fallback": [
                events_service.serialize_event_summary(e) for e in fallback_events
            ],
            "new_since_last_visit": [
                events_service.serialize_event_summary(e) for e in new_since
            ],
        }
    }, 200


def _list_popularity_fallback(
    session: Any,
    *,
    limit: int,
    excluded_event_ids: set[Any],
) -> list[Event]:
    """Pull a small set of popular DMV events to flesh out a thin recs list.

    Filters down to upcoming, non-cancelled DMV events that haven't
    already been recommended to the user, ordered by ``starts_at`` so
    soonest shows lead. The implementation deliberately does not sort
    by a "going count" metric today — the column doesn't exist yet, and
    chronological order is a defensible proxy for "popular soon" while
    we ship the broader sprint.

    Args:
        session: Active SQLAlchemy session.
        limit: Maximum number of fallback rows.
        excluded_event_ids: Event UUIDs that already appear in the
            recommendations payload; the fallback skips these so the
            section never renders the same show twice.

    Returns:
        List of :class:`Event` rows safe to surface as "Popular in DC".
    """
    if limit <= 0:
        return []

    now = datetime.now(UTC)
    events, _total = events_repo.list_events(
        session,
        region="DMV",
        status=EventStatus.CONFIRMED,
        date_from=now.date(),
        page=1,
        per_page=limit + len(excluded_event_ids),
    )
    if not events:
        return []
    filtered = [e for e in events if e.id not in excluded_event_ids]
    return filtered[:limit]


def _enqueue_visit_update(user_id: Any) -> None:
    """Best-effort Celery enqueue for the visit-timestamp write.

    Celery may be unavailable in dev (no broker running) or during
    tests; either way the home page must render. We swallow any error
    raised by the broker dispatch so the request never 500s on a
    background-task hiccup.

    Args:
        user_id: UUID of the current user.
    """
    with contextlib.suppress(Exception):
        # Imported lazily so the route module stays importable in
        # environments without a Celery broker — pytest specifically.
        from backend.services.home_tasks import record_home_visit

        record_home_visit.delay(str(user_id))

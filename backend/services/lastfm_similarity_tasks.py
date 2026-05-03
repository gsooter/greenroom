"""Celery tasks that enrich artists with Last.fm similar-artists data.

The nightly :func:`backfill_lastfm_similarity_enrichment` task drains
the ``artists`` backlog 200 rows at a time, delegating each artist to
:func:`enrich_artist_similarity_from_lastfm`. The per-artist task
prefers an MBID-based ``artist.getSimilar`` lookup when
``musicbrainz_id`` is populated and falls back to the name-based
endpoint otherwise.

Storage is delegated to
:func:`backend.services.artist_similarity.store_similar_artists`,
which upserts rows in the ``artist_similarity`` table and resolves
``similar_artist_id`` against the ``artists`` table when a match
exists.

A separate :func:`resolve_unlinked_similarity_rows` task runs nightly
after the backfill to link previously-unresolved rows to artists that
the scraper has since added.

**Rate limiting.** Last.fm allows 5 req/sec/key; we pace at 4. The
Celery fleet runs multiple workers, so the same Redis-backed lock that
gates the tag enrichment task gates this one too — the limit is
per-key, so the two task families share a single coordination point.

**Idempotency.** Per-artist tasks are safe to call repeatedly: artists
enriched within :data:`ENRICHED_FRESHNESS` are skipped, and no-match
outcomes still stamp ``lastfm_similar_enriched_at`` so we don't
re-search them on the next run.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import redis
from celery import shared_task

from backend.celery_app import celery_app
from backend.core.config import get_settings
from backend.core.database import get_session_factory
from backend.core.logging import get_logger
from backend.data.repositories import artists as artists_repo
from backend.services.artist_similarity import (
    resolve_similarity_links,
    store_similar_artists,
)
from backend.services.lastfm import (
    LastFMAPIError,
    LastFMSimilarArtist,
    fetch_similar_artists_by_mbid,
    fetch_similar_artists_by_name,
)

if TYPE_CHECKING:
    from backend.data.models.artists import Artist

logger = get_logger(__name__)

BACKFILL_BATCH_SIZE = 200
ENRICHED_FRESHNESS = timedelta(days=30)
SIMILAR_LIMIT = 20
RATE_LIMIT_LOCK_KEY = "lastfm_rate_limit"
# Lock timeout is short — only long enough to cover the sleep + the
# HTTP round-trip. The lock auto-expires so a crashed worker can't
# wedge the whole pipeline.
RATE_LIMIT_LOCK_TIMEOUT = 60.0
# 250 ms between calls = 4 req/sec, comfortably under Last.fm's
# 5 req/sec ceiling.
RATE_LIMIT_INTERVAL_SECONDS = 0.25

_redis_client: redis.Redis | None = None


def _get_redis() -> redis.Redis:
    """Return a lazily-initialized module-level Redis client.

    The Last.fm rate limit must hold across the entire Celery fleet.
    If Redis is unreachable we let the exception propagate so Celery
    can retry the task — pacing without coordination could earn us
    a temporary key suspension.

    Returns:
        A connected :class:`redis.Redis` client.
    """
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    url = get_settings().redis_url
    _redis_client = redis.Redis.from_url(url, socket_timeout=2.0)
    return _redis_client


def _reset_redis_for_tests() -> None:
    """Drop the cached Redis client so tests can inject their own.

    Only call from tests; has no runtime purpose.
    """
    global _redis_client
    _redis_client = None


def _is_recently_enriched(artist: Artist) -> bool:
    """Return True when ``artist`` was enriched within the freshness window.

    Args:
        artist: The :class:`Artist` row being considered.

    Returns:
        ``True`` if ``lastfm_similar_enriched_at`` is within the last
        :data:`ENRICHED_FRESHNESS` interval, else ``False``.
    """
    enriched_at = artist.lastfm_similar_enriched_at
    if enriched_at is None:
        return False
    if enriched_at.tzinfo is None:
        enriched_at = enriched_at.replace(tzinfo=UTC)
    return datetime.now(UTC) - enriched_at < ENRICHED_FRESHNESS


def _paced_call(func: Any, *args: Any, **kwargs: Any) -> Any:
    """Execute ``func`` while holding the global Last.fm rate-lock.

    Acquires the cluster-wide lock, sleeps for
    :data:`RATE_LIMIT_INTERVAL_SECONDS`, then runs the call. The sleep
    happens *inside* the lock so the next holder gets a fresh window.

    Args:
        func: The HTTP-bound callable.
        *args: Positional args forwarded to ``func``.
        **kwargs: Keyword args forwarded to ``func``.

    Returns:
        Whatever ``func`` returns.

    Raises:
        Whatever ``func`` raises. The lock is released regardless.
    """
    client = _get_redis()
    with client.lock(
        RATE_LIMIT_LOCK_KEY,
        timeout=RATE_LIMIT_LOCK_TIMEOUT,
        blocking_timeout=RATE_LIMIT_LOCK_TIMEOUT,
    ):
        time.sleep(RATE_LIMIT_INTERVAL_SECONDS)
        return func(*args, **kwargs)


def _fetch_similar_for_artist(
    artist: Artist,
) -> tuple[list[LastFMSimilarArtist], str]:
    """Return Last.fm similar-artist results plus the source label.

    Strategy:

    1. If the artist has ``musicbrainz_id``, try the MBID lookup. A
       non-empty result is taken as authoritative.
    2. Otherwise — or when the MBID lookup returned an empty list —
       fall back to the name-based endpoint with autocorrect.

    Args:
        artist: The :class:`Artist` row being enriched.

    Returns:
        Tuple of ``(similar_artists, source)`` where ``source`` is
        ``"mbid"``, ``"name"``, or ``"none"`` when both paths returned
        nothing.

    Raises:
        LastFMAPIError: Bubbles up so Celery can retry.
    """
    if artist.musicbrainz_id:
        mbid_results = _paced_call(
            fetch_similar_artists_by_mbid, artist.musicbrainz_id, SIMILAR_LIMIT
        )
        if mbid_results:
            return mbid_results, "mbid"

    name_results = _paced_call(fetch_similar_artists_by_name, artist.name)
    if name_results:
        return name_results, "name"
    return [], "none"


@shared_task(
    name=(
        "backend.services.lastfm_similarity_tasks.enrich_artist_similarity_from_lastfm"
    ),
    bind=True,
    autoretry_for=(LastFMAPIError,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=3,
)  # type: ignore[untyped-decorator]
def enrich_artist_similarity_from_lastfm(self: Any, artist_id: str) -> dict[str, Any]:
    """Fetch and store Last.fm similar artists for a single artist.

    MBID-first strategy when available; otherwise falls back to a
    name-based lookup with autocorrect. Stores up to
    :data:`SIMILAR_LIMIT` similar artists with their scores via
    :func:`store_similar_artists`, which also resolves
    ``similar_artist_id`` against any matching :class:`Artist` row.

    No-match outcomes still stamp ``lastfm_similar_enriched_at`` so
    the same row is not re-searched on the next backfill pass. Skips
    artists enriched within the last :data:`ENRICHED_FRESHNESS`
    interval. Retries up to 3 times with exponential backoff on
    :class:`LastFMAPIError`.

    Args:
        self: Celery task instance (auto-injected by ``bind=True``).
        artist_id: UUID string of the artist row to enrich.

    Returns:
        Outcome dict with ``status`` (``matched`` / ``unmatched`` /
        ``skipped`` / ``missing``), ``artist_id``, and — when matched —
        ``source`` (``"mbid"`` or ``"name"``) and ``similar_count``.

    Raises:
        LastFMAPIError: After exhausting retries on a transient API
            failure.
    """
    uid = uuid.UUID(artist_id)
    session_factory = get_session_factory()
    with session_factory() as session:
        try:
            artist = artists_repo.get_artist_by_id(session, uid)
            if artist is None:
                return {"artist_id": artist_id, "status": "missing"}

            if _is_recently_enriched(artist):
                logger.info(
                    "lastfm_similar_enrichment_skipped",
                    extra={
                        "artist_id": artist_id,
                        "artist_name": artist.name,
                    },
                )
                return {"artist_id": artist_id, "status": "skipped"}

            similar, source = _fetch_similar_for_artist(artist)
            if not similar:
                logger.info(
                    "lastfm_similar_enrichment_no_match",
                    extra={
                        "artist_id": artist_id,
                        "artist_name": artist.name,
                    },
                )
                artists_repo.mark_artist_lastfm_similar_enriched(session, artist)
                session.commit()
                return {"artist_id": artist_id, "status": "unmatched"}

            store_similar_artists(session, artist.id, similar)
            artists_repo.mark_artist_lastfm_similar_enriched(session, artist)
            session.commit()
            logger.info(
                "lastfm_similar_enrichment_matched",
                extra={
                    "artist_id": artist_id,
                    "artist_name": artist.name,
                    "source": source,
                    "similar_count": len(similar),
                },
            )
            return {
                "artist_id": artist_id,
                "status": "matched",
                "source": source,
                "similar_count": len(similar),
            }
        except LastFMAPIError:
            session.rollback()
            raise
        except Exception:
            session.rollback()
            raise


@shared_task(
    name=(
        "backend.services.lastfm_similarity_tasks.backfill_lastfm_similarity_enrichment"
    ),
)  # type: ignore[untyped-decorator]
def backfill_lastfm_similarity_enrichment() -> dict[str, Any]:
    """Queue similarity enrichment tasks for unenriched artists.

    Selects up to :data:`BACKFILL_BATCH_SIZE` artists whose
    ``lastfm_similar_enriched_at`` is NULL and queues a
    :func:`enrich_artist_similarity_from_lastfm` task for each. The
    200-row cap matches the spec — paced at 250 ms per request, the
    backfill drains in roughly 50 seconds end-to-end and grows
    proportionally as new artists get scraped.

    Returns:
        Summary dict with ``queued`` (count of tasks dispatched).
    """
    session_factory = get_session_factory()
    with session_factory() as session:
        pending = artists_repo.list_artists_for_lastfm_similar_enrichment(
            session, limit=BACKFILL_BATCH_SIZE
        )
        queued = 0
        for artist in pending:
            celery_app.send_task(
                (
                    "backend.services.lastfm_similarity_tasks"
                    ".enrich_artist_similarity_from_lastfm"
                ),
                args=[str(artist.id)],
            )
            queued += 1
        logger.info(
            "lastfm_similar_backfill_queued",
            extra={"queued": queued},
        )
        return {"queued": queued}


@shared_task(
    name=("backend.services.lastfm_similarity_tasks.resolve_unlinked_similarity_rows"),
)  # type: ignore[untyped-decorator]
def resolve_unlinked_similarity_rows() -> dict[str, Any]:
    """Resolve similarity links for newly-added artists.

    When the scraper adds a new artist, that artist may already appear
    as a ``similar_artist_name`` in existing similarity rows. This
    task delegates to
    :func:`backend.services.artist_similarity.resolve_similarity_links`
    to find those and link them. Runs nightly after the scraper and
    after the similarity backfill.

    Returns:
        Summary dict with ``linked`` (count of rows newly linked).
    """
    session_factory = get_session_factory()
    with session_factory() as session:
        try:
            linked = resolve_similarity_links(session)
            session.commit()
            logger.info(
                "lastfm_similar_resolve_complete",
                extra={"linked": linked},
            )
            return {"linked": linked}
        except Exception:
            session.rollback()
            raise

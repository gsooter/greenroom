"""Celery tasks that enrich scraped artists with MusicBrainz tags.

The nightly :func:`backfill_musicbrainz_enrichment` task drains the
``artists`` backlog 100 rows at a time, delegating each artist to
:func:`enrich_artist_from_musicbrainz`. The per-artist task hits
MusicBrainz, picks the best match through
:func:`backend.services.musicbrainz.find_best_match`, and writes the
raw genres/tags blobs onto the artist row.

**Rate limiting.** MusicBrainz enforces 1 request per second per IP
and we err on the side of 1.1 seconds between requests. The Celery
fleet runs multiple workers, so a Redis-backed lock plus a sleep
inside the lock guarantees pacing across the cluster — anything more
elaborate (token bucket etc.) is overkill for one provider with one
endpoint pair.

**Idempotency.** The per-artist task is safe to call repeatedly:
artists enriched within :data:`ENRICHED_FRESHNESS` are skipped, and
no-match outcomes still stamp ``musicbrainz_enriched_at`` so we don't
re-search them on the next run. A separate "stale_after" hook in the
repository lets a future operator schedule re-enrichment without
changing the task signature.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import redis
from celery import shared_task

from backend.celery_app import celery_app
from backend.core.config import get_settings
from backend.core.database import get_session_factory
from backend.core.logging import get_logger
from backend.data.repositories import artists as artists_repo
from backend.services.musicbrainz import (
    MusicBrainzAPIError,
    MusicBrainzNotFoundError,
    fetch_artist_details,
    find_best_match,
    search_musicbrainz_artist,
)

if TYPE_CHECKING:
    from backend.data.models.artists import Artist

logger = get_logger(__name__)

BACKFILL_BATCH_SIZE = 100
ENRICHED_FRESHNESS = timedelta(days=30)
RATE_LIMIT_LOCK_KEY = "musicbrainz_rate_limit"
# Lock timeout is short — only long enough to cover the sleep + the
# two HTTP round-trips. The lock auto-expires so a crashed worker
# can't wedge the whole pipeline.
RATE_LIMIT_LOCK_TIMEOUT = 60.0
# 1.1s between calls keeps us a hair under MusicBrainz's documented
# 1 req/sec ceiling.
RATE_LIMIT_INTERVAL_SECONDS = 1.1

_redis_client: redis.Redis | None = None


def _get_redis() -> redis.Redis:
    """Return a lazily-initialized module-level Redis client.

    The MusicBrainz rate limit must hold across the entire Celery
    fleet, so unlike the API limiter (which fails open) this one is
    required: if Redis is unreachable we let the exception propagate
    and let Celery retry the task. Pacing without coordination would
    burn through MusicBrainz's quota and earn us a temporary IP block.

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
        ``True`` if ``musicbrainz_enriched_at`` is within the last
        :data:`ENRICHED_FRESHNESS` interval, else ``False``.
    """
    enriched_at = artist.musicbrainz_enriched_at
    if enriched_at is None:
        return False
    if enriched_at.tzinfo is None:
        enriched_at = enriched_at.replace(tzinfo=UTC)
    return datetime.now(UTC) - enriched_at < ENRICHED_FRESHNESS


def _paced_call(func: Any, *args: Any, **kwargs: Any) -> Any:
    """Execute ``func`` while holding the global MusicBrainz rate-lock.

    Acquires the cluster-wide lock, sleeps for
    :data:`RATE_LIMIT_INTERVAL_SECONDS`, then runs the call. The sleep
    happens *inside* the lock so the next holder gets a fresh window.

    Args:
        func: The HTTP-bound callable (e.g.
            :func:`search_musicbrainz_artist`).
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


@shared_task(
    name="backend.services.musicbrainz_tasks.enrich_artist_from_musicbrainz",
    bind=True,
    autoretry_for=(MusicBrainzAPIError,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=3,
)  # type: ignore[untyped-decorator]
def enrich_artist_from_musicbrainz(self: Any, artist_id: str) -> dict[str, Any]:
    """Enrich a single artist row with MusicBrainz genre and tag data.

    Searches MusicBrainz for ``artist.name``, picks the best candidate
    above the confidence threshold, fetches the full artist record,
    and stores the raw ``genres`` and ``tags`` payloads. No-match
    outcomes still stamp ``musicbrainz_enriched_at`` so the same row
    is not re-searched on the next backfill pass.

    Skips artists enriched within the last
    :data:`ENRICHED_FRESHNESS` interval to keep repeated backfills
    cheap. Retries up to 3 times with exponential backoff on
    :class:`MusicBrainzAPIError` (typically 503/connection issues).
    A 404 on the MBID lookup is non-retryable — we mark the artist
    enriched with empty data so we don't keep chasing a missing record.

    Args:
        self: Celery task instance (auto-injected by ``bind=True``).
        artist_id: UUID string of the artist row to enrich.

    Returns:
        Outcome dict with ``status`` (``matched`` / ``unmatched`` /
        ``skipped`` / ``missing`` / ``not_found``), ``artist_id``, and
        — when matched — ``musicbrainz_id``, ``confidence``, and
        ``genre_count``.

    Raises:
        MusicBrainzAPIError: After exhausting retries on a transient
            API failure.
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
                    "musicbrainz_enrichment_skipped",
                    extra={
                        "artist_id": artist_id,
                        "artist_name": artist.name,
                    },
                )
                return {"artist_id": artist_id, "status": "skipped"}

            candidates = _paced_call(search_musicbrainz_artist, artist.name)
            match = find_best_match(artist.name, candidates)
            if match is None:
                logger.info(
                    "musicbrainz_enrichment_no_match",
                    extra={
                        "artist_id": artist_id,
                        "artist_name": artist.name,
                        "candidates": len(candidates),
                    },
                )
                artists_repo.mark_artist_musicbrainz_enriched(
                    session,
                    artist,
                    musicbrainz_id=None,
                    genres=None,
                    tags=None,
                    confidence=None,
                )
                session.commit()
                return {"artist_id": artist_id, "status": "unmatched"}

            candidate, confidence = match
            try:
                details = _paced_call(fetch_artist_details, candidate.mbid)
            except MusicBrainzNotFoundError:
                logger.warning(
                    "musicbrainz_mbid_not_found",
                    extra={
                        "artist_id": artist_id,
                        "artist_name": artist.name,
                        "mbid": candidate.mbid,
                    },
                )
                artists_repo.mark_artist_musicbrainz_enriched(
                    session,
                    artist,
                    musicbrainz_id=None,
                    genres=None,
                    tags=None,
                    confidence=None,
                )
                session.commit()
                return {"artist_id": artist_id, "status": "not_found"}

            confidence_decimal = Decimal(f"{confidence:.2f}")
            artists_repo.mark_artist_musicbrainz_enriched(
                session,
                artist,
                musicbrainz_id=details.mbid,
                genres=details.genres,
                tags=details.tags,
                confidence=confidence_decimal,
            )
            session.commit()
            logger.info(
                "musicbrainz_enrichment_matched",
                extra={
                    "artist_id": artist_id,
                    "artist_name": artist.name,
                    "mbid": details.mbid,
                    "confidence": float(confidence_decimal),
                    "genre_count": len(details.genres),
                    "tag_count": len(details.tags),
                },
            )
            return {
                "artist_id": artist_id,
                "status": "matched",
                "musicbrainz_id": details.mbid,
                "confidence": float(confidence_decimal),
                "genre_count": len(details.genres),
            }
        except MusicBrainzAPIError:
            session.rollback()
            raise
        except Exception:
            session.rollback()
            raise


@shared_task(
    name="backend.services.musicbrainz_tasks.backfill_musicbrainz_enrichment",
)  # type: ignore[untyped-decorator]
def backfill_musicbrainz_enrichment() -> dict[str, Any]:
    """Queue MusicBrainz enrichment tasks for unenriched artists.

    Selects up to :data:`BACKFILL_BATCH_SIZE` artists whose
    ``musicbrainz_enriched_at`` is NULL and queues an
    :func:`enrich_artist_from_musicbrainz` task for each. The 100-row
    cap is matched to MusicBrainz's 1 req/sec ceiling: 100 artists
    times ~2 requests each at 1.1s spacing is ~220 seconds total,
    comfortably inside one overnight beat fire.

    Returns:
        Summary dict with ``queued`` (count of tasks dispatched).
    """
    session_factory = get_session_factory()
    with session_factory() as session:
        pending = artists_repo.list_artists_for_musicbrainz_enrichment(
            session, limit=BACKFILL_BATCH_SIZE
        )
        queued = 0
        for artist in pending:
            celery_app.send_task(
                "backend.services.musicbrainz_tasks.enrich_artist_from_musicbrainz",
                args=[str(artist.id)],
            )
            queued += 1
        logger.info(
            "musicbrainz_backfill_queued",
            extra={"queued": queued},
        )
        return {"queued": queued}

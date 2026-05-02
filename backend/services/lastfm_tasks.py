"""Celery tasks that enrich scraped artists with Last.fm tag data.

The nightly :func:`backfill_lastfm_enrichment` task drains the
``artists`` backlog 200 rows at a time, delegating each artist to
:func:`enrich_artist_from_lastfm`. The per-artist task hits Last.fm,
prefers an MBID-based lookup when ``musicbrainz_id`` is populated by
the earlier MusicBrainz enrichment pass, and falls back to fuzzy
name search otherwise.

**MBID-first strategy.** Sprint 1A populates ``musicbrainz_id`` on
many artists. When that's available, ``artist.getInfo&mbid=...`` is
exact — confidence is forced to 1.00 and no fuzzy matching runs.
Without an MBID we run ``artist.search`` and let
:func:`backend.services.lastfm.find_best_match` blend name similarity
and listener-count percentile.

**Rate limiting.** Last.fm allows 5 req/sec/key but we pace at 4 to
stay clear of the ceiling. The Celery fleet runs multiple workers,
so a Redis-backed lock plus a 250 ms sleep inside the lock guarantees
pacing across the cluster.

**Idempotency.** The per-artist task is safe to call repeatedly:
artists enriched within :data:`ENRICHED_FRESHNESS` are skipped, and
no-match outcomes still stamp ``lastfm_enriched_at`` so we don't
re-search them on the next run.
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
from backend.services.lastfm import (
    LastFMAPIError,
    LastFMArtistInfo,
    fetch_artist_info_by_mbid,
    fetch_artist_info_by_name,
    find_best_match,
    search_lastfm_artist,
)

if TYPE_CHECKING:
    from backend.data.models.artists import Artist

logger = get_logger(__name__)

BACKFILL_BATCH_SIZE = 200
ENRICHED_FRESHNESS = timedelta(days=30)
RATE_LIMIT_LOCK_KEY = "lastfm_rate_limit"
# Lock timeout is short — only long enough to cover the sleep + the
# HTTP round-trip. The lock auto-expires so a crashed worker can't
# wedge the whole pipeline.
RATE_LIMIT_LOCK_TIMEOUT = 60.0
# 250 ms between calls = 4 req/sec, comfortably under Last.fm's
# 5 req/sec ceiling.
RATE_LIMIT_INTERVAL_SECONDS = 0.25
# Confidence we record on MBID-based lookups. The MBID is exact —
# either Last.fm has the record or it doesn't — so there's no fuzzy
# matching to score and 1.00 reflects that.
MBID_CONFIDENCE = Decimal("1.00")

_redis_client: redis.Redis | None = None


def _get_redis() -> redis.Redis:
    """Return a lazily-initialized module-level Redis client.

    The Last.fm rate limit must hold across the entire Celery fleet.
    If Redis is unreachable we let the exception propagate and let
    Celery retry the task — pacing without coordination could earn us
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
        ``True`` if ``lastfm_enriched_at`` is within the last
        :data:`ENRICHED_FRESHNESS` interval, else ``False``.
    """
    enriched_at = artist.lastfm_enriched_at
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


def _resolve_artist(
    artist: Artist,
) -> tuple[LastFMArtistInfo | None, Decimal | None, str]:
    """Return Last.fm info plus its match confidence and source label.

    Strategy:

    1. If the artist already has ``musicbrainz_id``, try the MBID
       lookup. A hit is treated as an exact match (confidence 1.00).
    2. Otherwise — or when the MBID lookup misses — run a name search,
       pick the best candidate via :func:`find_best_match`, then call
       ``artist.getInfo`` by name to fetch the full payload.

    Args:
        artist: The :class:`Artist` row being enriched.

    Returns:
        A tuple of ``(info, confidence, source)``. ``info`` is None
        when no match was found at all. ``source`` is ``"mbid"``,
        ``"name"``, or ``"none"``.

    Raises:
        LastFMAPIError: Bubbles up so the Celery task layer can retry.
    """
    if artist.musicbrainz_id:
        info = _paced_call(fetch_artist_info_by_mbid, artist.musicbrainz_id)
        if info is not None:
            return info, MBID_CONFIDENCE, "mbid"

    candidates = _paced_call(search_lastfm_artist, artist.name)
    match = find_best_match(artist.name, candidates)
    if match is None:
        return None, None, "none"

    candidate, confidence = match
    info = _paced_call(fetch_artist_info_by_name, candidate.name)
    if info is None:
        return None, None, "none"
    return info, Decimal(f"{confidence:.2f}"), "name"


@shared_task(
    name="backend.services.lastfm_tasks.enrich_artist_from_lastfm",
    bind=True,
    autoretry_for=(LastFMAPIError,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=3,
)  # type: ignore[untyped-decorator]
def enrich_artist_from_lastfm(self: Any, artist_id: str) -> dict[str, Any]:
    """Enrich a single artist row with Last.fm tag data.

    MBID-first strategy: when ``musicbrainz_id`` is populated, we use
    the exact ``artist.getInfo&mbid=...`` lookup and stamp confidence
    1.00. Otherwise we fall back to ``artist.search`` and fuzzy match.

    No-match outcomes still stamp ``lastfm_enriched_at`` so the same
    row is not re-searched on the next backfill pass. Skips artists
    enriched within the last :data:`ENRICHED_FRESHNESS` interval to
    keep repeated backfills cheap. Retries up to 3 times with
    exponential backoff on :class:`LastFMAPIError`.

    Args:
        self: Celery task instance (auto-injected by ``bind=True``).
        artist_id: UUID string of the artist row to enrich.

    Returns:
        Outcome dict with ``status`` (``matched`` / ``unmatched`` /
        ``skipped`` / ``missing``), ``artist_id``, and — when matched
        — ``source`` (``"mbid"`` or ``"name"``), ``confidence``, and
        ``tag_count``.

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
                    "lastfm_enrichment_skipped",
                    extra={
                        "artist_id": artist_id,
                        "artist_name": artist.name,
                    },
                )
                return {"artist_id": artist_id, "status": "skipped"}

            info, confidence, source = _resolve_artist(artist)
            if info is None or confidence is None:
                logger.info(
                    "lastfm_enrichment_no_match",
                    extra={
                        "artist_id": artist_id,
                        "artist_name": artist.name,
                    },
                )
                artists_repo.mark_artist_lastfm_enriched(
                    session,
                    artist,
                    tags=None,
                    listener_count=None,
                    url=None,
                    bio_summary=None,
                    confidence=None,
                )
                session.commit()
                return {"artist_id": artist_id, "status": "unmatched"}

            artists_repo.mark_artist_lastfm_enriched(
                session,
                artist,
                tags=info.tags,
                listener_count=info.listener_count,
                url=info.url or None,
                bio_summary=info.bio_summary,
                confidence=confidence,
            )
            session.commit()
            logger.info(
                "lastfm_enrichment_matched",
                extra={
                    "artist_id": artist_id,
                    "artist_name": artist.name,
                    "source": source,
                    "confidence": float(confidence),
                    "tag_count": len(info.tags),
                    "listener_count": info.listener_count,
                },
            )
            return {
                "artist_id": artist_id,
                "status": "matched",
                "source": source,
                "confidence": float(confidence),
                "tag_count": len(info.tags),
            }
        except LastFMAPIError:
            session.rollback()
            raise
        except Exception:
            session.rollback()
            raise


@shared_task(
    name="backend.services.lastfm_tasks.backfill_lastfm_enrichment",
)  # type: ignore[untyped-decorator]
def backfill_lastfm_enrichment() -> dict[str, Any]:
    """Queue Last.fm enrichment tasks for unenriched artists.

    Selects up to :data:`BACKFILL_BATCH_SIZE` artists whose
    ``lastfm_enriched_at`` is NULL and queues an
    :func:`enrich_artist_from_lastfm` task for each. The 200-row cap
    fits inside one beat fire: 200 artists at ~250 ms pacing each
    runs ~50 seconds end to end.

    Returns:
        Summary dict with ``queued`` (count of tasks dispatched).
    """
    session_factory = get_session_factory()
    with session_factory() as session:
        pending = artists_repo.list_artists_for_lastfm_enrichment(
            session, limit=BACKFILL_BATCH_SIZE
        )
        queued = 0
        for artist in pending:
            celery_app.send_task(
                "backend.services.lastfm_tasks.enrich_artist_from_lastfm",
                args=[str(artist.id)],
            )
            queued += 1
        logger.info(
            "lastfm_backfill_queued",
            extra={"queued": queued},
        )
        return {"queued": queued}

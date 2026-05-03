"""Celery tasks that consolidate per-artist granular tags (Decision 060).

Two tasks:

* :func:`consolidate_artist_tags_task` runs the consolidation pipeline
  for one artist. Idempotent: skips artists whose source data has not
  moved on since the last consolidation. Suitable as a hook from the
  Last.fm enrichment task or as a manual fixup.

* :func:`backfill_tag_consolidation` is the nightly backfill. Runs in
  two passes the first time and on any ``force=True`` invocation:

  1. **Pass 1.** Consolidate every selected artist *without* document-
     frequency filtering. Populates :attr:`Artist.granular_tags` from
     raw sources. Necessary because the DF blocklist depends on the
     populated data — there is no blocklist yet.
  2. **Pass 2.** Rebuild the blocklist from the now-populated data,
     then re-consolidate every artist applying the blocklist.

  Subsequent default-mode runs reuse the cached 24-hour blocklist and
  only re-consolidate rows whose source data has moved on. The two-
  pass execution is automatic — callers do not need to drive it
  manually.

**No rate limiting.** Tag consolidation is pure Python plus a few
Postgres queries. No external API calls, so the full backfill drains
inline rather than fanning out per-artist Celery tasks.

**Schedule.** The nightly fire is at 05:15 ET in
:func:`backend.celery_app._beat_schedule`. The 15-minute gap after
Last.fm similarity (05:00) and genre normalization (05:00) ensures
fresh source data is folded in.
"""

from __future__ import annotations

import uuid
from typing import Any

import redis
from celery import shared_task

from backend.celery_app import celery_app  # noqa: F401  # ensures app import
from backend.core.config import get_settings
from backend.core.database import get_session_factory
from backend.core.logging import get_logger
from backend.data.repositories import artists as artists_repo
from backend.services.tag_consolidation import (
    build_global_tag_blocklist,
    consolidate_artist_tags,
)

logger = get_logger(__name__)

# Generous ceiling for a single backfill fire. Consolidation is
# milliseconds per artist; the live catalog stays well below this and
# the cap protects against runaway loops if a future scrape balloons
# the row count unexpectedly.
BACKFILL_BATCH_SIZE = 5000


_redis_client: redis.Redis | None = None


def _get_redis() -> redis.Redis:
    """Return a lazily-initialized module-level Redis client.

    Mirrors the Last.fm task module's pattern so tests can swap in a
    fake by monkeypatching this function. The blocklist cache is the
    only Redis usage from this module, so the client is created on
    first need rather than at import time.

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


@shared_task(
    name="backend.services.tag_consolidation_tasks.consolidate_artist_tags_task",
)  # type: ignore[untyped-decorator]
def consolidate_artist_tags_task(artist_id: str) -> dict[str, Any]:
    """Consolidate granular tags for a single artist.

    Skips artists whose ``granular_tags_consolidated_at`` is more
    recent than both ``musicbrainz_enriched_at`` and
    ``lastfm_enriched_at`` — re-consolidating without fresh source
    data would write an identical list. The skip path returns
    ``status="skipped"`` so callers can distinguish a no-op from an
    actual consolidation.

    Reads the cached document-frequency blocklist when one exists so
    the resulting tag list reflects the same DF rules the nightly
    backfill applies. A missing blocklist (first run, before
    ``backfill_tag_consolidation`` has executed) means no DF filtering
    — the per-tag filters in
    :func:`backend.services.tag_consolidation.is_useful_for_similarity`
    still apply.

    Args:
        artist_id: UUID string of the artist row to consolidate.

    Returns:
        Outcome dict with ``status`` (``consolidated`` / ``skipped`` /
        ``empty`` / ``missing``), ``artist_id``, and — when
        consolidated — ``tag_count``.
    """
    uid = uuid.UUID(artist_id)
    session_factory = get_session_factory()
    with session_factory() as session:
        try:
            artist = artists_repo.get_artist_by_id(session, uid)
            if artist is None:
                return {"artist_id": artist_id, "status": "missing"}

            consolidated_at = artist.granular_tags_consolidated_at
            if consolidated_at is not None:
                source_timestamps = [
                    ts
                    for ts in (
                        artist.musicbrainz_enriched_at,
                        artist.lastfm_enriched_at,
                    )
                    if ts is not None
                ]
                if source_timestamps and all(
                    ts <= consolidated_at for ts in source_timestamps
                ):
                    return {"artist_id": artist_id, "status": "skipped"}

            blocklist = build_global_tag_blocklist(session, redis_client=_get_redis())
            tags = consolidate_artist_tags(session, artist.id, blocklist=blocklist)
            session.commit()
            status = "consolidated" if tags else "empty"
            logger.info(
                "tag_consolidation_done",
                extra={
                    "artist_id": artist_id,
                    "artist_name": artist.name,
                    "tag_count": len(tags),
                    "status": status,
                },
            )
            return {
                "artist_id": artist_id,
                "status": status,
                "tag_count": len(tags),
            }
        except Exception:
            session.rollback()
            raise


@shared_task(
    name="backend.services.tag_consolidation_tasks.backfill_tag_consolidation",
)  # type: ignore[untyped-decorator]
def backfill_tag_consolidation(force: bool = False) -> dict[str, Any]:
    """Consolidate granular tags for every artist due for a refresh.

    Two-pass execution:

    1. **Pass 1.** Run :func:`consolidate_artist_tags` against every
       selected artist with ``blocklist=None``. This populates
       ``granular_tags`` from raw sources without any document-
       frequency filtering — necessary on the bootstrapping run
       because there is no blocklist to apply yet.
    2. **Pass 2.** Rebuild the blocklist from the now-populated data
       (also caches it in Redis for 24h) and re-consolidate every
       artist applying the blocklist.

    Selection rules (passed to
    :func:`backend.data.repositories.artists.list_artists_for_tag_consolidation`):

    * ``force=False`` (default): artists whose
      ``granular_tags_consolidated_at`` is NULL or older than their
      MusicBrainz / Last.fm enrichment timestamps. Default mode does
      *not* re-execute pass 2 against artists that were skipped — a
      stale row's blocklist won't have changed.
    * ``force=True``: every artist, regardless of consolidation
      freshness. Used after the consolidation pipeline changes (new
      noise patterns, new geographic prefixes, etc.) to flush every
      cached output.

    Args:
        force: When True, re-consolidate every artist regardless of
            timestamp.

    Returns:
        Summary dict with ``pass1_processed``, ``pass2_processed``,
        ``with_tags`` (rows that ended with at least one tag after
        pass 2), and ``blocklist_size`` (the rebuilt blocklist's tag
        count).
    """
    session_factory = get_session_factory()
    with session_factory() as session:
        pending = artists_repo.list_artists_for_tag_consolidation(
            session, limit=BACKFILL_BATCH_SIZE, force=force
        )
        # Pass 1 — populate granular_tags from raw sources without DF
        # filtering. Snapshot the artist IDs before the loop so
        # subsequent ORM operations don't churn the iterator.
        pass1_ids = [artist.id for artist in pending]
        for artist in pending:
            consolidate_artist_tags(session, artist.id, blocklist=None)
        session.commit()

        # Rebuild the blocklist from freshly-populated data and cache
        # it. We pass the redis client so subsequent
        # ``consolidate_artist_tags_task`` calls see the same blocklist
        # without re-counting.
        try:
            redis_client: redis.Redis | None = _get_redis()
        except Exception:
            redis_client = None
        # Always recompute on the backfill — pass ``None`` to bypass
        # any stale cache and write the fresh result.
        blocklist = build_global_tag_blocklist(session, redis_client=None)
        if redis_client is not None and blocklist:
            from backend.services.tag_consolidation import (
                _BLOCKLIST_REDIS_KEY,
                _BLOCKLIST_TTL_SECONDS,
            )

            redis_client.setex(
                _BLOCKLIST_REDIS_KEY,
                _BLOCKLIST_TTL_SECONDS,
                "\n".join(sorted(blocklist)).encode("utf-8"),
            )

        # Pass 2 — re-consolidate every artist touched in pass 1 with
        # the blocklist applied.
        with_tags = 0
        for artist_id in pass1_ids:
            tags = consolidate_artist_tags(session, artist_id, blocklist=blocklist)
            if tags:
                with_tags += 1
        session.commit()

        logger.info(
            "tag_consolidation_backfill_done",
            extra={
                "pass1_processed": len(pass1_ids),
                "pass2_processed": len(pass1_ids),
                "with_tags": with_tags,
                "blocklist_size": len(blocklist),
                "force": force,
            },
        )
        return {
            "pass1_processed": len(pass1_ids),
            "pass2_processed": len(pass1_ids),
            "with_tags": with_tags,
            "blocklist_size": len(blocklist),
        }

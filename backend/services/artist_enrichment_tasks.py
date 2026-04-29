"""Celery tasks that enrich scraped artists with Spotify data.

The nightly :func:`enrich_unenriched_artists` task walks up to 100
artists whose ``spotify_enriched_at`` is NULL, hits Spotify's search
endpoint with an app-only access token, and delegates the per-artist
decision to :func:`backend.services.artist_enrichment.enrich_artist`.

Kept separate from :mod:`backend.services.artist_enrichment` so the
pure business-logic module has no Celery or HTTP imports — the task
here owns the session, the app token, and the per-artist try/except so
one flaky candidate does not poison the whole batch.
"""

from __future__ import annotations

import uuid
from typing import Any

from celery import shared_task

from backend.core.database import get_session_factory
from backend.core.exceptions import AppError
from backend.core.logging import get_logger
from backend.data.repositories import artists as artists_repo
from backend.services import spotify as spotify_service
from backend.services.artist_enrichment import enrich_artist

logger = get_logger(__name__)

BATCH_SIZE = 100


@shared_task(
    name="backend.services.artist_enrichment_tasks.enrich_unenriched_artists",
)  # type: ignore[untyped-decorator]
def enrich_unenriched_artists() -> dict[str, Any]:
    """Enrich up to :data:`BATCH_SIZE` unenriched artists in one pass.

    Owns its own session and Spotify app-token lifecycle. Per-artist
    failures (search errors, malformed payloads) are caught and counted
    rather than re-raised, so a single bad name does not stop the batch
    — the nightly cron will retry on the next tick. If the initial app-
    token mint itself fails, the task returns early with a failure
    count; Celery will surface the underlying :class:`AppError` via the
    result backend for ops visibility.

    Returns:
        Summary dict with keys ``processed``, ``matched``, ``unmatched``,
        ``errors`` (counts), and ``token_failed`` (bool) when the app
        token could not be minted.
    """
    session_factory = get_session_factory()
    with session_factory() as session:
        try:
            pending = artists_repo.list_unenriched_artists(session, limit=BATCH_SIZE)
            if not pending:
                return {
                    "processed": 0,
                    "matched": 0,
                    "unmatched": 0,
                    "errors": 0,
                    "token_failed": False,
                }

            try:
                tokens = spotify_service.get_app_access_token()
            except AppError as exc:
                logger.warning(
                    "artist_enrichment_token_failed",
                    extra={"error": exc.message},
                )
                return {
                    "processed": 0,
                    "matched": 0,
                    "unmatched": 0,
                    "errors": 0,
                    "token_failed": True,
                }

            matched = 0
            unmatched = 0
            errors = 0
            for artist in pending:
                try:
                    results = spotify_service.search_artist(
                        tokens.access_token, artist.name
                    )
                    updated = enrich_artist(session, artist, search_results=results)
                    if updated.spotify_id:
                        matched += 1
                    else:
                        unmatched += 1
                except Exception as exc:
                    errors += 1
                    logger.warning(
                        "artist_enrichment_failed",
                        extra={
                            "artist_id": str(artist.id),
                            "artist_name": artist.name,
                            "error": str(exc),
                        },
                    )
                    # Keep the session usable for the next iteration —
                    # a mid-batch flush failure (rare, but possible if
                    # Spotify returns an id that violates a constraint)
                    # would otherwise poison subsequent updates.
                    session.rollback()

            session.commit()
            return {
                "processed": len(pending),
                "matched": matched,
                "unmatched": unmatched,
                "errors": errors,
                "token_failed": False,
            }
        except Exception:
            session.rollback()
            raise


@shared_task(
    name="backend.services.artist_enrichment_tasks.enrich_artist_from_spotify",
)  # type: ignore[untyped-decorator]
def enrich_artist_from_spotify(artist_id: str) -> dict[str, Any]:
    """Enrich a single artist row on demand.

    An ops primitive for re-enriching a specific artist — useful when a
    scraper name was malformed and got saved as the wrong Spotify id, or
    when a user reports a wrong genre tag on the frontend.

    Args:
        artist_id: UUID string of the artist row to enrich.

    Returns:
        Dict describing the outcome (matched/unmatched/missing).

    Raises:
        AppError: ``SPOTIFY_AUTH_FAILED`` if the app token cannot be
            minted or the search call fails.
    """
    uid = uuid.UUID(artist_id)
    session_factory = get_session_factory()
    with session_factory() as session:
        try:
            artist = artists_repo.get_artist_by_id(session, uid)
            if artist is None:
                return {"artist_id": artist_id, "status": "missing"}

            tokens = spotify_service.get_app_access_token()
            results = spotify_service.search_artist(tokens.access_token, artist.name)
            updated = enrich_artist(session, artist, search_results=results)
            session.commit()
            return {
                "artist_id": artist_id,
                "status": "matched" if updated.spotify_id else "unmatched",
                "spotify_id": updated.spotify_id,
                "genres": list(updated.genres or []),
            }
        except Exception:
            session.rollback()
            raise

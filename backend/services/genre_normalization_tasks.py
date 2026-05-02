"""Celery tasks that normalize per-source genre data into canonical genres.

This is the third leg of the Sprint 1A/1B/1C genre stack. Sprint 1A
populates :attr:`Artist.musicbrainz_genres` + ``musicbrainz_tags``;
Sprint 1B populates :attr:`Artist.lastfm_tags`; this sprint reads both
and writes :attr:`Artist.canonical_genres`,
:attr:`Artist.genre_confidence`, and :attr:`Artist.genres_normalized_at`
via :func:`backend.services.genre_normalization.normalize_genres`.

**Two tasks:**

* :func:`normalize_artist_genres` runs the normalizer for one artist.
  Idempotent — safe to call repeatedly, and re-runs after the mapping
  dictionary changes will update the canonical assignment.
* :func:`backfill_genre_normalization` runs every artist that needs
  normalization in a single task. Normalization is pure Python with no
  API calls or rate limit, so the full backfill drains in one fire
  rather than fanning out per-artist tasks.

**Idempotency.** The per-artist task always stamps
``genres_normalized_at``, so an empty-output row is treated as "we ran
the normalizer and got no canonical mapping" rather than "we have
never run the normalizer". Re-runs are safe.

**Selection logic.** The backfill picks rows whose
``genres_normalized_at`` is NULL or whose MusicBrainz/Last.fm
enrichment timestamp is newer than their normalization timestamp. Re-
runs after fresh source data are folded in automatically every night.
A ``force=True`` flag re-normalizes every artist (use after a mapping
dictionary change so old assignments don't linger).
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from celery import shared_task

from backend.core.database import get_session_factory
from backend.core.logging import get_logger
from backend.data.repositories import artists as artists_repo
from backend.services.genre_normalization import normalize_genres

if TYPE_CHECKING:
    from backend.data.models.artists import Artist

logger = get_logger(__name__)

# 5000 is well above the live artist count and gives the backfill a
# safe ceiling — normalization is ~milliseconds per artist so even an
# unusually large catalog drains inside the beat fire's expiration
# window.
BACKFILL_BATCH_SIZE = 5000


def _gather_musicbrainz_signal(artist: Artist) -> list[dict[str, Any]]:
    """Combine the MusicBrainz curated genres and free-form tags.

    MusicBrainz exposes two arrays per artist: ``genres`` (curated
    label-style entries) and ``tags`` (free-form, user-applied with
    vote counts). Both inform our canonical mapping — genres alone are
    too sparse on long-tail artists, and tags alone miss the official
    label assignments. The mapper treats every entry the same way
    (canonical pattern match), so concatenation is safe.

    Args:
        artist: The :class:`Artist` row being normalized.

    Returns:
        Concatenated list of MusicBrainz genre/tag dicts. Empty list
        when both columns are None or empty.
    """
    combined: list[dict[str, Any]] = []
    if artist.musicbrainz_genres:
        combined.extend(artist.musicbrainz_genres)
    if artist.musicbrainz_tags:
        combined.extend(artist.musicbrainz_tags)
    return combined


@shared_task(
    name="backend.services.genre_normalization_tasks.normalize_artist_genres",
)  # type: ignore[untyped-decorator]
def normalize_artist_genres(artist_id: str) -> dict[str, Any]:
    """Normalize one artist's MusicBrainz + Last.fm signals to canonical genres.

    Reads :attr:`Artist.musicbrainz_genres`, ``musicbrainz_tags``, and
    ``lastfm_tags`` from the DB, runs them through
    :func:`backend.services.genre_normalization.normalize_genres`, and
    persists the resulting ordered canonical labels and confidence map
    via :func:`backend.data.repositories.artists.mark_artist_genres_normalized`.

    Idempotent: re-runs after the mapping dictionary changes simply
    overwrite the previous assignment. Safe to invoke from a backfill
    task or directly as a hook after Last.fm enrichment.

    Args:
        artist_id: UUID string of the artist row to normalize.

    Returns:
        Outcome dict with ``status`` (``normalized`` /
        ``empty`` / ``missing``), ``artist_id``, and — on success —
        ``genre_count``.
    """
    uid = uuid.UUID(artist_id)
    session_factory = get_session_factory()
    with session_factory() as session:
        try:
            artist = artists_repo.get_artist_by_id(session, uid)
            if artist is None:
                return {"artist_id": artist_id, "status": "missing"}

            mb_signal = _gather_musicbrainz_signal(artist)
            lfm_signal = artist.lastfm_tags or []
            genres, confidence = normalize_genres(mb_signal, lfm_signal)

            artists_repo.mark_artist_genres_normalized(
                session,
                artist,
                canonical_genres=genres,
                genre_confidence=confidence,
            )
            session.commit()

            status = "normalized" if genres else "empty"
            logger.info(
                "genre_normalization_done",
                extra={
                    "artist_id": artist_id,
                    "artist_name": artist.name,
                    "genre_count": len(genres),
                    "status": status,
                },
            )
            return {
                "artist_id": artist_id,
                "status": status,
                "genre_count": len(genres),
            }
        except Exception:
            session.rollback()
            raise


@shared_task(
    name="backend.services.genre_normalization_tasks.backfill_genre_normalization",
)  # type: ignore[untyped-decorator]
def backfill_genre_normalization(force: bool = False) -> dict[str, Any]:
    """Run the normalizer over every artist whose canonical genres are stale.

    Selection:

    * Default mode: artists where ``genres_normalized_at`` is NULL, or
      where either ``musicbrainz_enriched_at`` or ``lastfm_enriched_at``
      is more recent than ``genres_normalized_at``. This catches new
      artists and folds in fresh upstream enrichments every night.
    * ``force=True``: every artist, oldest first. Use after the mapping
      dictionary changes so previously-normalized rows reflect the
      updated rules.

    Normalization is pure Python (no API calls, no rate limit), so this
    task processes every selected row inline rather than fanning out
    per-artist Celery tasks. The :data:`BACKFILL_BATCH_SIZE` ceiling is
    a safety bound, not a per-fire intent — the live catalog is well
    below it.

    Args:
        force: When True, re-normalize every artist regardless of the
            normalization timestamp.

    Returns:
        Summary dict with ``processed`` (rows touched), ``with_genres``
        (rows that produced at least one canonical genre), and
        ``empty`` (rows that produced no canonical mapping).
    """
    session_factory = get_session_factory()
    with session_factory() as session:
        pending = artists_repo.list_artists_for_genre_normalization(
            session, limit=BACKFILL_BATCH_SIZE, force=force
        )
        with_genres = 0
        empty = 0
        for artist in pending:
            mb_signal = _gather_musicbrainz_signal(artist)
            lfm_signal = artist.lastfm_tags or []
            genres, confidence = normalize_genres(mb_signal, lfm_signal)
            artists_repo.mark_artist_genres_normalized(
                session,
                artist,
                canonical_genres=genres,
                genre_confidence=confidence,
            )
            if genres:
                with_genres += 1
            else:
                empty += 1
        session.commit()
        logger.info(
            "genre_normalization_backfill_done",
            extra={
                "processed": len(pending),
                "with_genres": with_genres,
                "empty": empty,
                "force": force,
            },
        )
        return {
            "processed": len(pending),
            "with_genres": with_genres,
            "empty": empty,
        }

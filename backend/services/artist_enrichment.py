"""Spotify enrichment for :class:`backend.data.models.artists.Artist` rows.

A nightly Celery task walks unenriched artists (see
:mod:`backend.services.artist_enrichment_tasks`) and calls
:func:`enrich_artist` for each. Keeping the decision logic — pick the
best Spotify candidate above the similarity threshold, else record a
null match — out of the task itself means we can unit-test it without
spinning up Celery or a live Spotify account.

The similarity threshold is tuned for the scraped-performer-name
domain, where Spotify search often returns a reasonable first candidate
but an occasional completely unrelated artist when the scraped name is
obscure. 0.85 is high enough to reject "Phoebe Bridgers" vs "Phoebe"
drift but forgiving enough to accept "The Beths" vs "Beths".
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import TYPE_CHECKING, Any

from backend.core.logging import get_logger
from backend.core.text import normalize_artist_name
from backend.data.repositories import artists as artists_repo

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from backend.data.models.artists import Artist

logger = get_logger(__name__)

SIMILARITY_THRESHOLD = 0.85


def _pick_best_match(
    target_name: str,
    candidates: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Return the Spotify candidate whose normalized name best matches.

    Walks the candidate list (Spotify search already orders by relevance,
    but we re-score because Spotify's ranking factors in popularity and
    can bubble up a more popular wrong-name artist above a less-popular
    correct one). The normalized-name ``SequenceMatcher`` ratio acts as a
    secondary check keyed only on the name itself.

    Args:
        target_name: The scraper's artist name, before normalization.
        candidates: Raw Spotify artist dicts from :func:`search_artist`.

    Returns:
        The best candidate with similarity >= ``SIMILARITY_THRESHOLD``,
        or None when no candidate clears the bar.
    """
    if not candidates:
        return None

    target_key = normalize_artist_name(target_name)
    if not target_key:
        return None

    best_candidate: dict[str, Any] | None = None
    best_score = 0.0
    for candidate in candidates:
        raw_name = candidate.get("name")
        if not isinstance(raw_name, str) or not raw_name.strip():
            continue
        candidate_key = normalize_artist_name(raw_name)
        score = SequenceMatcher(None, target_key, candidate_key).ratio()
        if score > best_score:
            best_score = score
            best_candidate = candidate

    if best_score < SIMILARITY_THRESHOLD:
        return None
    return best_candidate


def _extract_genres(candidate: dict[str, Any]) -> list[str]:
    """Pull the ``genres`` array off a Spotify artist payload.

    Args:
        candidate: Raw Spotify artist dict.

    Returns:
        The genre strings, lowercased and stripped. Empty list when the
        payload has no genres or the field is malformed.
    """
    raw = candidate.get("genres") or []
    if not isinstance(raw, list):
        return []
    cleaned: list[str] = []
    for genre in raw:
        if isinstance(genre, str) and genre.strip():
            cleaned.append(genre.strip().lower())
    return cleaned


def enrich_artist(
    session: Session,
    artist: Artist,
    *,
    search_results: list[dict[str, Any]],
) -> Artist:
    """Persist a Spotify enrichment attempt for a single artist.

    Resolves the best Spotify candidate from ``search_results`` and
    stamps ``spotify_enriched_at`` unconditionally — a "no match" still
    counts as enrichment so the nightly task does not re-check the row.

    Args:
        session: Active SQLAlchemy session.
        artist: The :class:`Artist` to enrich.
        search_results: Candidates from
            :func:`backend.services.spotify.search_artist`. Tasks call
            the HTTP layer and pass the raw list here so the decision
            logic is fully unit-testable without a network stub.

    Returns:
        The updated :class:`Artist` row (same instance).
    """
    best = _pick_best_match(artist.name, search_results)
    if best is None:
        logger.info(
            "artist_enrichment_no_match",
            extra={"artist_id": str(artist.id), "artist_name": artist.name},
        )
        return artists_repo.mark_artist_enriched(
            session,
            artist,
            spotify_id=None,
            genres=[],
        )

    spotify_id = best.get("id")
    spotify_id_str = (
        spotify_id.strip()
        if isinstance(spotify_id, str) and spotify_id.strip()
        else None
    )
    genres = _extract_genres(best)
    logger.info(
        "artist_enrichment_matched",
        extra={
            "artist_id": str(artist.id),
            "artist_name": artist.name,
            "spotify_id": spotify_id_str,
            "genres_count": len(genres),
        },
    )
    return artists_repo.mark_artist_enriched(
        session,
        artist,
        spotify_id=spotify_id_str,
        genres=genres,
    )

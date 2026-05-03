"""Admin-triggered artist catalog hydration (Decision 067).

Hydration grows the ``artists`` table by inserting similar-artists from
an existing seed artist's Last.fm similarity rows. The operation is
deliberately controlled — without these guardrails an enthusiastic
admin could explode the catalog with thousands of weak matches in an
afternoon.

Four guardrails:

* :data:`MAX_ARTISTS_PER_HYDRATION` — at most 5 new artists per call.
* :data:`MIN_SIMILARITY_SCORE` — Last.fm scores below 0.5 are too weak
  to be worth catalog clutter.
* :data:`MAX_HYDRATION_DEPTH` — every artist must sit within two hops
  of a real DMV-scraped seed; we do not chain hydrations forever.
* :data:`DAILY_HYDRATION_CAP` — a global 24-hour cap of 100 new
  artists, computed from the audit log.

Two public functions:

* :func:`preview_hydration` is the read-only inspection used by the
  admin UI's confirmation modal. It classifies each candidate
  (``eligible`` / ``already_exists`` / ``below_threshold`` /
  ``depth_exceeded``) and reports the daily cap remaining without
  modifying the database.

* :func:`execute_hydration` is the side-effectful path. It re-validates
  against a fresh preview, applies the daily cap mid-execution,
  inserts new :class:`Artist` rows with full lineage metadata, queues
  per-artist enrichment Celery tasks, and writes a single audit-log
  row capturing the whole call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal

from sqlalchemy import func, select

from backend.core.logging import get_logger
from backend.core.text import normalize_artist_name
from backend.data.models.artist_similarity import ArtistSimilarity
from backend.data.models.artists import Artist
from backend.data.models.hydration_log import HydrationLog

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.orm import Session

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Hydration controls — NOT user-configurable.
# ---------------------------------------------------------------------------

MAX_ARTISTS_PER_HYDRATION: int = 5
"""Maximum similar artists added per single hydration call."""

MIN_SIMILARITY_SCORE: float = 0.5
"""Minimum Last.fm similarity score for an artist to be eligible.

Last.fm reports 0.0-1.0 similarity. Below 0.5 the long tail of weak
matches is not worth the catalog clutter — see the archive entry on
Decision 067 for the operational tuning notes.
"""

MAX_HYDRATION_DEPTH: int = 2
"""Maximum depth — artists at this depth or higher cannot be hydrated.

0 = original (scraper-seeded). 1 = added by hydrating an original.
2 = added by hydrating a depth-1 artist. Capping at 2 keeps every
artist within two hops of a real DMV-scraped seed.
"""

DAILY_HYDRATION_CAP: int = 100
"""Total new artists allowed across all hydrations in a 24-hour window.

Computed from :class:`HydrationLog`, not from
:attr:`Artist.hydration_source`, so any future background hydrations
(e.g. a recommendation-engine batch) do not consume the manual cap.
"""

CandidateStatus = Literal[
    "eligible", "already_exists", "below_threshold", "depth_exceeded"
]
"""Per-candidate classification surfaced to the confirmation modal."""


# ---------------------------------------------------------------------------
# Result shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HydrationCandidate:
    """One row in the candidate list for a hydration preview.

    Attributes:
        similar_artist_name: Display name as Last.fm returned it.
        similar_artist_mbid: MusicBrainz id when Last.fm carried one.
        similarity_score: Provider-reported similarity (0.0-1.0).
        status: Why this candidate did or didn't make the cut.
        existing_artist_id: When ``status == "already_exists"``, the
            UUID of the existing :class:`Artist` row, else ``None``.
    """

    similar_artist_name: str
    similar_artist_mbid: str | None
    similarity_score: float
    status: CandidateStatus
    existing_artist_id: uuid.UUID | None


@dataclass(frozen=True)
class HydrationPreview:
    """Read-only snapshot driving the admin confirmation modal.

    Attributes:
        source_artist: The seed artist whose similarities are being
            considered.
        candidates: Every similarity edge for the source, classified.
        eligible_count: Count of candidates with status ``eligible``.
        would_add_count: ``min(eligible_count, daily_cap_remaining,
            MAX_ARTISTS_PER_HYDRATION)`` — what the operator sees as
            "we will add N artists if you confirm".
        daily_cap_remaining: Slots left under
            :data:`DAILY_HYDRATION_CAP` for the next 24 hours.
        can_proceed: ``True`` when at least one candidate could be
            added; ``False`` when the source is at max depth or the
            daily cap is exhausted.
        blocking_reason: Human-readable explanation of why
            ``can_proceed`` is False, else ``None``.
    """

    source_artist: Artist
    candidates: list[HydrationCandidate]
    eligible_count: int
    would_add_count: int
    daily_cap_remaining: int
    can_proceed: bool
    blocking_reason: str | None


@dataclass
class HydrationResult:
    """Return value of :func:`execute_hydration`.

    Attributes:
        source_artist_id: UUID of the seed artist for the hydration.
        added_artists: Newly created :class:`Artist` rows.
        added_count: Convenience count — ``len(added_artists)``.
        skipped_count: Candidates skipped because they already exist
            in ``artists``.
        filtered_count: Candidates dropped because their similarity
            score was below :data:`MIN_SIMILARITY_SCORE`.
        daily_cap_hit: True when the cap clipped the additions.
        blocking_reason: Set when no artists were added because of
            depth exhaustion or a missing source row.
    """

    source_artist_id: uuid.UUID
    added_artists: list[Artist] = field(default_factory=list)
    added_count: int = 0
    skipped_count: int = 0
    filtered_count: int = 0
    daily_cap_hit: bool = False
    blocking_reason: str | None = None


# ---------------------------------------------------------------------------
# Daily-cap helper
# ---------------------------------------------------------------------------


def get_daily_hydration_count(session: Session) -> int:
    """Count artists added via hydration in the last 24 hours.

    Reads from :class:`HydrationLog` rather than from the artists
    table because the artists count would mix in any future
    background hydrations (none today; could exist later) that should
    not consume the operator-facing daily cap.

    Args:
        session: Active SQLAlchemy session.

    Returns:
        Total UUIDs across the ``added_artist_ids`` arrays of every
        :class:`HydrationLog` row created in the last 24 hours.
    """
    cutoff = datetime.now(UTC) - timedelta(hours=24)
    stmt = select(
        func.coalesce(func.sum(func.cardinality(HydrationLog.added_artist_ids)), 0)
    ).where(HydrationLog.created_at >= cutoff)
    return int(session.execute(stmt).scalar_one() or 0)


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


def preview_hydration(
    session: Session, source_artist_id: uuid.UUID
) -> HydrationPreview | None:
    """Produce a read-only preview of what hydrating ``source_artist_id`` would do.

    Reads the source artist's similarity edges, classifies each one
    against the existing artist catalog and the controls
    (depth/threshold), and reports the daily cap headroom.

    Args:
        session: Active SQLAlchemy session.
        source_artist_id: UUID of the seed artist.

    Returns:
        A :class:`HydrationPreview`, or ``None`` when the source
        artist does not exist.
    """
    source = session.get(Artist, source_artist_id)
    if source is None:
        return None

    edges = list(
        session.execute(
            select(ArtistSimilarity)
            .where(ArtistSimilarity.source_artist_id == source_artist_id)
            .order_by(ArtistSimilarity.similarity_score.desc())
        )
        .scalars()
        .all()
    )

    cap_remaining = max(0, DAILY_HYDRATION_CAP - get_daily_hydration_count(session))

    if source.hydration_depth >= MAX_HYDRATION_DEPTH:
        depth_reason = (
            f"Source artist is at hydration depth {source.hydration_depth}; "
            f"cannot hydrate beyond depth {MAX_HYDRATION_DEPTH}."
        )
        candidates = [
            HydrationCandidate(
                similar_artist_name=edge.similar_artist_name,
                similar_artist_mbid=edge.similar_artist_mbid,
                similarity_score=float(edge.similarity_score),
                status="depth_exceeded",
                existing_artist_id=None,
            )
            for edge in edges
        ]
        return HydrationPreview(
            source_artist=source,
            candidates=candidates,
            eligible_count=0,
            would_add_count=0,
            daily_cap_remaining=cap_remaining,
            can_proceed=False,
            blocking_reason=depth_reason,
        )

    existing_index = _index_existing_artists(
        session,
        names=[edge.similar_artist_name for edge in edges],
    )

    candidates: list[HydrationCandidate] = []
    eligible = 0
    for edge in edges:
        normalized = normalize_artist_name(edge.similar_artist_name)
        existing_id = existing_index.get(normalized)
        score = float(edge.similarity_score)
        if existing_id is not None:
            status: CandidateStatus = "already_exists"
        elif score < MIN_SIMILARITY_SCORE:
            status = "below_threshold"
        else:
            status = "eligible"
            eligible += 1
        candidates.append(
            HydrationCandidate(
                similar_artist_name=edge.similar_artist_name,
                similar_artist_mbid=edge.similar_artist_mbid,
                similarity_score=score,
                status=status,
                existing_artist_id=existing_id,
            )
        )

    would_add = min(eligible, cap_remaining, MAX_ARTISTS_PER_HYDRATION)
    can_proceed = would_add > 0
    blocking_reason: str | None = None
    if not can_proceed:
        if cap_remaining == 0:
            blocking_reason = (
                f"Daily hydration cap of {DAILY_HYDRATION_CAP} reached; "
                "try again later."
            )
        elif eligible == 0:
            blocking_reason = (
                "No eligible candidates — every similar artist either "
                "already exists or is below the similarity threshold."
            )

    return HydrationPreview(
        source_artist=source,
        candidates=candidates,
        eligible_count=eligible,
        would_add_count=would_add,
        daily_cap_remaining=cap_remaining,
        can_proceed=can_proceed,
        blocking_reason=blocking_reason,
    )


# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------


def execute_hydration(
    session: Session,
    source_artist_id: uuid.UUID,
    *,
    admin_email: str,
    confirmed_candidates: list[str],
    immediate: bool = False,
) -> HydrationResult:
    """Insert the confirmed similar-artist rows and audit the call.

    Re-runs :func:`preview_hydration` so a stale confirmation can't
    bypass current state (a freshly-added artist or a daily cap that
    closed since the modal opened). The new artist rows are committed
    in one transaction; per-artist enrichment task queuing happens
    after the commit so a broker outage does not roll back the writes.

    Args:
        session: Active SQLAlchemy session. Owned by the caller
            (a Flask request, the CLI, or the bulk Celery task).
        source_artist_id: UUID of the seed artist.
        admin_email: Email of the operator triggering the hydration —
            stored in the audit log.
        confirmed_candidates: Display names the operator confirmed in
            the modal. Names not in the current eligible set (e.g.
            because the artist was added since the preview ran) are
            silently dropped — the audit log records the discrepancy.
        immediate: When ``True``, the per-artist enrichment tasks are
            queued for immediate execution rather than waiting for the
            nightly schedule. Counts against the same Last.fm rate
            limit; use sparingly.

    Returns:
        A :class:`HydrationResult` summarizing the outcome.
    """
    result = HydrationResult(source_artist_id=source_artist_id)

    preview = preview_hydration(session, source_artist_id)
    if preview is None:
        result.blocking_reason = (
            f"No artist found with id {source_artist_id}; "
            "cannot hydrate a missing source."
        )
        return result

    if not preview.can_proceed:
        result.blocking_reason = preview.blocking_reason
        result.skipped_count = sum(
            1 for c in preview.candidates if c.status == "already_exists"
        )
        result.filtered_count = sum(
            1 for c in preview.candidates if c.status == "below_threshold"
        )
        result.daily_cap_hit = preview.daily_cap_remaining == 0
        # Still write an audit row so the failed attempt is visible.
        _record_audit(
            session,
            source_artist_id=source_artist_id,
            admin_email=admin_email,
            candidates=preview.candidates,
            added_ids=[],
            skipped=result.skipped_count,
            filtered=result.filtered_count,
            daily_cap_hit=result.daily_cap_hit,
        )
        session.commit()
        return result

    confirmed = set(confirmed_candidates)
    by_name = {c.similar_artist_name: c for c in preview.candidates}

    # Counts cover the whole candidate set — the audit row is meant to
    # explain the entire shape of the operation, not just what the
    # operator confirmed.
    result.skipped_count = sum(
        1 for c in preview.candidates if c.status == "already_exists"
    )
    result.filtered_count = sum(
        1 for c in preview.candidates if c.status == "below_threshold"
    )

    cap_remaining = preview.daily_cap_remaining
    parent_depth = preview.source_artist.hydration_depth

    additions: list[Artist] = []
    for name in confirmed_candidates:
        candidate = by_name.get(name)
        if candidate is None or candidate.status != "eligible":
            continue
        if len(additions) >= MAX_ARTISTS_PER_HYDRATION:
            break
        if len(additions) >= cap_remaining:
            result.daily_cap_hit = True
            break
        new_artist = Artist(
            name=name.strip(),
            normalized_name=normalize_artist_name(name),
            genres=[],
            hydration_source="similar_artist",
            hydrated_from_artist_id=source_artist_id,
            hydration_depth=parent_depth + 1,
            hydrated_at=datetime.now(UTC),
        )
        session.add(new_artist)
        additions.append(new_artist)

    # Discard the dropped candidates from the confirmed list before
    # flushing so the audit row reflects what actually happened.
    confirmed_eligible = sum(
        1
        for n in confirmed
        if (c := by_name.get(n)) is not None and c.status == "eligible"
    )
    if (
        not result.daily_cap_hit
        and len(additions) < confirmed_eligible
        and cap_remaining < confirmed_eligible
    ):
        result.daily_cap_hit = True

    session.flush()
    added_ids = [a.id for a in additions]

    _record_audit(
        session,
        source_artist_id=source_artist_id,
        admin_email=admin_email,
        candidates=preview.candidates,
        added_ids=added_ids,
        skipped=result.skipped_count,
        filtered=result.filtered_count,
        daily_cap_hit=result.daily_cap_hit,
    )
    session.commit()

    result.added_artists = additions
    result.added_count = len(additions)

    for artist in additions:
        _enqueue_full_enrichment(artist.id, immediate=immediate)

    logger.info(
        "artist_hydration_executed",
        extra={
            "source_artist_id": str(source_artist_id),
            "admin_email": admin_email,
            "added_count": result.added_count,
            "skipped_count": result.skipped_count,
            "filtered_count": result.filtered_count,
            "daily_cap_hit": result.daily_cap_hit,
        },
    )
    return result


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _index_existing_artists(
    session: Session, *, names: list[str]
) -> dict[str, uuid.UUID]:
    """Map normalized name → existing artist UUID for the given names.

    Args:
        session: Active SQLAlchemy session.
        names: Display names to look up; normalized via
            :func:`backend.core.text.normalize_artist_name` before the
            query so case/diacritic variants resolve to one row.

    Returns:
        Mapping of normalized name to UUID. Missing names are absent.
    """
    if not names:
        return {}
    keys = list({normalize_artist_name(n) for n in names if n})
    if not keys:
        return {}
    stmt = select(Artist.normalized_name, Artist.id).where(
        Artist.normalized_name.in_(keys)
    )
    return {key: artist_id for key, artist_id in session.execute(stmt).all()}


def _record_audit(
    session: Session,
    *,
    source_artist_id: uuid.UUID,
    admin_email: str,
    candidates: list[HydrationCandidate],
    added_ids: list[uuid.UUID],
    skipped: int,
    filtered: int,
    daily_cap_hit: bool,
) -> HydrationLog:
    """Append a :class:`HydrationLog` row capturing this hydration.

    Args:
        session: Active SQLAlchemy session. Caller commits.
        source_artist_id: UUID of the seed artist.
        admin_email: Operator email (verbatim).
        candidates: Candidate snapshot from the preview that drove this
            execution. Stored as JSONB so the modal's view is preserved
            even if state shifts later.
        added_ids: UUIDs of the artist rows actually created.
        skipped: Count of already-existing candidates dropped.
        filtered: Count of below-threshold candidates dropped.
        daily_cap_hit: True when the cap clipped this call.

    Returns:
        The :class:`HydrationLog` row.
    """
    log = HydrationLog(
        source_artist_id=source_artist_id,
        admin_email=admin_email,
        candidate_artists=[_serialize_candidate(c) for c in candidates],
        added_artist_ids=list(added_ids),
        skipped_count=skipped,
        filtered_count=filtered,
        daily_cap_hit=daily_cap_hit,
    )
    session.add(log)
    session.flush()
    return log


def _serialize_candidate(candidate: HydrationCandidate) -> dict[str, object]:
    """Render a :class:`HydrationCandidate` as JSONB-safe primitives.

    Args:
        candidate: The candidate to serialize.

    Returns:
        A dict suitable for storing in the audit log.
    """
    return {
        "similar_artist_name": candidate.similar_artist_name,
        "similar_artist_mbid": candidate.similar_artist_mbid,
        "similarity_score": candidate.similarity_score,
        "status": candidate.status,
        "existing_artist_id": (
            str(candidate.existing_artist_id) if candidate.existing_artist_id else None
        ),
    }


# Tasks are wired by registered name (string) so this service module
# does not import ``backend.celery_app`` at import time — the layer rule
# keeps services free of broker imports for the same reason scrapers
# are. The list mirrors the four enrichment passes a brand-new artist
# row would otherwise wait on (MusicBrainz → Last.fm → similar →
# Spotify); each task tolerates being run before the prior one
# completes.
ENRICHMENT_TASK_NAMES: tuple[str, ...] = (
    "backend.services.musicbrainz_tasks.enrich_artist_from_musicbrainz",
    "backend.services.lastfm_tasks.enrich_artist_from_lastfm",
    "backend.services.lastfm_similarity_tasks.enrich_artist_similarity_from_lastfm",
    "backend.services.artist_enrichment_tasks.enrich_artist_from_spotify",
)


def _send_enrichment_task(task_name: str, artist_id: str) -> None:
    """Hand off one enrichment call to the Celery broker.

    Wrapped in a helper so tests can monkey-patch the broker call out
    of the dispatch path without poking at ``celery_app``.

    Args:
        task_name: Registered Celery task name.
        artist_id: UUID string of the artist to enrich.
    """
    try:
        from backend.celery_app import celery_app

        celery_app.send_task(task_name, args=[artist_id])
    except Exception:
        # Enrichment retries on its own nightly schedule; a broker
        # outage here just means the new artist takes longer to
        # populate genres/tags, not that the hydration itself failed.
        logger.exception(
            "hydration_enrichment_enqueue_failed",
            extra={"task_name": task_name, "artist_id": artist_id},
        )


def _enqueue_full_enrichment(artist_id: uuid.UUID, *, immediate: bool) -> None:
    """Queue every enrichment pass for one freshly hydrated artist.

    Args:
        artist_id: UUID of the new artist row.
        immediate: When True, signals to the queue that the operator
            wants results in minutes rather than waiting for the
            nightly schedule. Today this is informational — every task
            already runs as soon as the worker picks it up — but is
            preserved on the audit trail for tuning.
    """
    str_id = str(artist_id)
    for task_name in ENRICHMENT_TASK_NAMES:
        _send_enrichment_task(task_name, str_id)
    if immediate:
        logger.info(
            "hydration_immediate_enrichment_requested",
            extra={"artist_id": str_id},
        )

"""One-time backfill: seed the ``artists`` table from existing events.

Before the ingestion path learned to upsert artists
(commit feat(scraper): upsert artists and persist event genres at
ingestion), every scraped event stored its performer names only in the
``events.artists`` array. This script walks every existing event,
collects the distinct performer names, and upserts each into the
``artists`` table so the nightly Spotify enrichment task has rows to
work on.

Idempotent: :func:`upsert_artist_by_name` is keyed on the normalized
name, so re-running the script never creates duplicate rows.

Usage:
    python -m backend.scripts.backfill_artist_enrichment [--dry-run]

``--dry-run`` prints the counts the script would write but never calls
the database's write path.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

from backend.core.database import get_session_factory
from backend.core.logging import get_logger
from backend.core.text import normalize_artist_name
from backend.data.repositories import artists as artists_repo
from backend.data.repositories import events as events_repo

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = get_logger(__name__)


@dataclass(frozen=True)
class BackfillSummary:
    """Counts the script emits after a pass.

    Attributes:
        scanned: Distinct non-empty artist names found across all events.
        created: Number of new rows inserted into the ``artists`` table
            (i.e. names whose normalized key was not already present).
        already_present: Names that mapped to a row that already existed.
        skipped_blank: Raw names dropped because they normalized to an
            empty string (whitespace-only, punctuation-only, etc.).
    """

    scanned: int
    created: int
    already_present: int
    skipped_blank: int


def backfill_artists_from_events(
    session: Session,
    *,
    dry_run: bool = False,
) -> BackfillSummary:
    """Seed the ``artists`` table with every performer name on events.

    Walks :func:`events_repo.list_all_event_artist_names`, filters out
    whitespace-only and duplicate-by-normalized-key entries, and calls
    :func:`artists_repo.upsert_artist_by_name` for each unique name. The
    repo upsert already handles the "row exists" case, so this function
    only needs to detect pre-existing rows for reporting purposes.

    Args:
        session: Active SQLAlchemy session.
        dry_run: When True, skip all writes and only count what would
            have been done. A dry-run never calls ``upsert_artist_by_name``.

    Returns:
        A :class:`BackfillSummary` with per-bucket counts.
    """
    raw_names = events_repo.list_all_event_artist_names(session)
    seen_keys: set[str] = set()
    created = 0
    already_present = 0
    skipped_blank = 0
    scanned = 0

    for raw_name in raw_names:
        normalized = normalize_artist_name(raw_name)
        if not normalized:
            skipped_blank += 1
            continue
        if normalized in seen_keys:
            continue
        seen_keys.add(normalized)
        scanned += 1

        existing = artists_repo.get_artist_by_normalized_name(session, normalized)
        if existing is not None:
            already_present += 1
            continue

        if not dry_run:
            artists_repo.upsert_artist_by_name(session, raw_name)
        created += 1

    return BackfillSummary(
        scanned=scanned,
        created=created,
        already_present=already_present,
        skipped_blank=skipped_blank,
    )


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m backend.scripts.backfill_artist_enrichment``.

    Args:
        argv: Optional argv override for testing. When None, parses from
            :data:`sys.argv`.

    Returns:
        Process exit code: 0 on success, non-zero on unexpected failure.
    """
    parser = argparse.ArgumentParser(
        description="Seed the artists table from existing event rows.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count what would be written without touching the DB.",
    )
    args = parser.parse_args(argv)

    session_factory = get_session_factory()
    with session_factory() as session:
        try:
            summary = backfill_artists_from_events(session, dry_run=args.dry_run)
            if args.dry_run:
                session.rollback()
            else:
                session.commit()
        except Exception:
            session.rollback()
            raise

    logger.info(
        "artist_backfill_complete",
        extra={
            "scanned": summary.scanned,
            # ``created_count`` avoids the reserved LogRecord.created key.
            "created_count": summary.created,
            "already_present": summary.already_present,
            "skipped_blank": summary.skipped_blank,
            "dry_run": args.dry_run,
        },
    )
    # Print a human summary to stdout so the operator sees output when
    # running the script interactively — logs alone go to stderr/JSON.
    prefix = "[dry-run] " if args.dry_run else ""
    print(
        f"{prefix}scanned={summary.scanned} "
        f"created={summary.created} "
        f"already_present={summary.already_present} "
        f"skipped_blank={summary.skipped_blank}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

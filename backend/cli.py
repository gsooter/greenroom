"""Greenroom command-line interface.

Run via ``python -m backend.cli <command> [args...]``. Currently
exposes the artist-hydration tooling so a Railway shell session can
drive catalog growth without opening the admin UI. Every CLI command
shares the underlying service code with the admin endpoints — the same
controls (depth, similarity threshold, per-call cap, daily cap)
apply.
"""

from __future__ import annotations

import sys
import uuid
from typing import TYPE_CHECKING

import click
from sqlalchemy import select

from backend.core.database import get_session_factory
from backend.data.models.artists import Artist
from backend.data.models.events import Event
from backend.data.models.venues import Venue
from backend.services.admin_dashboard import (
    best_hydration_candidates,
    most_hydrated_leaderboard,
)
from backend.services.artist_hydration import (
    DAILY_HYDRATION_CAP,
    MAX_ARTISTS_PER_HYDRATION,
    execute_hydration,
    get_daily_hydration_count,
    preview_hydration,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

DEFAULT_CLI_OPERATOR = "cli@greenroom.local"


@click.group()
def cli() -> None:
    """Greenroom command-line interface."""


def _resolve_artist(
    session: Session, *, artist_id: str | None, artist_name: str | None
) -> Artist | None:
    """Look up an artist by id or name for CLI subcommands.

    Args:
        session: Active SQLAlchemy session.
        artist_id: UUID string from ``--artist-id``.
        artist_name: Display name from ``--artist-name``.

    Returns:
        The matching :class:`Artist`, or ``None`` if nothing matches.
    """
    if artist_id:
        try:
            uid = uuid.UUID(artist_id)
        except ValueError:
            click.echo(f"Invalid UUID: {artist_id}", err=True)
            return None
        return session.get(Artist, uid)
    if artist_name:
        from backend.core.text import normalize_artist_name

        key = normalize_artist_name(artist_name)
        stmt = select(Artist).where(Artist.normalized_name == key)
        return session.execute(stmt).scalar_one_or_none()
    return None


@cli.command()
@click.option("--artist-id", help="UUID of the seed artist to hydrate.")
@click.option("--artist-name", help="Name of the seed artist to hydrate.")
@click.option(
    "--operator",
    default=DEFAULT_CLI_OPERATOR,
    show_default=True,
    help="Email recorded in the hydration audit log.",
)
@click.option(
    "--yes",
    is_flag=True,
    default=False,
    help="Skip the interactive confirmation prompt.",
)
@click.option(
    "--immediate/--scheduled",
    default=False,
    help="Queue enrichment immediately instead of waiting for the nightly cron.",
)
def hydrate(
    artist_id: str | None,
    artist_name: str | None,
    operator: str,
    yes: bool,
    immediate: bool,
) -> None:
    """Hydrate similar artists for one seed artist.

    Either --artist-id or --artist-name is required. The CLI prints
    the candidate list and asks for confirmation unless --yes is
    passed.
    """
    if not artist_id and not artist_name:
        click.echo("One of --artist-id or --artist-name is required.", err=True)
        sys.exit(2)
    session_factory = get_session_factory()
    with session_factory() as session:
        artist = _resolve_artist(session, artist_id=artist_id, artist_name=artist_name)
        if artist is None:
            click.echo("No matching artist found.", err=True)
            sys.exit(1)
        preview = preview_hydration(session, artist.id)
        if preview is None:
            click.echo("Artist not found after lookup.", err=True)
            sys.exit(1)

        click.echo(
            f"Hydrating from {artist.name} (depth {artist.hydration_depth}). "
            f"Daily cap: {preview.daily_cap_remaining}/{DAILY_HYDRATION_CAP} remaining."
        )
        if not preview.can_proceed:
            click.echo(f"Cannot proceed: {preview.blocking_reason}", err=True)
            sys.exit(1)

        eligible = [c for c in preview.candidates if c.status == "eligible"]
        for candidate in eligible[:MAX_ARTISTS_PER_HYDRATION]:
            click.echo(
                f"  + {candidate.similar_artist_name} "
                f"({candidate.similarity_score:.2f})"
            )
        skipped = [c for c in preview.candidates if c.status != "eligible"]
        if skipped:
            click.echo(f"  ({len(skipped)} candidates filtered or already exist)")

        if not yes:
            click.confirm(f"Add {preview.would_add_count} artist(s)?", abort=True)

        confirmed = [
            c.similar_artist_name for c in eligible[:MAX_ARTISTS_PER_HYDRATION]
        ]
        result = execute_hydration(
            session,
            artist.id,
            admin_email=operator,
            confirmed_candidates=confirmed,
            immediate=immediate,
        )
        if result.added_count == 0:
            click.echo(
                f"No artists added: {result.blocking_reason or 'see audit log'}.",
                err=True,
            )
            sys.exit(1)
        click.echo(f"Added {result.added_count} artist(s).")
        for added in result.added_artists:
            click.echo(f"  - {added.name} ({added.id})")
        if result.daily_cap_hit:
            click.echo("Daily cap was hit — fewer artists added than confirmed.")


@cli.command("hydrate-bulk")
@click.option(
    "--venue",
    "venue_slug",
    help="Hydrate every artist with an upcoming event at this venue.",
)
@click.option(
    "--operator",
    default=DEFAULT_CLI_OPERATOR,
    show_default=True,
    help="Email recorded in the audit log.",
)
@click.option(
    "--max-sources",
    default=10,
    show_default=True,
    help="Maximum source artists to iterate before stopping.",
)
@click.option(
    "--yes",
    is_flag=True,
    default=False,
    help="Skip the interactive confirmation prompt.",
)
def hydrate_bulk(
    venue_slug: str | None, operator: str, max_sources: int, yes: bool
) -> None:
    """Hydrate similar artists for every source on a list.

    Currently supports --venue: iterates through every artist with an
    upcoming event at the given venue and hydrates each in order
    until the daily cap is reached.
    """
    if not venue_slug:
        click.echo("--venue is required (more selectors coming).", err=True)
        sys.exit(2)
    session_factory = get_session_factory()
    with session_factory() as session:
        sources = _artists_at_venue(session, venue_slug=venue_slug, limit=max_sources)
        if not sources:
            click.echo(f"No upcoming-event artists found at {venue_slug}.")
            return

        click.echo(
            f"Found {len(sources)} source artist(s) at {venue_slug}. "
            f"Daily cap remaining: "
            f"{DAILY_HYDRATION_CAP - get_daily_hydration_count(session)}."
        )
        if not yes:
            click.confirm("Proceed with bulk hydration?", abort=True)

        total_added = 0
        for artist in sources:
            preview = preview_hydration(session, artist.id)
            if preview is None or not preview.can_proceed:
                continue
            confirmed = [
                c.similar_artist_name
                for c in preview.candidates
                if c.status == "eligible"
            ][:MAX_ARTISTS_PER_HYDRATION]
            result = execute_hydration(
                session,
                artist.id,
                admin_email=operator,
                confirmed_candidates=confirmed,
            )
            click.echo(
                f"  {artist.name}: +{result.added_count} "
                f"(skipped {result.skipped_count}, "
                f"filtered {result.filtered_count})"
            )
            total_added += result.added_count
            if result.daily_cap_hit:
                click.echo("Daily cap reached — stopping bulk run.")
                break
        click.echo(f"Done. Added {total_added} artist(s) total.")


@cli.command("hydration-stats")
@click.option(
    "--days",
    default=30,
    show_default=True,
    help="Window for the most-hydrated leaderboard.",
)
@click.option("--limit", default=10, show_default=True, help="Rows per leaderboard.")
def hydration_stats(days: int, limit: int) -> None:
    """Print the hydration leaderboards and current daily-cap status."""
    session_factory = get_session_factory()
    with session_factory() as session:
        cap_used = get_daily_hydration_count(session)
        click.echo(
            f"Daily cap: {DAILY_HYDRATION_CAP - cap_used}/{DAILY_HYDRATION_CAP} "
            "remaining."
        )

        click.echo(f"\nMost hydrated (last {days} days):")
        for row in most_hydrated_leaderboard(session, days=days, limit=limit):
            click.echo(f"  {row.artist_name}: {row.hydration_count} added")

        click.echo("\nBest hydration candidates:")
        for cand in best_hydration_candidates(session, limit=limit):
            preview = (
                f" — top: {cand.top_candidate_name}" if cand.top_candidate_name else ""
            )
            click.echo(
                f"  {cand.artist_name}: {cand.candidate_count} candidates{preview}"
            )


def _artists_at_venue(session: Session, *, venue_slug: str, limit: int) -> list[Artist]:
    """Return artists with upcoming events at the given venue.

    Args:
        session: Active SQLAlchemy session.
        venue_slug: Slug of the venue.
        limit: Maximum source artists to return.

    Returns:
        Up to ``limit`` :class:`Artist` rows whose normalized name
        matches a performer on an upcoming event at the venue.
    """
    from datetime import UTC, datetime

    from backend.core.text import normalize_artist_name

    venue = session.execute(
        select(Venue).where(Venue.slug == venue_slug)
    ).scalar_one_or_none()
    if venue is None:
        return []

    rows = session.execute(
        select(Event.artists)
        .where(Event.venue_id == venue.id)
        .where(Event.starts_at >= datetime.now(UTC))
    ).all()

    seen: dict[str, None] = {}
    for row in rows:
        names = row[0] or []
        for name in names:
            key = normalize_artist_name(name)
            if key and key not in seen:
                seen[key] = None
            if len(seen) >= limit * 4:
                break
        if len(seen) >= limit * 4:
            break

    keys = list(seen)[: limit * 4]
    if not keys:
        return []
    artist_rows = list(
        session.execute(
            select(Artist).where(Artist.normalized_name.in_(keys)).limit(limit)
        )
        .scalars()
        .all()
    )
    return artist_rows


if __name__ == "__main__":
    cli()

"""Repository tests for :mod:`backend.data.repositories.artists`.

Runs against the ``greenroom_test`` Postgres database using the
transactional fixture in ``conftest.py`` — every write is rolled back
on teardown.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from backend.data.models.artists import Artist
from backend.data.repositories import artists as artists_repo


def test_upsert_creates_new_row_keyed_on_normalized_name(session: Session) -> None:
    artist = artists_repo.upsert_artist_by_name(session, "Phoebe Bridgers")
    assert artist.id is not None
    assert artist.name == "Phoebe Bridgers"
    assert artist.normalized_name == "phoebe bridgers"
    assert artist.genres == []
    assert artist.spotify_enriched_at is None


def test_upsert_collapses_case_and_diacritic_spellings(session: Session) -> None:
    first = artists_repo.upsert_artist_by_name(session, "Beyoncé")
    second = artists_repo.upsert_artist_by_name(session, "BEYONCE")
    third = artists_repo.upsert_artist_by_name(session, "  beyonce  ")
    assert first.id == second.id == third.id
    # First spelling wins for display casing.
    assert first.name == "Beyoncé"


def test_get_artist_by_normalized_name_returns_none_when_missing(
    session: Session,
) -> None:
    assert artists_repo.get_artist_by_normalized_name(session, "unknown") is None


def test_list_unenriched_artists_filters_and_orders(session: Session) -> None:
    # Oldest, already enriched — should not appear.
    enriched = Artist(
        name="Old Band",
        normalized_name="old band",
        genres=["rock"],
        spotify_enriched_at=datetime.now(UTC),
    )
    # Unenriched, in insertion order.
    pending_one = Artist(name="First", normalized_name="first", genres=[])
    pending_two = Artist(name="Second", normalized_name="second", genres=[])
    session.add_all([enriched, pending_one, pending_two])
    session.flush()

    results = artists_repo.list_unenriched_artists(session, limit=10)
    names = [a.normalized_name for a in results]
    assert "old band" not in names
    assert names == ["first", "second"]


def test_list_unenriched_artists_honors_limit(session: Session) -> None:
    for i in range(5):
        session.add(Artist(name=f"A {i}", normalized_name=f"a {i}", genres=[]))
    session.flush()

    results = artists_repo.list_unenriched_artists(session, limit=3)
    assert len(results) == 3


def test_mark_artist_enriched_stamps_fields(session: Session) -> None:
    artist = artists_repo.upsert_artist_by_name(session, "The Beths")
    updated = artists_repo.mark_artist_enriched(
        session,
        artist,
        spotify_id="sp-xyz",
        genres=["indie", "rock"],
    )
    assert updated.spotify_id == "sp-xyz"
    assert updated.genres == ["indie", "rock"]
    assert updated.spotify_enriched_at is not None


def test_mark_artist_enriched_records_a_null_match(session: Session) -> None:
    """Callers pass ``spotify_id=None, genres=[]`` when no match is found."""
    artist = artists_repo.upsert_artist_by_name(session, "Very Niche Act")
    updated = artists_repo.mark_artist_enriched(
        session,
        artist,
        spotify_id=None,
        genres=[],
    )
    # The enriched-at stamp still fires so the nightly task does not
    # re-check this row on the next pass.
    assert updated.spotify_id is None
    assert updated.genres == []
    assert updated.spotify_enriched_at is not None

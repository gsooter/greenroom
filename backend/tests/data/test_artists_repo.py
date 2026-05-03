"""Repository tests for :mod:`backend.data.repositories.artists`.

Runs against the ``greenroom_test`` Postgres database using the
transactional fixture in ``conftest.py`` — every write is rolled back
on teardown.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

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


def test_search_artists_substring_match_is_case_and_diacritic_insensitive(
    session: Session,
) -> None:
    artists_repo.upsert_artist_by_name(session, "Beyoncé")
    artists_repo.upsert_artist_by_name(session, "Phoebe Bridgers")
    artists_repo.upsert_artist_by_name(session, "Beach House")

    results = artists_repo.search_artists(session, query="BEYONCE")
    assert [a.name for a in results] == ["Beyoncé"]

    results = artists_repo.search_artists(session, query="beach")
    assert [a.name for a in results] == ["Beach House"]


def test_search_artists_empty_and_whitespace_query_returns_empty(
    session: Session,
) -> None:
    artists_repo.upsert_artist_by_name(session, "Anyone")
    assert artists_repo.search_artists(session, query="") == []
    assert artists_repo.search_artists(session, query="   ") == []


def test_search_artists_honors_limit(session: Session) -> None:
    for i in range(5):
        artists_repo.upsert_artist_by_name(session, f"Match {i}")

    results = artists_repo.search_artists(session, query="match", limit=3)
    assert len(results) == 3


# ---------------------------------------------------------------------------
# MusicBrainz enrichment helpers
# ---------------------------------------------------------------------------


def test_list_artists_for_musicbrainz_enrichment_filters_to_unenriched(
    session: Session,
) -> None:
    fresh = Artist(
        name="Fresh",
        normalized_name="fresh",
        genres=[],
        musicbrainz_enriched_at=datetime.now(UTC),
    )
    pending_one = Artist(name="A", normalized_name="a", genres=[])
    pending_two = Artist(name="B", normalized_name="b", genres=[])
    session.add_all([fresh, pending_one, pending_two])
    session.flush()

    results = artists_repo.list_artists_for_musicbrainz_enrichment(session, limit=10)
    names = [a.normalized_name for a in results]
    assert "fresh" not in names
    assert names == ["a", "b"]


def test_list_artists_for_musicbrainz_enrichment_includes_stale_when_threshold_set(
    session: Session,
) -> None:
    """A row enriched longer ago than ``stale_after`` is eligible."""
    stale = Artist(
        name="Stale",
        normalized_name="stale",
        genres=[],
        musicbrainz_enriched_at=datetime.now(UTC) - timedelta(days=60),
    )
    fresh = Artist(
        name="Fresh",
        normalized_name="fresh",
        genres=[],
        musicbrainz_enriched_at=datetime.now(UTC),
    )
    session.add_all([stale, fresh])
    session.flush()

    results = artists_repo.list_artists_for_musicbrainz_enrichment(
        session, limit=10, stale_after=timedelta(days=30)
    )
    names = [a.normalized_name for a in results]
    assert "stale" in names
    assert "fresh" not in names


def test_mark_artist_musicbrainz_enriched_stamps_all_fields(
    session: Session,
) -> None:
    artist = artists_repo.upsert_artist_by_name(session, "boygenius")
    genres = [{"name": "indie rock", "count": 12}]
    tags = [{"name": "supergroup", "count": 4}]
    updated = artists_repo.mark_artist_musicbrainz_enriched(
        session,
        artist,
        musicbrainz_id="9c0bd8b6-1c1d-49ec-9cb3-0fd9f9d6b3e3",
        genres=genres,
        tags=tags,
        confidence=Decimal("0.95"),
    )
    assert updated.musicbrainz_id == "9c0bd8b6-1c1d-49ec-9cb3-0fd9f9d6b3e3"
    assert updated.musicbrainz_genres == genres
    assert updated.musicbrainz_tags == tags
    assert updated.musicbrainz_match_confidence == Decimal("0.95")
    assert updated.musicbrainz_enriched_at is not None


def test_mark_artist_musicbrainz_enriched_records_null_match(
    session: Session,
) -> None:
    """No-match outcomes still stamp the timestamp so we don't re-search."""
    artist = artists_repo.upsert_artist_by_name(session, "Very Niche Act")
    updated = artists_repo.mark_artist_musicbrainz_enriched(
        session,
        artist,
        musicbrainz_id=None,
        genres=None,
        tags=None,
        confidence=None,
    )
    assert updated.musicbrainz_id is None
    assert updated.musicbrainz_genres is None
    assert updated.musicbrainz_tags is None
    assert updated.musicbrainz_match_confidence is None
    assert updated.musicbrainz_enriched_at is not None


# ---------------------------------------------------------------------------
# Genre normalization helpers
# ---------------------------------------------------------------------------


def test_list_artists_for_genre_normalization_includes_never_normalized(
    session: Session,
) -> None:
    """Rows with ``genres_normalized_at IS NULL`` are always pending."""
    never = Artist(
        name="Never",
        normalized_name="never",
        genres=[],
        musicbrainz_enriched_at=datetime.now(UTC),
    )
    fresh = Artist(
        name="Fresh",
        normalized_name="fresh",
        genres=[],
        musicbrainz_enriched_at=datetime.now(UTC) - timedelta(hours=2),
        genres_normalized_at=datetime.now(UTC),
        canonical_genres=["Indie Rock"],
    )
    session.add_all([never, fresh])
    session.flush()

    results = artists_repo.list_artists_for_genre_normalization(session, limit=10)
    names = [a.normalized_name for a in results]
    assert "never" in names
    assert "fresh" not in names


def test_list_artists_for_genre_normalization_includes_stale_after_mb_refresh(
    session: Session,
) -> None:
    """A MusicBrainz refresh newer than the last normalization re-queues."""
    now = datetime.now(UTC)
    stale = Artist(
        name="Stale",
        normalized_name="stale",
        genres=[],
        musicbrainz_enriched_at=now,
        genres_normalized_at=now - timedelta(days=2),
        canonical_genres=["Pop"],
    )
    session.add(stale)
    session.flush()

    results = artists_repo.list_artists_for_genre_normalization(session, limit=10)
    assert any(a.normalized_name == "stale" for a in results)


def test_list_artists_for_genre_normalization_includes_stale_after_lastfm_refresh(
    session: Session,
) -> None:
    """A Last.fm refresh newer than the last normalization re-queues."""
    now = datetime.now(UTC)
    stale = Artist(
        name="Stale Lastfm",
        normalized_name="stale lastfm",
        genres=[],
        lastfm_enriched_at=now,
        genres_normalized_at=now - timedelta(days=1),
        canonical_genres=["Folk"],
    )
    session.add(stale)
    session.flush()

    results = artists_repo.list_artists_for_genre_normalization(session, limit=10)
    assert any(a.normalized_name == "stale lastfm" for a in results)


def test_list_artists_for_genre_normalization_force_returns_everything(
    session: Session,
) -> None:
    """``force=True`` ignores the timestamp comparison entirely."""
    now = datetime.now(UTC)
    already = Artist(
        name="Already",
        normalized_name="already",
        genres=[],
        musicbrainz_enriched_at=now - timedelta(days=2),
        genres_normalized_at=now,
        canonical_genres=["Pop"],
    )
    session.add(already)
    session.flush()

    default = artists_repo.list_artists_for_genre_normalization(session, limit=10)
    assert all(a.normalized_name != "already" for a in default)

    forced = artists_repo.list_artists_for_genre_normalization(
        session, limit=10, force=True
    )
    assert any(a.normalized_name == "already" for a in forced)


def test_mark_artist_genres_normalized_persists_canonical_output(
    session: Session,
) -> None:
    artist = artists_repo.upsert_artist_by_name(session, "Boygenius")
    canonical = ["Indie Rock", "Folk"]
    confidence = {"Indie Rock": 1.0, "Folk": 0.6}

    updated = artists_repo.mark_artist_genres_normalized(
        session,
        artist,
        canonical_genres=canonical,
        genre_confidence=confidence,
    )

    assert updated.canonical_genres == canonical
    assert updated.genre_confidence == confidence
    assert updated.genres_normalized_at is not None


def test_mark_artist_genres_normalized_stamps_empty_result(session: Session) -> None:
    """Empty output still bumps the timestamp so we don't re-process."""
    artist = artists_repo.upsert_artist_by_name(session, "Niche Act")
    updated = artists_repo.mark_artist_genres_normalized(
        session,
        artist,
        canonical_genres=[],
        genre_confidence={},
    )
    assert updated.canonical_genres == []
    assert updated.genre_confidence == {}
    assert updated.genres_normalized_at is not None

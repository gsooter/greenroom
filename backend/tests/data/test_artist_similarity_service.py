"""Tests for :mod:`backend.services.artist_similarity`.

Lives under ``tests/data`` rather than ``tests/services`` because the
service is heavily SQL-bound — the upcoming-shows magic query relies
on Postgres ``ANY()`` array semantics and the resolution path joins
across artists, events, and venues. Mocks would obscure more than they
illuminate. Each test runs inside the standard rolled-back transaction
provided by :mod:`backend.tests.data.conftest`.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.orm import Session

from backend.data.models.artist_similarity import ArtistSimilarity
from backend.data.models.artists import Artist
from backend.data.models.cities import City
from backend.data.models.events import Event, EventStatus
from backend.data.models.venues import Venue
from backend.services.artist_similarity import (
    ArtistSimilarityResult,
    TagSimilarityResult,
    find_artists_by_tag_similarity,
    get_similar_artists,
    resolve_similarity_links,
    store_similar_artists,
)
from backend.services.lastfm import LastFMSimilarArtist


def _make_artist(
    session: Session,
    *,
    name: str,
    musicbrainz_id: str | None = None,
    granular_tags: list[str] | None = None,
) -> Artist:
    """Insert and return a minimal :class:`Artist` row."""
    artist = Artist(
        name=name,
        normalized_name=name.lower().strip(),
        genres=[],
        musicbrainz_id=musicbrainz_id,
        granular_tags=granular_tags or [],
    )
    session.add(artist)
    session.flush()
    return artist


def _similar(
    name: str,
    score: float,
    *,
    mbid: str | None = None,
) -> LastFMSimilarArtist:
    """Build a :class:`LastFMSimilarArtist` record."""
    return LastFMSimilarArtist(
        name=name,
        mbid=mbid,
        match_score=score,
        url=f"https://www.last.fm/music/{name.replace(' ', '+')}",
        image_url=None,
    )


# ---------------------------------------------------------------------------
# store_similar_artists
# ---------------------------------------------------------------------------


def test_store_inserts_new_rows_for_a_source_artist(session: Session) -> None:
    source = _make_artist(session, name="Phoebe Bridgers")
    store_similar_artists(
        session,
        source.id,
        [_similar("Lucy Dacus", 1.0), _similar("Julien Baker", 0.95)],
    )

    rows = session.query(ArtistSimilarity).all()
    assert len(rows) == 2
    by_name = {r.similar_artist_name: r for r in rows}
    assert by_name["Lucy Dacus"].similarity_score == Decimal("1.000")
    assert by_name["Julien Baker"].similarity_score == Decimal("0.950")
    assert all(r.source == "lastfm" for r in rows)
    assert all(r.source_artist_id == source.id for r in rows)


def test_store_upserts_existing_rows_with_new_score(session: Session) -> None:
    source = _make_artist(session, name="Phoebe Bridgers")
    store_similar_artists(session, source.id, [_similar("Lucy Dacus", 0.5)])
    original = session.query(ArtistSimilarity).one()
    original_id = original.id

    store_similar_artists(session, source.id, [_similar("Lucy Dacus", 0.99)])

    rows = session.query(ArtistSimilarity).all()
    assert len(rows) == 1
    assert rows[0].id == original_id
    assert rows[0].similarity_score == Decimal("0.990")


def test_store_deletes_rows_no_longer_present_in_input(session: Session) -> None:
    """Source artist is the authority — stale rows are removed."""
    source = _make_artist(session, name="Phoebe Bridgers")
    store_similar_artists(
        session,
        source.id,
        [_similar("Lucy Dacus", 0.9), _similar("Julien Baker", 0.85)],
    )
    assert session.query(ArtistSimilarity).count() == 2

    store_similar_artists(session, source.id, [_similar("Lucy Dacus", 0.92)])

    rows = session.query(ArtistSimilarity).all()
    assert len(rows) == 1
    assert rows[0].similar_artist_name == "Lucy Dacus"


def test_store_resolves_similar_artist_id_by_mbid(session: Session) -> None:
    source = _make_artist(session, name="Phoebe Bridgers")
    matched = _make_artist(session, name="Lucy Dacus", musicbrainz_id="mb-lucy")
    store_similar_artists(
        session,
        source.id,
        [_similar("Lucy Dacus DIFFERENT CASE", 0.9, mbid="mb-lucy")],
    )

    row = session.query(ArtistSimilarity).one()
    assert row.similar_artist_id == matched.id


def test_store_resolves_similar_artist_id_by_case_insensitive_name(
    session: Session,
) -> None:
    source = _make_artist(session, name="Phoebe Bridgers")
    matched = _make_artist(session, name="Lucy Dacus")
    store_similar_artists(session, source.id, [_similar("LUCY  DACUS  ", 0.9)])

    row = session.query(ArtistSimilarity).one()
    assert row.similar_artist_id == matched.id


def test_store_does_not_use_fuzzy_matching_for_resolution(
    session: Session,
) -> None:
    """A near-but-not-exact name match must NOT resolve.

    False positives in similarity links pollute recommendations — better
    to leave a row unresolved than to link it to the wrong artist.
    """
    source = _make_artist(session, name="Phoebe Bridgers")
    _make_artist(session, name="Lucy Dacus")
    store_similar_artists(
        session,
        source.id,
        [_similar("Lucy Dacuss", 0.9)],  # extra trailing s
    )

    row = session.query(ArtistSimilarity).one()
    assert row.similar_artist_id is None


def test_store_leaves_similar_id_null_when_no_match_exists(
    session: Session,
) -> None:
    source = _make_artist(session, name="Phoebe Bridgers")
    store_similar_artists(session, source.id, [_similar("Brand New Random Name", 0.9)])

    row = session.query(ArtistSimilarity).one()
    assert row.similar_artist_id is None
    assert row.similar_artist_mbid is None


def test_store_handles_empty_similar_list_by_clearing_existing(
    session: Session,
) -> None:
    source = _make_artist(session, name="Phoebe Bridgers")
    store_similar_artists(session, source.id, [_similar("Lucy Dacus", 0.9)])

    store_similar_artists(session, source.id, [])

    assert session.query(ArtistSimilarity).count() == 0


# ---------------------------------------------------------------------------
# resolve_similarity_links
# ---------------------------------------------------------------------------


def test_resolve_links_unresolved_rows_when_artist_now_exists(
    session: Session,
) -> None:
    """A row inserted before the matching artist row gets linked later."""
    source = _make_artist(session, name="Phoebe Bridgers")
    store_similar_artists(session, source.id, [_similar("Lucy Dacus", 0.9)])
    assert session.query(ArtistSimilarity).one().similar_artist_id is None

    matched = _make_artist(session, name="Lucy Dacus")
    linked = resolve_similarity_links(session)

    assert linked == 1
    assert session.query(ArtistSimilarity).one().similar_artist_id == matched.id


def test_resolve_links_by_mbid_when_both_have_one(session: Session) -> None:
    source = _make_artist(session, name="Phoebe Bridgers")
    store_similar_artists(
        session,
        source.id,
        [_similar("Lucy Dacus Different Case", 0.9, mbid="mb-lucy")],
    )
    matched = _make_artist(session, name="Lucy Dacus", musicbrainz_id="mb-lucy")
    linked = resolve_similarity_links(session)

    assert linked == 1
    assert session.query(ArtistSimilarity).one().similar_artist_id == matched.id


def test_resolve_does_not_modify_already_linked_rows(session: Session) -> None:
    source = _make_artist(session, name="Phoebe Bridgers")
    matched = _make_artist(session, name="Lucy Dacus")
    store_similar_artists(session, source.id, [_similar("Lucy Dacus", 0.9)])
    assert session.query(ArtistSimilarity).one().similar_artist_id == matched.id

    linked = resolve_similarity_links(session)

    assert linked == 0


def test_resolve_skips_when_no_matching_artist_exists(session: Session) -> None:
    source = _make_artist(session, name="Phoebe Bridgers")
    store_similar_artists(session, source.id, [_similar("Random Unknown", 0.9)])

    linked = resolve_similarity_links(session)

    assert linked == 0
    assert session.query(ArtistSimilarity).one().similar_artist_id is None


# ---------------------------------------------------------------------------
# get_similar_artists
# ---------------------------------------------------------------------------


def test_get_returns_results_sorted_by_score_descending(
    session: Session,
) -> None:
    source = _make_artist(session, name="Phoebe Bridgers")
    store_similar_artists(
        session,
        source.id,
        [
            _similar("Soccer Mommy", 0.6),
            _similar("Lucy Dacus", 0.99),
            _similar("Julien Baker", 0.92),
        ],
    )

    results = get_similar_artists(session, source.id)

    names = [r.similar_artist_name for r in results]
    assert names == ["Lucy Dacus", "Julien Baker", "Soccer Mommy"]
    assert all(isinstance(r, ArtistSimilarityResult) for r in results)


def test_get_respects_limit(session: Session) -> None:
    source = _make_artist(session, name="Phoebe Bridgers")
    store_similar_artists(
        session,
        source.id,
        [_similar(f"Sim {i}", 0.9 - i * 0.01) for i in range(10)],
    )

    results = get_similar_artists(session, source.id, limit=3)
    assert len(results) == 3


def test_get_with_only_with_upcoming_shows_filters_to_event_artists(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_event: Callable[..., Event],
) -> None:
    source = _make_artist(session, name="Phoebe Bridgers")
    has_show = _make_artist(session, name="Lucy Dacus")
    no_show = _make_artist(session, name="Soccer Mommy")
    store_similar_artists(
        session,
        source.id,
        [
            _similar("Lucy Dacus", 0.95),
            _similar("Soccer Mommy", 0.7),
            _similar("Brand New Random", 0.6),
        ],
    )

    city = make_city()
    venue = make_venue(city=city)
    make_event(venue=venue, artists=["Lucy Dacus"])

    results = get_similar_artists(
        session,
        source.id,
        only_with_upcoming_shows=True,
        city_id=city.id,
    )

    names = [r.similar_artist_name for r in results]
    assert names == ["Lucy Dacus"]
    assert results[0].similar_artist_id == has_show.id
    assert results[0].upcoming_show_count == 1
    assert no_show.id not in {r.similar_artist_id for r in results}


def test_get_with_only_with_upcoming_shows_excludes_unresolved(
    session: Session,
    make_city: Callable[..., City],
) -> None:
    """``similar_artist_id IS NULL`` rows can't be joined to events."""
    source = _make_artist(session, name="Phoebe Bridgers")
    store_similar_artists(
        session,
        source.id,
        [_similar("Brand New Random", 0.95)],
    )
    city = make_city()

    results = get_similar_artists(
        session,
        source.id,
        only_with_upcoming_shows=True,
        city_id=city.id,
    )
    assert results == []


def test_get_only_with_upcoming_shows_excludes_past_events(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_event: Callable[..., Event],
) -> None:
    source = _make_artist(session, name="Phoebe Bridgers")
    matched = _make_artist(session, name="Lucy Dacus")
    store_similar_artists(session, source.id, [_similar("Lucy Dacus", 0.9)])

    city = make_city()
    venue = make_venue(city=city)
    make_event(
        venue=venue,
        artists=["Lucy Dacus"],
        starts_at=datetime.now(UTC) - timedelta(days=10),
    )
    results = get_similar_artists(
        session,
        source.id,
        only_with_upcoming_shows=True,
        city_id=city.id,
    )
    assert results == []
    # Without the filter, the row still appears.
    other = get_similar_artists(session, source.id)
    assert other[0].similar_artist_id == matched.id


def test_get_only_with_upcoming_shows_excludes_cancelled_events(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_event: Callable[..., Event],
) -> None:
    source = _make_artist(session, name="Phoebe Bridgers")
    _make_artist(session, name="Lucy Dacus")
    store_similar_artists(session, source.id, [_similar("Lucy Dacus", 0.9)])

    city = make_city()
    venue = make_venue(city=city)
    make_event(
        venue=venue,
        artists=["Lucy Dacus"],
        status=EventStatus.CANCELLED,
    )
    results = get_similar_artists(
        session,
        source.id,
        only_with_upcoming_shows=True,
        city_id=city.id,
    )
    assert results == []


def test_get_returns_empty_list_when_source_artist_has_no_similars(
    session: Session,
) -> None:
    source = _make_artist(session, name="Phoebe Bridgers")
    assert get_similar_artists(session, source.id) == []


def test_get_returns_empty_list_for_unknown_source_artist(
    session: Session,
) -> None:
    assert get_similar_artists(session, uuid.uuid4()) == []


def test_get_payload_includes_similar_artist_id_when_resolved(
    session: Session,
) -> None:
    source = _make_artist(session, name="Phoebe Bridgers")
    matched = _make_artist(session, name="Lucy Dacus")
    store_similar_artists(session, source.id, [_similar("Lucy Dacus", 0.95)])

    results = get_similar_artists(session, source.id)
    assert results[0].similar_artist_id == matched.id
    assert results[0].similarity_score == pytest.approx(0.95, abs=1e-3)


def test_artist_similarity_result_exposes_documented_fields() -> None:
    fields: Any = ArtistSimilarityResult.__dataclass_fields__  # type: ignore[attr-defined]
    assert "similar_artist_name" in fields
    assert "similar_artist_id" in fields
    assert "similarity_score" in fields
    assert "upcoming_show_count" in fields


def test_get_only_with_upcoming_shows_returns_empty_when_no_city(
    session: Session,
) -> None:
    """Without a city scope the magic query loses its locality."""
    source = _make_artist(session, name="Phoebe Bridgers")
    _make_artist(session, name="Lucy Dacus")
    store_similar_artists(session, source.id, [_similar("Lucy Dacus", 0.9)])
    assert (
        get_similar_artists(
            session, source.id, only_with_upcoming_shows=True, city_id=None
        )
        == []
    )


def test_minimum_score_filter_drops_low_score_rows(session: Session) -> None:
    source = _make_artist(session, name="Phoebe Bridgers")
    store_similar_artists(
        session,
        source.id,
        [_similar("High", 0.9), _similar("Low", 0.3)],
    )
    high_only = get_similar_artists(session, source.id, minimum_score=0.5)
    assert [r.similar_artist_name for r in high_only] == ["High"]


# ---------------------------------------------------------------------------
# find_artists_by_tag_similarity
# ---------------------------------------------------------------------------


def test_tag_similarity_returns_results_sorted_by_jaccard_descending(
    session: Session,
) -> None:
    """Higher overlap → higher Jaccard → higher rank."""
    source = _make_artist(
        session,
        name="Phoebe",
        granular_tags=["indie folk", "indie rock", "singer-songwriter", "sad"],
    )
    high_overlap = _make_artist(
        session,
        name="Lucy",
        granular_tags=["indie folk", "indie rock", "singer-songwriter"],
    )
    low_overlap = _make_artist(
        session,
        name="Distant",
        granular_tags=["indie folk", "punk", "hardcore", "noise"],
    )

    results = find_artists_by_tag_similarity(session, source.id, min_overlap=1)

    names = [r.artist_name for r in results]
    assert names.index("Lucy") < names.index("Distant")
    assert results[0].artist_id == high_overlap.id
    assert results[0].jaccard_score > results[-1].jaccard_score
    assert all(isinstance(r, TagSimilarityResult) for r in results)
    assert low_overlap.id in {r.artist_id for r in results}


def test_tag_similarity_excludes_source_artist(session: Session) -> None:
    source = _make_artist(
        session,
        name="Source",
        granular_tags=["indie folk", "indie rock", "shoegaze"],
    )
    _make_artist(
        session,
        name="Other",
        granular_tags=["indie folk", "indie rock", "shoegaze"],
    )
    results = find_artists_by_tag_similarity(session, source.id, min_overlap=1)
    assert source.id not in {r.artist_id for r in results}


def test_tag_similarity_excludes_artists_with_empty_tags(session: Session) -> None:
    source = _make_artist(
        session,
        name="Source",
        granular_tags=["indie folk", "indie rock"],
    )
    _make_artist(session, name="NoTags", granular_tags=[])
    matched = _make_artist(
        session, name="Match", granular_tags=["indie folk", "indie rock"]
    )

    results = find_artists_by_tag_similarity(session, source.id, min_overlap=1)
    artist_ids = {r.artist_id for r in results}
    assert matched.id in artist_ids
    assert all(
        r.artist_name != "NoTags" for r in results
    )  # the empty-tags artist is excluded


def test_tag_similarity_respects_min_overlap(session: Session) -> None:
    """Artists below min_overlap shared tags are filtered out."""
    source = _make_artist(
        session,
        name="Source",
        granular_tags=["indie folk", "indie rock", "shoegaze", "dream pop"],
    )
    _make_artist(
        session,
        name="OneShared",
        granular_tags=["indie folk", "punk", "hardcore"],
    )
    _make_artist(
        session,
        name="ThreeShared",
        granular_tags=["indie folk", "indie rock", "shoegaze", "noise"],
    )

    results = find_artists_by_tag_similarity(session, source.id, min_overlap=3)
    names = [r.artist_name for r in results]
    assert "ThreeShared" in names
    assert "OneShared" not in names


def test_tag_similarity_jaccard_score_matches_manual_computation(
    session: Session,
) -> None:
    """Jaccard score (intersect over union) is computed correctly for known inputs."""
    source = _make_artist(
        session, name="A", granular_tags=["one", "two", "three", "four"]
    )
    _make_artist(
        session,
        name="B",
        granular_tags=["two", "three", "five"],
    )

    results = find_artists_by_tag_similarity(session, source.id, min_overlap=1)
    assert len(results) == 1
    # Intersect = {two, three}, size 2; union = {one,two,three,four,five}, size 5.
    assert results[0].shared_tag_count == 2
    assert results[0].total_tag_union == 5
    assert results[0].jaccard_score == pytest.approx(2 / 5)


def test_tag_similarity_returns_empty_for_unknown_source(session: Session) -> None:
    assert find_artists_by_tag_similarity(session, uuid.uuid4()) == []


def test_tag_similarity_returns_empty_when_source_has_no_tags(
    session: Session,
) -> None:
    """A source artist with no granular tags has nothing to compare against."""
    source = _make_artist(session, name="Empty", granular_tags=[])
    _make_artist(session, name="Other", granular_tags=["indie folk"])

    assert find_artists_by_tag_similarity(session, source.id) == []


def test_tag_similarity_respects_limit(session: Session) -> None:
    source = _make_artist(
        session, name="Source", granular_tags=["a", "b", "c", "d", "e"]
    )
    for i in range(10):
        _make_artist(
            session,
            name=f"Match{i}",
            granular_tags=["a", "b", "c"],
        )

    results = find_artists_by_tag_similarity(session, source.id, min_overlap=1, limit=4)
    assert len(results) == 4


def test_tag_similarity_only_with_upcoming_shows_filters_correctly(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_event: Callable[..., Event],
) -> None:
    source = _make_artist(
        session,
        name="Source",
        granular_tags=["indie folk", "indie rock", "shoegaze"],
    )
    has_show = _make_artist(
        session, name="Has Show", granular_tags=["indie folk", "indie rock", "shoegaze"]
    )
    _make_artist(
        session, name="No Show", granular_tags=["indie folk", "indie rock", "shoegaze"]
    )

    city = make_city()
    venue = make_venue(city=city)
    make_event(venue=venue, artists=["Has Show"])

    results = find_artists_by_tag_similarity(
        session,
        source.id,
        min_overlap=2,
        only_with_upcoming_shows=True,
        city_id=city.id,
    )
    names = [r.artist_name for r in results]
    assert names == ["Has Show"]
    assert results[0].artist_id == has_show.id
    assert results[0].upcoming_show_count == 1


def test_tag_similarity_only_with_upcoming_shows_requires_city(
    session: Session,
) -> None:
    source = _make_artist(session, name="S", granular_tags=["a", "b"])
    _make_artist(session, name="O", granular_tags=["a", "b"])
    assert (
        find_artists_by_tag_similarity(
            session, source.id, only_with_upcoming_shows=True, city_id=None
        )
        == []
    )


def test_tag_similarity_excludes_past_events_in_magic_query(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_event: Callable[..., Event],
) -> None:
    source = _make_artist(
        session, name="Source", granular_tags=["indie", "folk", "rock"]
    )
    _make_artist(session, name="Past", granular_tags=["indie", "folk", "rock"])
    city = make_city()
    venue = make_venue(city=city)
    make_event(
        venue=venue,
        artists=["Past"],
        starts_at=datetime.now(UTC) - timedelta(days=7),
    )
    results = find_artists_by_tag_similarity(
        session,
        source.id,
        min_overlap=2,
        only_with_upcoming_shows=True,
        city_id=city.id,
    )
    assert results == []


def test_tag_similarity_result_dataclass_fields() -> None:
    fields: Any = TagSimilarityResult.__dataclass_fields__  # type: ignore[attr-defined]
    assert "artist_id" in fields
    assert "artist_name" in fields
    assert "shared_tag_count" in fields
    assert "total_tag_union" in fields
    assert "jaccard_score" in fields
    assert "upcoming_show_count" in fields


# ---------------------------------------------------------------------------
# Integration-style: full pipeline through consolidate + similarity
# ---------------------------------------------------------------------------


def test_pipeline_consolidate_then_query_for_indie_folk_artist(
    session: Session,
) -> None:
    """End-to-end smoke: consolidation output drives a meaningful query."""
    from backend.services.tag_consolidation import consolidate_artist_tags

    def mb_genre(name: str, count: int = 6) -> dict[str, Any]:
        return {"name": name, "count": count}

    def lfm_tag(name: str) -> dict[str, Any]:
        return {"name": name, "url": f"https://last.fm/tag/{name}"}

    # Three artists in the same indie-folk neighborhood, plus one
    # outlier that should not surface.
    source = Artist(
        name="Phoebe",
        normalized_name="phoebe",
        genres=[],
        musicbrainz_genres=[mb_genre("indie folk", 12), mb_genre("indie rock", 8)],
        lastfm_tags=[lfm_tag("indie folk"), lfm_tag("singer-songwriter")],
    )
    near = Artist(
        name="Lucy",
        normalized_name="lucy",
        genres=[],
        musicbrainz_genres=[mb_genre("indie rock", 6)],
        lastfm_tags=[lfm_tag("indie folk"), lfm_tag("singer-songwriter")],
    )
    outlier = Artist(
        name="Hardcore",
        normalized_name="hardcore-band",
        genres=[],
        lastfm_tags=[lfm_tag("hardcore"), lfm_tag("punk"), lfm_tag("metalcore")],
    )
    session.add_all([source, near, outlier])
    session.flush()

    consolidate_artist_tags(session, source.id)
    consolidate_artist_tags(session, near.id)
    consolidate_artist_tags(session, outlier.id)
    session.flush()

    results = find_artists_by_tag_similarity(session, source.id, min_overlap=1)
    names = [r.artist_name for r in results]
    assert "Lucy" in names
    assert "Hardcore" not in names

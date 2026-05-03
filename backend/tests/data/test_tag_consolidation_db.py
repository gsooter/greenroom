"""Database-backed tests for :mod:`backend.services.tag_consolidation`.

The pure-function pieces are tested in
``tests/services/test_tag_consolidation.py``. This file covers the
two functions that touch Postgres — ``build_global_tag_blocklist``
counts artists per tag via ``unnest`` and grouping, and
``consolidate_artist_tags`` round-trips through an :class:`Artist`
row. Both rely on Postgres-specific array semantics, so they live in
the data-layer test suite where the standard rolled-back transaction
fixture provides a real session.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from backend.data.models.artists import Artist
from backend.services import tag_consolidation

# ---------------------------------------------------------------------------
# Local helpers
# ---------------------------------------------------------------------------


class _FakeRedis:
    """Minimal in-memory stand-in for the Redis interface we touch."""

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.ttls: dict[str, int] = {}

    def get(self, key: str) -> bytes | None:
        return self.store.get(key)

    def setex(self, key: str, ttl: int, value: bytes) -> None:
        self.store[key] = value
        self.ttls[key] = ttl


def _make_artist(
    session: Session,
    *,
    name: str,
    granular_tags: list[str] | None = None,
    musicbrainz_genres: list[dict[str, Any]] | None = None,
    musicbrainz_tags: list[dict[str, Any]] | None = None,
    lastfm_tags: list[dict[str, Any]] | None = None,
) -> Artist:
    """Insert and return an :class:`Artist` with the requested source data."""
    artist = Artist(
        name=name,
        normalized_name=name.lower().strip() + "-" + uuid.uuid4().hex[:6],
        genres=[],
        granular_tags=granular_tags or [],
        musicbrainz_genres=musicbrainz_genres,
        musicbrainz_tags=musicbrainz_tags,
        lastfm_tags=lastfm_tags,
    )
    session.add(artist)
    session.flush()
    return artist


def _mb(name: str, count: int = 1) -> dict[str, Any]:
    return {"name": name, "count": count}


def _lfm(name: str) -> dict[str, Any]:
    return {"name": name, "url": f"https://last.fm/tag/{name}"}


# ---------------------------------------------------------------------------
# build_global_tag_blocklist
# ---------------------------------------------------------------------------


def test_blocklist_blocks_overly_broad_tags(session: Session) -> None:
    """Tags appearing on >30% of artists should be in the blocklist."""
    # 10 artists, 9 tagged with "rock" (90%, well over 30%).
    for i in range(9):
        _make_artist(session, name=f"Rocker {i}", granular_tags=["rock", "indie"])
    _make_artist(session, name="Lone Folk", granular_tags=["folk"])

    blocklist = tag_consolidation.build_global_tag_blocklist(session)
    assert "rock" in blocklist


def test_blocklist_blocks_too_rare_tags(session: Session) -> None:
    """Tags appearing on fewer than MIN_GLOBAL_FREQUENCY artists are blocked."""
    # 10 artists; "weird-tag" appears once. Below MIN_GLOBAL_FREQUENCY=3.
    _make_artist(session, name="Singular", granular_tags=["weird-tag", "indie"])
    for i in range(9):
        _make_artist(session, name=f"Indie {i}", granular_tags=["indie"])

    blocklist = tag_consolidation.build_global_tag_blocklist(session)
    assert "weird-tag" in blocklist


def test_blocklist_keeps_useful_tags(session: Session) -> None:
    """Tags in the goldilocks zone should NOT be blocked."""
    # 12 artists. ``shoegaze`` appears on 3 (25%, between
    # MIN_GLOBAL_FREQUENCY=3 and the 30% DF cap), ``indie`` appears on
    # all 12 (100%, well over the cap).
    for i in range(12):
        tags = ["indie"]
        if i < 3:
            tags.append("shoegaze")
        _make_artist(session, name=f"Artist {i}", granular_tags=tags)

    blocklist = tag_consolidation.build_global_tag_blocklist(session)
    assert "shoegaze" not in blocklist
    assert "indie" in blocklist


def test_blocklist_returns_empty_when_no_data(session: Session) -> None:
    """Empty corpus → empty blocklist; bootstrapping case."""
    blocklist = tag_consolidation.build_global_tag_blocklist(session)
    assert blocklist == set()


def test_blocklist_caches_result_in_redis(session: Session) -> None:
    """Subsequent calls hit the cache instead of re-counting."""
    _make_artist(session, name="A", granular_tags=["rock", "indie"])
    _make_artist(session, name="B", granular_tags=["rock", "shoegaze"])
    _make_artist(session, name="C", granular_tags=["rock", "folk"])
    _make_artist(session, name="D", granular_tags=["rock"])
    _make_artist(session, name="E", granular_tags=["folk"])

    redis = _FakeRedis()
    first = tag_consolidation.build_global_tag_blocklist(session, redis_client=redis)
    assert first  # something was blocked
    assert tag_consolidation._BLOCKLIST_REDIS_KEY in redis.store
    cached_value = redis.store[tag_consolidation._BLOCKLIST_REDIS_KEY]
    assert b"rock" in cached_value


def test_blocklist_uses_cached_result_when_available(session: Session) -> None:
    """A cached blocklist is returned without database access."""
    redis = _FakeRedis()
    redis.store[tag_consolidation._BLOCKLIST_REDIS_KEY] = b"cached-tag-1\ncached-tag-2"

    # No artists in the DB, but the cache must be honored anyway.
    result = tag_consolidation.build_global_tag_blocklist(session, redis_client=redis)
    assert result == {"cached-tag-1", "cached-tag-2"}


# ---------------------------------------------------------------------------
# consolidate_artist_tags
# ---------------------------------------------------------------------------


def test_consolidate_writes_granular_tags_to_artist(session: Session) -> None:
    artist = _make_artist(
        session,
        name="Phoebe",
        musicbrainz_genres=[_mb("indie folk", 8), _mb("indie rock", 5)],
        lastfm_tags=[_lfm("singer-songwriter"), _lfm("indie folk")],
    )
    result = tag_consolidation.consolidate_artist_tags(session, artist.id)
    assert "indie folk" in result
    session.refresh(artist)
    assert artist.granular_tags == result
    assert artist.granular_tags_consolidated_at is not None


def test_consolidate_skips_blocklisted_tags_when_supplied(session: Session) -> None:
    artist = _make_artist(
        session,
        name="Phoebe",
        musicbrainz_genres=[_mb("indie folk", 8), _mb("indie rock", 5)],
        lastfm_tags=[_lfm("singer-songwriter")],
    )
    result = tag_consolidation.consolidate_artist_tags(
        session,
        artist.id,
        blocklist={"indie rock"},
    )
    assert "indie rock" not in result
    assert "indie folk" in result


def test_consolidate_without_blocklist_keeps_all_tags(session: Session) -> None:
    """First-pass behavior: no blocklist exists → no DF filtering."""
    artist = _make_artist(
        session,
        name="Artist",
        musicbrainz_genres=[_mb("rock", 10), _mb("pop", 8)],
    )
    result = tag_consolidation.consolidate_artist_tags(session, artist.id)
    assert "rock" in result
    assert "pop" in result


def test_consolidate_returns_empty_for_artist_with_no_source_data(
    session: Session,
) -> None:
    artist = _make_artist(session, name="Empty")
    result = tag_consolidation.consolidate_artist_tags(session, artist.id)
    assert result == []
    session.refresh(artist)
    assert artist.granular_tags == []
    assert artist.granular_tags_consolidated_at is not None


def test_consolidate_returns_empty_for_unknown_artist(session: Session) -> None:
    result = tag_consolidation.consolidate_artist_tags(session, uuid.uuid4())
    assert result == []


def test_consolidate_overwrites_previous_granular_tags(session: Session) -> None:
    """Re-running consolidation should reflect the latest source data."""
    artist = _make_artist(
        session,
        name="Phoebe",
        granular_tags=["stale-tag"],
        musicbrainz_genres=[_mb("indie folk", 5)],
    )
    result = tag_consolidation.consolidate_artist_tags(session, artist.id)
    assert "stale-tag" not in result
    assert "indie folk" in result

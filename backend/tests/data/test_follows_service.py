"""Unit tests for :mod:`backend.services.follows`.

Uses the real Postgres test database via the ``session`` fixture so the
service layer's follow/unfollow + search compositions are covered
end-to-end against Postgres semantics.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from sqlalchemy.orm import Session

from backend.core.exceptions import NotFoundError, ValidationError
from backend.data.models.artists import Artist
from backend.data.models.cities import City
from backend.data.models.users import User
from backend.data.models.venues import Venue
from backend.data.repositories import follows as follows_repo
from backend.services import follows as follows_service


def _make_artist(session: Session, name: str) -> Artist:
    """Create an artist row for service-layer tests.

    Args:
        session: Active SQLAlchemy session.
        name: Display name. Normalized form is lowercased.

    Returns:
        The persisted :class:`Artist`.
    """
    artist = Artist(name=name, normalized_name=name.lower(), genres=["indie"])
    session.add(artist)
    session.flush()
    return artist


# ---------------------------------------------------------------------------
# Artist search
# ---------------------------------------------------------------------------


def test_search_artists_tags_followed_flag(
    session: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    followed = _make_artist(session, "Phoebe Bridgers")
    _make_artist(session, "Phoebe Bowls")
    follows_repo.follow_artist(session, user.id, followed.id)

    results = follows_service.search_artists_for_user(session, user, query="phoebe")
    names_and_flags = {r["name"]: r["is_followed"] for r in results}
    assert names_and_flags == {"Phoebe Bridgers": True, "Phoebe Bowls": False}


def test_search_artists_empty_query_returns_empty(
    session: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    _make_artist(session, "Anyone")
    assert follows_service.search_artists_for_user(session, user, query="") == []


def test_search_artists_clamps_limit(
    session: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    for i in range(30):
        _make_artist(session, f"Match {i}")
    # Requested 500; service clamps at 25.
    results = follows_service.search_artists_for_user(
        session, user, query="match", limit=500
    )
    assert len(results) == 25


def test_search_artists_rejects_nonpositive_limit(
    session: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    with pytest.raises(ValidationError):
        follows_service.search_artists_for_user(session, user, query="x", limit=0)


# ---------------------------------------------------------------------------
# Artist follow / unfollow
# ---------------------------------------------------------------------------


def test_follow_artist_writes_edge(
    session: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    artist = _make_artist(session, "The Beths")
    follows_service.follow_artist(session, user, artist.id)
    assert artist.id in follows_repo.list_followed_artist_ids(session, user.id)


def test_follow_artist_rejects_missing_artist(
    session: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    with pytest.raises(NotFoundError):
        follows_service.follow_artist(session, user, uuid.uuid4())


def test_unfollow_artist_is_idempotent(
    session: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    artist = _make_artist(session, "Bon Iver")
    # Never followed — still a no-op success.
    follows_service.unfollow_artist(session, user, artist.id)


def test_list_followed_artists_returns_summaries(
    session: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    artist = _make_artist(session, "Angel Olsen")
    follows_service.follow_artist(session, user, artist.id)
    rows, total = follows_service.list_followed_artists(session, user)
    assert total == 1
    assert rows[0]["name"] == "Angel Olsen"
    assert rows[0]["is_followed"] is True


# ---------------------------------------------------------------------------
# Venue follows — bulk + unfollow
# ---------------------------------------------------------------------------


def test_follow_venues_bulk_writes_each_edge(
    session: Session,
    make_user: Callable[..., User],
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
) -> None:
    user = make_user()
    city = make_city()
    v1 = make_venue(city=city, name="V1")
    v2 = make_venue(city=city, name="V2")

    written = follows_service.follow_venues_bulk(session, user, [v1.id, v2.id])
    assert written == 2
    assert follows_repo.list_followed_venue_ids(session, user.id) == {v1.id, v2.id}


def test_follow_venues_bulk_rejects_missing_venue(
    session: Session,
    make_user: Callable[..., User],
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
) -> None:
    user = make_user()
    city = make_city()
    real = make_venue(city=city)

    with pytest.raises(NotFoundError):
        follows_service.follow_venues_bulk(session, user, [real.id, uuid.uuid4()])
    # Partial write must not land.
    assert follows_repo.list_followed_venue_ids(session, user.id) == set()


def test_follow_venues_bulk_rejects_oversize_batch(
    session: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    ids = [uuid.uuid4() for _ in range(201)]
    with pytest.raises(ValidationError):
        follows_service.follow_venues_bulk(session, user, ids)


def test_follow_venues_bulk_empty_list_is_noop(
    session: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    assert follows_service.follow_venues_bulk(session, user, []) == 0


def test_list_followed_venues_returns_summaries(
    session: Session,
    make_user: Callable[..., User],
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
) -> None:
    user = make_user()
    city = make_city()
    venue = make_venue(city=city, name="Something")
    follows_service.follow_venues_bulk(session, user, [venue.id])
    rows, total = follows_service.list_followed_venues(session, user)
    assert total == 1
    assert rows[0]["name"] == "Something"

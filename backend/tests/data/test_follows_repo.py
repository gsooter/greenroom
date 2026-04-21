"""Repository tests for :mod:`backend.data.repositories.follows`.

Runs against the ``greenroom_test`` Postgres database using the
transactional fixture in ``conftest.py`` — every write is rolled back
on teardown.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from backend.data.models.artists import Artist
from backend.data.models.cities import City
from backend.data.models.users import User
from backend.data.models.venues import Venue
from backend.data.repositories import follows as follows_repo


def _make_artist(session: Session, name: str) -> Artist:
    """Create and flush an Artist row for tests.

    Args:
        session: Active SQLAlchemy session.
        name: Display name. Normalized form is lowercased.

    Returns:
        The persisted :class:`Artist`.
    """
    artist = Artist(name=name, normalized_name=name.lower(), genres=[])
    session.add(artist)
    session.flush()
    return artist


# ---------------------------------------------------------------------------
# Artist follows
# ---------------------------------------------------------------------------


def test_follow_artist_creates_edge(
    session: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    artist = _make_artist(session, "Phoebe Bridgers")

    follows_repo.follow_artist(session, user.id, artist.id)
    ids = follows_repo.list_followed_artist_ids(session, user.id)
    assert ids == {artist.id}


def test_follow_artist_is_idempotent(
    session: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    artist = _make_artist(session, "The Beths")

    follows_repo.follow_artist(session, user.id, artist.id)
    # Repeat call must not raise and must not duplicate the row.
    follows_repo.follow_artist(session, user.id, artist.id)
    ids = follows_repo.list_followed_artist_ids(session, user.id)
    assert ids == {artist.id}


def test_unfollow_artist_removes_edge(
    session: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    artist = _make_artist(session, "Big Thief")

    follows_repo.follow_artist(session, user.id, artist.id)
    follows_repo.unfollow_artist(session, user.id, artist.id)
    assert follows_repo.list_followed_artist_ids(session, user.id) == set()


def test_unfollow_artist_missing_edge_is_noop(
    session: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    artist = _make_artist(session, "Not Followed")
    # Should not raise.
    follows_repo.unfollow_artist(session, user.id, artist.id)


def test_list_followed_artists_returns_newest_first(
    session: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    oldest = _make_artist(session, "A")
    middle = _make_artist(session, "B")
    newest = _make_artist(session, "C")

    follows_repo.follow_artist(
        session, user.id, oldest.id, now=datetime(2026, 1, 1, tzinfo=UTC)
    )
    follows_repo.follow_artist(
        session, user.id, middle.id, now=datetime(2026, 2, 1, tzinfo=UTC)
    )
    follows_repo.follow_artist(
        session, user.id, newest.id, now=datetime(2026, 3, 1, tzinfo=UTC)
    )

    rows, total = follows_repo.list_followed_artists(session, user.id)
    assert total == 3
    assert [a.id for a in rows] == [newest.id, middle.id, oldest.id]


def test_list_followed_artists_paginates(
    session: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    artists = [_make_artist(session, f"A{i}") for i in range(5)]
    for i, artist in enumerate(artists):
        follows_repo.follow_artist(
            session, user.id, artist.id, now=datetime(2026, 1, i + 1, tzinfo=UTC)
        )

    page_one, total = follows_repo.list_followed_artists(
        session, user.id, page=1, per_page=2
    )
    page_two, _ = follows_repo.list_followed_artists(
        session, user.id, page=2, per_page=2
    )
    assert total == 5
    assert len(page_one) == 2
    assert len(page_two) == 2
    # No overlap between pages.
    assert {a.id for a in page_one}.isdisjoint({a.id for a in page_two})


# ---------------------------------------------------------------------------
# Venue follows
# ---------------------------------------------------------------------------


def test_follow_venue_creates_edge(
    session: Session,
    make_user: Callable[..., User],
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
) -> None:
    user = make_user()
    city = make_city()
    venue = make_venue(city=city)

    follows_repo.follow_venue(session, user.id, venue.id)
    assert follows_repo.list_followed_venue_ids(session, user.id) == {venue.id}


def test_follow_venue_is_idempotent(
    session: Session,
    make_user: Callable[..., User],
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
) -> None:
    user = make_user()
    city = make_city()
    venue = make_venue(city=city)

    follows_repo.follow_venue(session, user.id, venue.id)
    follows_repo.follow_venue(session, user.id, venue.id)
    assert follows_repo.list_followed_venue_ids(session, user.id) == {venue.id}


def test_follow_venues_bulk_writes_new_edges_and_dedupes(
    session: Session,
    make_user: Callable[..., User],
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
) -> None:
    user = make_user()
    city = make_city()
    v1 = make_venue(city=city, name="V1")
    v2 = make_venue(city=city, name="V2")
    v3 = make_venue(city=city, name="V3")

    # Duplicate v1 id in the request — dedupe should happen server-side.
    written = follows_repo.follow_venues_bulk(
        session, user.id, [v1.id, v1.id, v2.id, v3.id]
    )
    assert written == 3
    assert follows_repo.list_followed_venue_ids(session, user.id) == {
        v1.id,
        v2.id,
        v3.id,
    }


def test_follow_venues_bulk_skips_existing_edges(
    session: Session,
    make_user: Callable[..., User],
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
) -> None:
    user = make_user()
    city = make_city()
    v1 = make_venue(city=city, name="V1")
    v2 = make_venue(city=city, name="V2")

    follows_repo.follow_venue(session, user.id, v1.id)
    written = follows_repo.follow_venues_bulk(session, user.id, [v1.id, v2.id])
    # Only v2 was new.
    assert written == 1
    assert follows_repo.list_followed_venue_ids(session, user.id) == {v1.id, v2.id}


def test_follow_venues_bulk_empty_list(
    session: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    written = follows_repo.follow_venues_bulk(session, user.id, [])
    assert written == 0


def test_unfollow_venue_removes_edge(
    session: Session,
    make_user: Callable[..., User],
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
) -> None:
    user = make_user()
    city = make_city()
    venue = make_venue(city=city)

    follows_repo.follow_venue(session, user.id, venue.id)
    follows_repo.unfollow_venue(session, user.id, venue.id)
    assert follows_repo.list_followed_venue_ids(session, user.id) == set()


def test_unfollow_venue_missing_edge_is_noop(
    session: Session,
    make_user: Callable[..., User],
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
) -> None:
    user = make_user()
    city = make_city()
    venue = make_venue(city=city)
    # Should not raise.
    follows_repo.unfollow_venue(session, user.id, venue.id)


def test_list_followed_venues_newest_first_and_paginates(
    session: Session,
    make_user: Callable[..., User],
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
) -> None:
    user = make_user()
    city = make_city()
    venues = [make_venue(city=city, name=f"V{i}") for i in range(4)]
    for i, venue in enumerate(venues):
        follows_repo.follow_venue(
            session, user.id, venue.id, now=datetime(2026, 1, i + 1, tzinfo=UTC)
        )

    rows, total = follows_repo.list_followed_venues(
        session, user.id, page=1, per_page=2
    )
    assert total == 4
    # Newest follow (last created) first.
    assert [v.id for v in rows] == [venues[3].id, venues[2].id]

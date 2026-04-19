"""Repository tests for :mod:`backend.data.repositories.users`."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from backend.data.models.cities import City
from backend.data.models.events import Event
from backend.data.models.users import (
    OAuthProvider,
    User,
)
from backend.data.models.venues import Venue
from backend.data.repositories import users as users_repo

# ---------------------------------------------------------------------------
# User CRUD
# ---------------------------------------------------------------------------


def test_get_user_by_id_and_email(
    session: Session, make_user: Callable[..., User]
) -> None:
    user = make_user(email="pat@example.test")
    assert users_repo.get_user_by_id(session, user.id).email == "pat@example.test"
    assert users_repo.get_user_by_email(session, "pat@example.test").id == user.id
    assert users_repo.get_user_by_email(session, "nope@x.test") is None


def test_get_user_by_id_missing(session: Session) -> None:
    assert users_repo.get_user_by_id(session, uuid.uuid4()) is None


def test_create_user_defaults_and_city(
    session: Session, make_city: Callable[..., City]
) -> None:
    city = make_city()
    u = users_repo.create_user(
        session,
        email=f"{uuid.uuid4().hex[:6]}@x.test",
        display_name="Pat",
        city_id=city.id,
    )
    assert u.city_id == city.id
    assert u.is_active is True


def test_update_user_ignores_unknown_fields(
    session: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    updated = users_repo.update_user(session, user, display_name="New", bogus="x")
    assert updated.display_name == "New"


def test_update_last_login_sets_timestamp(
    session: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    assert user.last_login_at is None
    users_repo.update_last_login(session, user)
    assert user.last_login_at is not None


# ---------------------------------------------------------------------------
# OAuth provider
# ---------------------------------------------------------------------------


def test_oauth_create_get_and_update(
    session: Session, make_user: Callable[..., User]
) -> None:
    user = make_user()
    assert users_repo.get_oauth_provider(session, OAuthProvider.SPOTIFY, "sp1") is None

    oauth = users_repo.create_oauth_provider(
        session,
        user_id=user.id,
        provider=OAuthProvider.SPOTIFY,
        provider_user_id="sp1",
        access_token="at",
        refresh_token="rt",
        token_expires_at=datetime.now(UTC) + timedelta(hours=1),
        scopes="user-read-email",
        provider_data={"country": "US"},
    )
    assert oauth.id is not None

    fetched = users_repo.get_oauth_provider(session, OAuthProvider.SPOTIFY, "sp1")
    assert fetched is not None and fetched.id == oauth.id

    # access_token rotation only.
    users_repo.update_oauth_tokens(session, oauth, access_token="at2")
    assert oauth.access_token == "at2"
    assert oauth.refresh_token == "rt"

    new_expiry = datetime.now(UTC) + timedelta(hours=2)
    users_repo.update_oauth_tokens(
        session,
        oauth,
        access_token="at3",
        refresh_token="rt2",
        token_expires_at=new_expiry,
    )
    assert oauth.refresh_token == "rt2"
    assert oauth.token_expires_at.replace(microsecond=0) == (
        new_expiry.replace(microsecond=0)
    )


# ---------------------------------------------------------------------------
# Saved events
# ---------------------------------------------------------------------------


def test_saved_event_create_lookup_list_delete(
    session: Session,
    make_user: Callable[..., User],
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_event: Callable[..., Event],
) -> None:
    user = make_user()
    city = make_city()
    venue = make_venue(city=city)
    e1 = make_event(venue=venue)
    e2 = make_event(venue=venue)

    assert users_repo.get_saved_event(session, user.id, e1.id) is None

    s1 = users_repo.create_saved_event(session, user_id=user.id, event_id=e1.id)
    users_repo.create_saved_event(session, user_id=user.id, event_id=e2.id)

    fetched = users_repo.get_saved_event(session, user.id, e1.id)
    assert fetched is not None and fetched.id == s1.id

    rows, total = users_repo.list_saved_events(session, user.id)
    assert total == 2
    assert len(rows) == 2

    users_repo.delete_saved_event(session, s1)
    rows, total = users_repo.list_saved_events(session, user.id)
    assert total == 1


def test_list_saved_events_pagination(
    session: Session,
    make_user: Callable[..., User],
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_event: Callable[..., Event],
) -> None:
    user = make_user()
    city = make_city()
    venue = make_venue(city=city)
    for _ in range(3):
        ev = make_event(venue=venue)
        users_repo.create_saved_event(session, user_id=user.id, event_id=ev.id)

    rows, total = users_repo.list_saved_events(session, user.id, page=1, per_page=2)
    assert total == 3
    assert len(rows) == 2

    rows_p2, _ = users_repo.list_saved_events(session, user.id, page=2, per_page=2)
    assert len(rows_p2) == 1


# ---------------------------------------------------------------------------
# Recommendations
# ---------------------------------------------------------------------------


def test_recommendation_create_list_dismiss_and_bulk_delete(
    session: Session,
    make_user: Callable[..., User],
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_event: Callable[..., Event],
) -> None:
    user = make_user()
    city = make_city()
    venue = make_venue(city=city)
    e1 = make_event(venue=venue)
    e2 = make_event(venue=venue)

    r1 = users_repo.create_recommendation(
        session,
        user_id=user.id,
        event_id=e1.id,
        score=0.9,
        score_breakdown={"artist_match": 0.9},
    )
    users_repo.create_recommendation(
        session,
        user_id=user.id,
        event_id=e2.id,
        score=0.5,
        score_breakdown={"artist_match": 0.5},
    )

    rows, total = users_repo.list_recommendations(session, user.id)
    assert total == 2
    # Ordered by score descending.
    assert rows[0].score == 0.9

    # Dismiss one — default list should exclude dismissed.
    users_repo.dismiss_recommendation(session, r1)
    rows, total = users_repo.list_recommendations(session, user.id)
    assert total == 1
    assert rows[0].score == 0.5

    # include_dismissed=True returns both.
    rows, total = users_repo.list_recommendations(
        session, user.id, include_dismissed=True
    )
    assert total == 2

    # Pagination path.
    rows, total = users_repo.list_recommendations(
        session, user.id, include_dismissed=True, page=1, per_page=1
    )
    assert total == 2
    assert len(rows) == 1

    # Bulk delete returns count and empties the list.
    deleted = users_repo.delete_recommendations_for_user(session, user.id)
    assert deleted == 2
    rows, total = users_repo.list_recommendations(
        session, user.id, include_dismissed=True
    )
    assert total == 0

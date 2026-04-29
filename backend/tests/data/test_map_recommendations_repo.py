"""Repository tests for :mod:`backend.data.repositories.map_recommendations`.

Runs against the ``greenroom_test`` Postgres database using the
transactional fixture in ``conftest.py`` — every write is rolled back
on teardown.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from backend.data.models.cities import City
from backend.data.models.map_recommendations import MapRecommendationCategory
from backend.data.models.users import User
from backend.data.repositories import map_recommendations as rec_repo


def _make_rec(
    session: Session,
    user: User | None,
    *,
    session_id: str | None = None,
    category: MapRecommendationCategory = MapRecommendationCategory.FOOD,
    body: str = "Great late-night tacos",
    place_name: str = "El Taco Lab",
    place_address: str | None = "2000 14th St NW, Washington, DC",
    latitude: float = 38.917,
    longitude: float = -77.032,
    similarity_score: float = 0.92,
    ip_hash: str | None = None,
    created_at: datetime | None = None,
    suppressed_at: datetime | None = None,
) -> uuid.UUID:
    """Helper — create a recommendation and optionally back-date it.

    Returns:
        The new recommendation's UUID.
    """
    rec = rec_repo.create_recommendation(
        session,
        submitter_user_id=user.id if user else None,
        session_id=session_id if user is None else None,
        place_name=place_name,
        place_address=place_address,
        latitude=latitude,
        longitude=longitude,
        similarity_score=similarity_score,
        category=category,
        body=body,
        ip_hash=ip_hash,
    )
    if created_at is not None:
        rec.created_at = created_at
    if suppressed_at is not None:
        rec.suppressed_at = suppressed_at
    if created_at is not None or suppressed_at is not None:
        session.flush()
    return rec.id


def test_create_and_get_recommendation_roundtrips(
    session: Session,
    make_city: Callable[..., City],
    make_user: Callable[..., User],
) -> None:
    make_city()
    user = make_user()

    rec = rec_repo.create_recommendation(
        session,
        submitter_user_id=user.id,
        session_id=None,
        place_name="Black Cat",
        place_address="1811 14th St NW, Washington, DC",
        latitude=38.9152,
        longitude=-77.0316,
        similarity_score=0.97,
        category=MapRecommendationCategory.DRINKS,
        body="Upstairs bar has the best jukebox",
        ip_hash="abc" * 10,
    )
    fetched = rec_repo.get_recommendation_by_id(session, rec.id)
    assert fetched is not None
    assert fetched.place_name == "Black Cat"
    assert fetched.category == MapRecommendationCategory.DRINKS
    assert fetched.similarity_score == pytest.approx(0.97)


def test_get_recommendation_by_id_missing_returns_none(session: Session) -> None:
    assert rec_repo.get_recommendation_by_id(session, uuid.uuid4()) is None


def test_create_recommendation_accepts_guest_session(
    session: Session,
    make_city: Callable[..., City],
) -> None:
    """Guest submits pass a session_id and no user_id."""
    make_city()
    rec = rec_repo.create_recommendation(
        session,
        submitter_user_id=None,
        session_id="guest-xyz",
        place_name="Pie Shop",
        place_address=None,
        latitude=38.922,
        longitude=-77.003,
        similarity_score=0.88,
        category=MapRecommendationCategory.LATE_NIGHT,
        body="Slice + beer after the show",
        ip_hash=None,
    )
    assert rec.session_id == "guest-xyz"
    assert rec.submitter_user_id is None


def test_create_recommendation_rejects_missing_submitter(
    session: Session,
    make_city: Callable[..., City],
) -> None:
    make_city()
    with pytest.raises(ValueError):
        rec_repo.create_recommendation(
            session,
            submitter_user_id=None,
            session_id=None,
            place_name="Nowhere",
            place_address=None,
            latitude=38.9,
            longitude=-77.0,
            similarity_score=0.9,
            category=MapRecommendationCategory.OTHER,
            body="body",
            ip_hash=None,
        )


def test_list_recommendations_filters_by_bounding_box(
    session: Session,
    make_city: Callable[..., City],
    make_user: Callable[..., User],
) -> None:
    make_city()
    user = make_user()
    inside_id = _make_rec(session, user, latitude=38.917, longitude=-77.032)
    _make_rec(
        session,
        user,
        latitude=40.0,
        longitude=-74.0,
        place_name="Way Outside",
    )

    rows = rec_repo.list_recommendations_in_bounds(
        session,
        sw_lat=38.9,
        sw_lng=-77.05,
        ne_lat=38.93,
        ne_lng=-77.01,
    )
    assert [rec.id for rec, _l, _d in rows] == [inside_id]


def test_list_recommendations_filters_by_category(
    session: Session,
    make_city: Callable[..., City],
    make_user: Callable[..., User],
) -> None:
    make_city()
    user = make_user()
    _make_rec(session, user, category=MapRecommendationCategory.FOOD)
    coffee_id = _make_rec(session, user, category=MapRecommendationCategory.COFFEE)

    rows = rec_repo.list_recommendations_in_bounds(
        session,
        sw_lat=38.9,
        sw_lng=-77.05,
        ne_lat=38.93,
        ne_lng=-77.01,
        category=MapRecommendationCategory.COFFEE,
    )
    assert [rec.id for rec, _l, _d in rows] == [coffee_id]


def test_list_recommendations_hides_suppressed_by_default(
    session: Session,
    make_city: Callable[..., City],
    make_user: Callable[..., User],
) -> None:
    make_city()
    user = make_user()
    visible_id = _make_rec(session, user, body="visible")
    _make_rec(
        session,
        user,
        body="hidden",
        suppressed_at=datetime.now(UTC),
    )

    rows = rec_repo.list_recommendations_in_bounds(
        session,
        sw_lat=38.9,
        sw_lng=-77.05,
        ne_lat=38.93,
        ne_lng=-77.01,
    )
    assert [rec.id for rec, _l, _d in rows] == [visible_id]


def test_list_recommendations_include_suppressed_returns_all(
    session: Session,
    make_city: Callable[..., City],
    make_user: Callable[..., User],
) -> None:
    """Admin tooling can opt into seeing hidden rows."""
    make_city()
    user = make_user()
    _make_rec(session, user, body="visible")
    _make_rec(
        session,
        user,
        body="hidden",
        suppressed_at=datetime.now(UTC),
    )

    rows = rec_repo.list_recommendations_in_bounds(
        session,
        sw_lat=38.9,
        sw_lng=-77.05,
        ne_lat=38.93,
        ne_lng=-77.01,
        include_suppressed=True,
    )
    assert len(rows) == 2


def test_list_recommendations_sort_new_is_reverse_chronological(
    session: Session,
    make_city: Callable[..., City],
    make_user: Callable[..., User],
) -> None:
    make_city()
    user = make_user()
    now = datetime.now(UTC)
    _make_rec(session, user, body="oldest", created_at=now - timedelta(days=3))
    _make_rec(session, user, body="middle", created_at=now - timedelta(days=1))
    _make_rec(session, user, body="newest", created_at=now)

    rows = rec_repo.list_recommendations_in_bounds(
        session,
        sw_lat=38.9,
        sw_lng=-77.05,
        ne_lat=38.93,
        ne_lng=-77.01,
        sort="new",
    )
    assert [rec.body for rec, _l, _d in rows] == ["newest", "middle", "oldest"]


def test_list_recommendations_sort_top_uses_net_votes_plus_recency(
    session: Session,
    make_city: Callable[..., City],
    make_user: Callable[..., User],
) -> None:
    """Top-sort prefers net likes, but a fresh recommendation with no
    votes can still tie an older one with 1 net like thanks to the +1
    recency boost.
    """
    make_city()
    author = make_user()
    voter = make_user()
    now = datetime.now(UTC)
    old_leader_id = _make_rec(
        session, author, body="old leader", created_at=now - timedelta(days=10)
    )
    new_cold_id = _make_rec(session, author, body="new no votes")

    rec_repo.upsert_vote(
        session,
        recommendation_id=old_leader_id,
        user_id=voter.id,
        session_id=None,
        value=1,
    )

    rows = rec_repo.list_recommendations_in_bounds(
        session,
        sw_lat=38.9,
        sw_lng=-77.05,
        ne_lat=38.93,
        ne_lng=-77.01,
        sort="top",
    )
    bodies = [rec.body for rec, _l, _d in rows]
    assert bodies[0] == "new no votes"
    assert bodies[1] == "old leader"
    assert {rec.id for rec, _l, _d in rows} == {old_leader_id, new_cold_id}


def test_list_recommendations_limit_clamps_to_max_200(
    session: Session,
    make_city: Callable[..., City],
    make_user: Callable[..., User],
) -> None:
    make_city()
    user = make_user()
    for i in range(3):
        _make_rec(session, user, body=f"rec-{i}")
    rows = rec_repo.list_recommendations_in_bounds(
        session,
        sw_lat=38.9,
        sw_lng=-77.05,
        ne_lat=38.93,
        ne_lng=-77.01,
        limit=9999,
    )
    assert len(rows) == 3


def test_update_and_delete_recommendation(
    session: Session,
    make_city: Callable[..., City],
    make_user: Callable[..., User],
) -> None:
    make_city()
    user = make_user()
    rec = rec_repo.create_recommendation(
        session,
        submitter_user_id=user.id,
        session_id=None,
        place_name="Ben's Chili Bowl",
        place_address="1213 U St NW, Washington, DC",
        latitude=38.917,
        longitude=-77.029,
        similarity_score=0.95,
        category=MapRecommendationCategory.LATE_NIGHT,
        body="original",
        ip_hash=None,
    )

    rec_repo.update_recommendation_body(session, rec, body="edited")
    fetched = rec_repo.get_recommendation_by_id(session, rec.id)
    assert fetched is not None and fetched.body == "edited"

    rec_repo.delete_recommendation(session, rec)
    assert rec_repo.get_recommendation_by_id(session, rec.id) is None


def test_suppress_and_unsuppress_toggle_visibility(
    session: Session,
    make_city: Callable[..., City],
    make_user: Callable[..., User],
) -> None:
    make_city()
    user = make_user()
    rec_id = _make_rec(session, user, body="controversial")
    rec = rec_repo.get_recommendation_by_id(session, rec_id)
    assert rec is not None

    rec_repo.suppress_recommendation(session, rec)
    rows = rec_repo.list_recommendations_in_bounds(
        session,
        sw_lat=38.9,
        sw_lng=-77.05,
        ne_lat=38.93,
        ne_lng=-77.01,
    )
    assert rows == []

    rec_repo.unsuppress_recommendation(session, rec)
    rows = rec_repo.list_recommendations_in_bounds(
        session,
        sw_lat=38.9,
        sw_lng=-77.05,
        ne_lat=38.93,
        ne_lng=-77.01,
    )
    assert [r.id for r, _l, _d in rows] == [rec_id]


def test_upsert_vote_inserts_then_updates_in_place(
    session: Session,
    make_city: Callable[..., City],
    make_user: Callable[..., User],
) -> None:
    make_city()
    author = make_user()
    voter = make_user()
    rec_id = _make_rec(session, author)

    first = rec_repo.upsert_vote(
        session,
        recommendation_id=rec_id,
        user_id=voter.id,
        session_id=None,
        value=1,
    )
    second = rec_repo.upsert_vote(
        session,
        recommendation_id=rec_id,
        user_id=voter.id,
        session_id=None,
        value=-1,
    )
    assert first.id == second.id
    assert second.value == -1

    likes, dislikes = rec_repo.count_votes_for_recommendation(session, rec_id)
    assert likes == 0 and dislikes == 1


def test_upsert_vote_separate_rows_per_voter(
    session: Session,
    make_city: Callable[..., City],
    make_user: Callable[..., User],
) -> None:
    make_city()
    author = make_user()
    alice = make_user()
    bob = make_user()
    rec_id = _make_rec(session, author)

    rec_repo.upsert_vote(
        session,
        recommendation_id=rec_id,
        user_id=alice.id,
        session_id=None,
        value=1,
    )
    rec_repo.upsert_vote(
        session,
        recommendation_id=rec_id,
        user_id=bob.id,
        session_id=None,
        value=-1,
    )
    likes, dislikes = rec_repo.count_votes_for_recommendation(session, rec_id)
    assert likes == 1 and dislikes == 1


def test_upsert_vote_guest_session_dedupe(
    session: Session,
    make_city: Callable[..., City],
    make_user: Callable[..., User],
) -> None:
    """Two upvotes from the same guest session collapse to one row."""
    make_city()
    author = make_user()
    rec_id = _make_rec(session, author)

    rec_repo.upsert_vote(
        session,
        recommendation_id=rec_id,
        user_id=None,
        session_id="guest-1",
        value=1,
    )
    rec_repo.upsert_vote(
        session,
        recommendation_id=rec_id,
        user_id=None,
        session_id="guest-1",
        value=1,
    )
    likes, dislikes = rec_repo.count_votes_for_recommendation(session, rec_id)
    assert (likes, dislikes) == (1, 0)


def test_upsert_vote_rejects_bad_value(
    session: Session,
    make_city: Callable[..., City],
    make_user: Callable[..., User],
) -> None:
    make_city()
    author = make_user()
    voter = make_user()
    rec_id = _make_rec(session, author)
    with pytest.raises(ValueError):
        rec_repo.upsert_vote(
            session,
            recommendation_id=rec_id,
            user_id=voter.id,
            session_id=None,
            value=5,
        )


def test_upsert_vote_rejects_both_user_and_session(
    session: Session,
    make_city: Callable[..., City],
    make_user: Callable[..., User],
) -> None:
    make_city()
    author = make_user()
    voter = make_user()
    rec_id = _make_rec(session, author)
    with pytest.raises(ValueError):
        rec_repo.upsert_vote(
            session,
            recommendation_id=rec_id,
            user_id=voter.id,
            session_id="guest-1",
            value=1,
        )


def test_upsert_vote_rejects_neither_user_nor_session(
    session: Session,
    make_city: Callable[..., City],
    make_user: Callable[..., User],
) -> None:
    make_city()
    author = make_user()
    rec_id = _make_rec(session, author)
    with pytest.raises(ValueError):
        rec_repo.upsert_vote(
            session,
            recommendation_id=rec_id,
            user_id=None,
            session_id=None,
            value=1,
        )


def test_clear_vote_removes_row_when_present(
    session: Session,
    make_city: Callable[..., City],
    make_user: Callable[..., User],
) -> None:
    make_city()
    author = make_user()
    voter = make_user()
    rec_id = _make_rec(session, author)
    rec_repo.upsert_vote(
        session,
        recommendation_id=rec_id,
        user_id=voter.id,
        session_id=None,
        value=1,
    )
    assert (
        rec_repo.clear_vote(
            session,
            recommendation_id=rec_id,
            user_id=voter.id,
            session_id=None,
        )
        is True
    )
    likes, dislikes = rec_repo.count_votes_for_recommendation(session, rec_id)
    assert (likes, dislikes) == (0, 0)


def test_clear_vote_noop_when_no_existing_vote(
    session: Session,
    make_city: Callable[..., City],
    make_user: Callable[..., User],
) -> None:
    make_city()
    author = make_user()
    voter = make_user()
    rec_id = _make_rec(session, author)
    assert (
        rec_repo.clear_vote(
            session,
            recommendation_id=rec_id,
            user_id=voter.id,
            session_id=None,
        )
        is False
    )


def test_clear_vote_rejects_missing_identity(
    session: Session,
    make_city: Callable[..., City],
    make_user: Callable[..., User],
) -> None:
    make_city()
    author = make_user()
    rec_id = _make_rec(session, author)
    with pytest.raises(ValueError):
        rec_repo.clear_vote(
            session,
            recommendation_id=rec_id,
            user_id=None,
            session_id=None,
        )


def test_count_recent_recommendations_from_ip_respects_window(
    session: Session,
    make_city: Callable[..., City],
    make_user: Callable[..., User],
) -> None:
    make_city()
    user = make_user()
    now = datetime.now(UTC)
    _make_rec(
        session,
        user,
        ip_hash="spammer",
        created_at=now - timedelta(minutes=2),
    )
    _make_rec(
        session,
        user,
        ip_hash="spammer",
        created_at=now - timedelta(hours=2),
    )
    _make_rec(session, user, ip_hash="other", created_at=now)

    recent = rec_repo.count_recent_recommendations_from_ip(
        session, "spammer", within=timedelta(minutes=10)
    )
    assert recent == 1
    all_time = rec_repo.count_recent_recommendations_from_ip(
        session, "spammer", within=timedelta(days=7)
    )
    assert all_time == 2


def test_get_voter_values_returns_signed_user_and_guest_overlap(
    session: Session,
    make_city: Callable[..., City],
    make_user: Callable[..., User],
) -> None:
    """Returns both user-keyed and session-keyed votes for the caller."""
    make_city()
    author = make_user()
    voter = make_user()
    rec_a = _make_rec(session, author, body="a")
    rec_b = _make_rec(session, author, body="b")
    rec_c = _make_rec(session, author, body="c")

    rec_repo.upsert_vote(
        session,
        recommendation_id=rec_a,
        user_id=voter.id,
        session_id=None,
        value=1,
    )
    rec_repo.upsert_vote(
        session,
        recommendation_id=rec_b,
        user_id=None,
        session_id="g1",
        value=-1,
    )
    # rec_c has no vote from either identity.

    result = rec_repo.get_voter_values_for_recommendations(
        session,
        [rec_a, rec_b, rec_c],
        user_id=voter.id,
        session_id="g1",
    )
    assert result == {rec_a: 1, rec_b: -1}


def test_get_voter_values_returns_empty_without_identity(
    session: Session,
    make_city: Callable[..., City],
    make_user: Callable[..., User],
) -> None:
    make_city()
    author = make_user()
    rec_id = _make_rec(session, author)
    result = rec_repo.get_voter_values_for_recommendations(
        session, [rec_id], user_id=None, session_id=None
    )
    assert result == {}

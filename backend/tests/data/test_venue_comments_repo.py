"""Repository tests for :mod:`backend.data.repositories.venue_comments`.

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
from backend.data.models.users import User
from backend.data.models.venue_comments import VenueCommentCategory
from backend.data.models.venues import Venue
from backend.data.repositories import venue_comments as comments_repo


def _make_comment(
    session: Session,
    venue: Venue,
    user: User | None,
    *,
    category: VenueCommentCategory = VenueCommentCategory.VIBES,
    body: str = "Great show",
    ip_hash: str | None = None,
    created_at: datetime | None = None,
) -> uuid.UUID:
    """Helper — create a comment and optionally backdate its created_at.

    Returns:
        The new comment's UUID.
    """
    comment = comments_repo.create_comment(
        session,
        venue_id=venue.id,
        user_id=user.id if user else None,
        category=category,
        body=body,
        ip_hash=ip_hash,
    )
    if created_at is not None:
        comment.created_at = created_at
        session.flush()
    return comment.id


def test_create_and_get_comment_roundtrips(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_user: Callable[..., User],
) -> None:
    city = make_city()
    venue = make_venue(city=city)
    user = make_user()

    comment = comments_repo.create_comment(
        session,
        venue_id=venue.id,
        user_id=user.id,
        category=VenueCommentCategory.TICKETS,
        body="Box office opens at 6",
        ip_hash="abc" * 10,
    )
    fetched = comments_repo.get_comment_by_id(session, comment.id)
    assert fetched is not None
    assert fetched.body == "Box office opens at 6"
    assert fetched.category == VenueCommentCategory.TICKETS


def test_get_comment_by_id_missing_returns_none(session: Session) -> None:
    assert comments_repo.get_comment_by_id(session, uuid.uuid4()) is None


def test_list_comments_filters_by_category(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_user: Callable[..., User],
) -> None:
    city = make_city()
    venue = make_venue(city=city)
    user = make_user()
    _make_comment(session, venue, user, category=VenueCommentCategory.VIBES)
    _make_comment(session, venue, user, category=VenueCommentCategory.TICKETS)

    rows = comments_repo.list_comments_by_venue(
        session, venue.id, category=VenueCommentCategory.TICKETS
    )
    assert len(rows) == 1
    assert rows[0][0].category == VenueCommentCategory.TICKETS


def test_list_comments_sort_new_is_reverse_chronological(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_user: Callable[..., User],
) -> None:
    city = make_city()
    venue = make_venue(city=city)
    user = make_user()
    now = datetime.now(UTC)
    _make_comment(
        session, venue, user, body="oldest", created_at=now - timedelta(days=3)
    )
    _make_comment(
        session, venue, user, body="middle", created_at=now - timedelta(days=1)
    )
    _make_comment(session, venue, user, body="newest", created_at=now)

    rows = comments_repo.list_comments_by_venue(session, venue.id, sort="new")
    assert [c.body for c, _likes, _dislikes in rows] == ["newest", "middle", "oldest"]


def test_list_comments_sort_top_uses_net_votes_plus_recency(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_user: Callable[..., User],
) -> None:
    """Top-sort prefers net likes, but a fresh comment with no votes can
    still tie an older comment with 1 net like thanks to the +1 recency
    boost.
    """
    city = make_city()
    venue = make_venue(city=city)
    author = make_user()
    voter = make_user()
    now = datetime.now(UTC)
    old_leader_id = _make_comment(
        session,
        venue,
        author,
        body="old leader",
        created_at=now - timedelta(days=10),
    )
    new_cold_id = _make_comment(session, venue, author, body="new no votes")

    # One upvote on the old leader.
    comments_repo.upsert_vote(
        session,
        comment_id=old_leader_id,
        user_id=voter.id,
        session_id=None,
        value=1,
    )

    rows = comments_repo.list_comments_by_venue(session, venue.id, sort="top")
    bodies = [c.body for c, _likes, _dislikes in rows]
    # Both have effective score 1 (old: 1 net, no boost; new: 0 net + 1 boost).
    # Tie broken by created_at DESC → new wins.
    assert bodies[0] == "new no votes"
    assert bodies[1] == "old leader"
    # Confirm we returned the right ids too.
    assert {c.id for c, _l, _d in rows} == {old_leader_id, new_cold_id}


def test_list_comments_limit_clamps_to_max_100(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_user: Callable[..., User],
) -> None:
    city = make_city()
    venue = make_venue(city=city)
    user = make_user()
    for i in range(3):
        _make_comment(session, venue, user, body=f"c{i}")
    rows = comments_repo.list_comments_by_venue(session, venue.id, limit=9999)
    assert len(rows) == 3


def test_update_and_delete_comment(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_user: Callable[..., User],
) -> None:
    city = make_city()
    venue = make_venue(city=city)
    user = make_user()
    comment = comments_repo.create_comment(
        session,
        venue_id=venue.id,
        user_id=user.id,
        category=VenueCommentCategory.OTHER,
        body="original",
        ip_hash=None,
    )

    comments_repo.update_comment_body(session, comment, body="edited")
    fetched = comments_repo.get_comment_by_id(session, comment.id)
    assert fetched is not None and fetched.body == "edited"

    comments_repo.delete_comment(session, comment)
    assert comments_repo.get_comment_by_id(session, comment.id) is None


def test_upsert_vote_inserts_then_updates_in_place(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_user: Callable[..., User],
) -> None:
    city = make_city()
    venue = make_venue(city=city)
    author = make_user()
    voter = make_user()
    comment_id = _make_comment(session, venue, author)

    first = comments_repo.upsert_vote(
        session,
        comment_id=comment_id,
        user_id=voter.id,
        session_id=None,
        value=1,
    )
    second = comments_repo.upsert_vote(
        session,
        comment_id=comment_id,
        user_id=voter.id,
        session_id=None,
        value=-1,
    )
    assert first.id == second.id
    assert second.value == -1

    likes, dislikes = comments_repo.count_votes_for_comment(session, comment_id)
    assert likes == 0 and dislikes == 1


def test_upsert_vote_separate_rows_per_voter(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_user: Callable[..., User],
) -> None:
    city = make_city()
    venue = make_venue(city=city)
    author = make_user()
    alice = make_user()
    bob = make_user()
    comment_id = _make_comment(session, venue, author)

    comments_repo.upsert_vote(
        session, comment_id=comment_id, user_id=alice.id, session_id=None, value=1
    )
    comments_repo.upsert_vote(
        session, comment_id=comment_id, user_id=bob.id, session_id=None, value=-1
    )
    likes, dislikes = comments_repo.count_votes_for_comment(session, comment_id)
    assert likes == 1 and dislikes == 1


def test_upsert_vote_guest_session_dedupe(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_user: Callable[..., User],
) -> None:
    """Two upvotes from the same guest session collapse to one row."""
    city = make_city()
    venue = make_venue(city=city)
    author = make_user()
    comment_id = _make_comment(session, venue, author)

    comments_repo.upsert_vote(
        session, comment_id=comment_id, user_id=None, session_id="guest-1", value=1
    )
    comments_repo.upsert_vote(
        session, comment_id=comment_id, user_id=None, session_id="guest-1", value=1
    )
    likes, dislikes = comments_repo.count_votes_for_comment(session, comment_id)
    assert (likes, dislikes) == (1, 0)


def test_upsert_vote_rejects_bad_value(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_user: Callable[..., User],
) -> None:
    city = make_city()
    venue = make_venue(city=city)
    author = make_user()
    voter = make_user()
    comment_id = _make_comment(session, venue, author)
    with pytest.raises(ValueError):
        comments_repo.upsert_vote(
            session,
            comment_id=comment_id,
            user_id=voter.id,
            session_id=None,
            value=5,
        )


def test_upsert_vote_rejects_both_user_and_session(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_user: Callable[..., User],
) -> None:
    city = make_city()
    venue = make_venue(city=city)
    author = make_user()
    voter = make_user()
    comment_id = _make_comment(session, venue, author)
    with pytest.raises(ValueError):
        comments_repo.upsert_vote(
            session,
            comment_id=comment_id,
            user_id=voter.id,
            session_id="guest-1",
            value=1,
        )


def test_upsert_vote_rejects_neither_user_nor_session(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_user: Callable[..., User],
) -> None:
    city = make_city()
    venue = make_venue(city=city)
    author = make_user()
    comment_id = _make_comment(session, venue, author)
    with pytest.raises(ValueError):
        comments_repo.upsert_vote(
            session,
            comment_id=comment_id,
            user_id=None,
            session_id=None,
            value=1,
        )


def test_clear_vote_removes_row_when_present(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_user: Callable[..., User],
) -> None:
    city = make_city()
    venue = make_venue(city=city)
    author = make_user()
    voter = make_user()
    comment_id = _make_comment(session, venue, author)
    comments_repo.upsert_vote(
        session, comment_id=comment_id, user_id=voter.id, session_id=None, value=1
    )
    assert (
        comments_repo.clear_vote(
            session, comment_id=comment_id, user_id=voter.id, session_id=None
        )
        is True
    )
    likes, dislikes = comments_repo.count_votes_for_comment(session, comment_id)
    assert (likes, dislikes) == (0, 0)


def test_clear_vote_noop_when_no_existing_vote(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_user: Callable[..., User],
) -> None:
    city = make_city()
    venue = make_venue(city=city)
    author = make_user()
    voter = make_user()
    comment_id = _make_comment(session, venue, author)
    assert (
        comments_repo.clear_vote(
            session, comment_id=comment_id, user_id=voter.id, session_id=None
        )
        is False
    )


def test_count_recent_comments_from_ip_respects_window(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_user: Callable[..., User],
) -> None:
    city = make_city()
    venue = make_venue(city=city)
    user = make_user()
    now = datetime.now(UTC)
    _make_comment(
        session,
        venue,
        user,
        ip_hash="spammer",
        created_at=now - timedelta(minutes=2),
    )
    _make_comment(
        session,
        venue,
        user,
        ip_hash="spammer",
        created_at=now - timedelta(hours=2),
    )
    _make_comment(session, venue, user, ip_hash="other", created_at=now)

    recent = comments_repo.count_recent_comments_from_ip(
        session, "spammer", within=timedelta(minutes=10)
    )
    assert recent == 1
    all_time = comments_repo.count_recent_comments_from_ip(
        session, "spammer", within=timedelta(days=7)
    )
    assert all_time == 2


def test_get_voter_values_returns_signed_user_and_guest_overlap(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_user: Callable[..., User],
) -> None:
    """Returns both user-keyed and session-keyed votes for the caller."""
    city = make_city()
    venue = make_venue(city=city)
    author = make_user()
    voter = make_user()
    comment_a = _make_comment(session, venue, author, body="a")
    comment_b = _make_comment(session, venue, author, body="b")
    comment_c = _make_comment(session, venue, author, body="c")
    comments_repo.upsert_vote(
        session, comment_id=comment_a, user_id=voter.id, session_id=None, value=1
    )
    comments_repo.upsert_vote(
        session, comment_id=comment_b, user_id=None, session_id="g1", value=-1
    )
    # c has no vote from either identity

    result = comments_repo.get_voter_values_for_comments(
        session,
        [comment_a, comment_b, comment_c],
        user_id=voter.id,
        session_id="g1",
    )
    assert result == {comment_a: 1, comment_b: -1}


def test_get_voter_values_returns_empty_without_identity(
    session: Session,
    make_city: Callable[..., City],
    make_venue: Callable[..., Venue],
    make_user: Callable[..., User],
) -> None:
    city = make_city()
    venue = make_venue(city=city)
    author = make_user()
    comment_id = _make_comment(session, venue, author)
    result = comments_repo.get_voter_values_for_comments(
        session, [comment_id], user_id=None, session_id=None
    )
    assert result == {}

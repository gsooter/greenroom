"""Tests for :mod:`backend.services.artist_hydration`.

Lives under ``tests/data`` because the service is heavily SQL-bound —
depth lineage, daily-cap counts from the audit log, atomic adds on
transaction failure. The shared ``session`` fixture from
:mod:`backend.tests.data.conftest` runs each test inside a rolled-back
transaction.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

from backend.data.models.artist_similarity import ArtistSimilarity
from backend.data.models.artists import Artist
from backend.data.models.hydration_log import HydrationLog
from backend.services import artist_hydration
from backend.services.artist_hydration import (
    DAILY_HYDRATION_CAP,
    MAX_ARTISTS_PER_HYDRATION,
    MAX_HYDRATION_DEPTH,
    execute_hydration,
    get_daily_hydration_count,
    preview_hydration,
)

# ---------------------------------------------------------------------------
# Test fixtures local to this module
# ---------------------------------------------------------------------------


def _make_artist(
    session: Session,
    *,
    name: str,
    hydration_depth: int = 0,
    hydration_source: str | None = None,
    hydrated_from_artist_id: uuid.UUID | None = None,
) -> Artist:
    """Insert and return a minimal :class:`Artist` row.

    Args:
        session: Active SQLAlchemy session.
        name: Display name for the artist.
        hydration_depth: Lineage depth to seed.
        hydration_source: Optional lineage source tag.
        hydrated_from_artist_id: Optional parent artist UUID.

    Returns:
        The created :class:`Artist` row.
    """
    artist = Artist(
        name=name,
        normalized_name=name.lower().strip(),
        genres=[],
        hydration_depth=hydration_depth,
        hydration_source=hydration_source,
        hydrated_from_artist_id=hydrated_from_artist_id,
    )
    session.add(artist)
    session.flush()
    return artist


def _make_similarity(
    session: Session,
    *,
    source_artist: Artist,
    similar_name: str,
    score: float,
    similar_mbid: str | None = None,
) -> ArtistSimilarity:
    """Insert and return an :class:`ArtistSimilarity` edge.

    Args:
        session: Active SQLAlchemy session.
        source_artist: The source-side artist row.
        similar_name: Display name of the similar artist as Last.fm
            returned it.
        score: Similarity score in 0.0-1.0.
        similar_mbid: Optional MusicBrainz id for the similar artist.

    Returns:
        The created :class:`ArtistSimilarity` row.
    """
    edge = ArtistSimilarity(
        source_artist_id=source_artist.id,
        similar_artist_name=similar_name,
        similar_artist_mbid=similar_mbid,
        similarity_score=Decimal(f"{score:.3f}"),
        source="lastfm",
    )
    session.add(edge)
    session.flush()
    return edge


@pytest.fixture
def stub_enrichment_queue(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Stub Celery ``send_task`` so tests don't need a broker.

    Returns:
        The :class:`MagicMock` standing in for ``celery_app.send_task``.
    """
    sent = MagicMock()
    monkeypatch.setattr(artist_hydration, "_send_enrichment_task", sent)
    return sent


@pytest.fixture(autouse=True)
def _commit_session_writes(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Make ``Session.commit`` flush instead of ending the test transaction.

    The data-layer conftest wraps each test in an outer transaction that
    is rolled back on teardown. The hydration service issues a real
    ``commit`` to make additions atomic in production; in tests we want
    those to behave as a flush so the outer rollback still cleans up.

    Yields:
        None.
    """
    original = Session.commit

    def _flush_only(self: Session) -> None:
        self.flush()

    monkeypatch.setattr(Session, "commit", _flush_only)
    yield
    monkeypatch.setattr(Session, "commit", original)


# ---------------------------------------------------------------------------
# preview_hydration
# ---------------------------------------------------------------------------


def test_preview_returns_eligible_candidates_above_threshold(
    session: Session,
) -> None:
    source = _make_artist(session, name="Caamp")
    _make_similarity(
        session,
        source_artist=source,
        similar_name="The Head and the Heart",
        score=0.91,
    )
    _make_similarity(session, source_artist=source, similar_name="Mt. Joy", score=0.88)

    preview = preview_hydration(session, source.id)

    assert preview.source_artist.id == source.id
    eligible = [c for c in preview.candidates if c.status == "eligible"]
    assert {c.similar_artist_name for c in eligible} == {
        "The Head and the Heart",
        "Mt. Joy",
    }
    assert preview.eligible_count == 2
    assert preview.can_proceed is True
    assert preview.blocking_reason is None


def test_preview_filters_below_minimum_similarity_score(session: Session) -> None:
    source = _make_artist(session, name="Caamp")
    _make_similarity(
        session, source_artist=source, similar_name="Strong Match", score=0.81
    )
    _make_similarity(
        session, source_artist=source, similar_name="Weak Match", score=0.30
    )

    preview = preview_hydration(session, source.id)

    by_name = {c.similar_artist_name: c for c in preview.candidates}
    assert by_name["Strong Match"].status == "eligible"
    assert by_name["Weak Match"].status == "below_threshold"
    assert preview.eligible_count == 1


def test_preview_marks_already_existing_artists(session: Session) -> None:
    source = _make_artist(session, name="Caamp")
    existing = _make_artist(session, name="Mt. Joy")
    _make_similarity(session, source_artist=source, similar_name="Mt. Joy", score=0.88)

    preview = preview_hydration(session, source.id)

    candidate = next(
        c for c in preview.candidates if c.similar_artist_name == "Mt. Joy"
    )
    assert candidate.status == "already_exists"
    assert candidate.existing_artist_id == existing.id
    assert preview.eligible_count == 0


def test_preview_blocks_at_max_hydration_depth(session: Session) -> None:
    source = _make_artist(session, name="Caamp", hydration_depth=MAX_HYDRATION_DEPTH)
    _make_similarity(session, source_artist=source, similar_name="Anything", score=0.9)

    preview = preview_hydration(session, source.id)

    assert preview.can_proceed is False
    assert preview.blocking_reason is not None
    assert "depth" in preview.blocking_reason.lower()
    assert preview.would_add_count == 0


def test_preview_reports_remaining_daily_cap(
    session: Session,
) -> None:
    source = _make_artist(session, name="Caamp")
    for i in range(3):
        _make_similarity(
            session,
            source_artist=source,
            similar_name=f"Sim {i}",
            score=0.9,
        )
    # Pre-load the audit table so the cap math has something to subtract.
    session.add(
        HydrationLog(
            source_artist_id=source.id,
            admin_email="ops@greenroom.test",
            candidate_artists=[],
            added_artist_ids=[uuid.uuid4() for _ in range(2)],
        )
    )
    session.flush()

    preview = preview_hydration(session, source.id)

    assert preview.daily_cap_remaining == DAILY_HYDRATION_CAP - 2
    assert preview.would_add_count == 3


def test_preview_clamps_would_add_count_to_max_per_hydration(
    session: Session,
) -> None:
    source = _make_artist(session, name="Caamp")
    for i in range(MAX_ARTISTS_PER_HYDRATION + 5):
        _make_similarity(
            session,
            source_artist=source,
            similar_name=f"Sim {i}",
            score=0.9,
        )

    preview = preview_hydration(session, source.id)

    assert preview.eligible_count == MAX_ARTISTS_PER_HYDRATION + 5
    assert preview.would_add_count == MAX_ARTISTS_PER_HYDRATION


def test_preview_can_proceed_false_when_daily_cap_zero(
    session: Session,
) -> None:
    source = _make_artist(session, name="Caamp")
    _make_similarity(session, source_artist=source, similar_name="Sim", score=0.9)
    session.add(
        HydrationLog(
            source_artist_id=source.id,
            admin_email="ops@greenroom.test",
            candidate_artists=[],
            added_artist_ids=[uuid.uuid4() for _ in range(DAILY_HYDRATION_CAP)],
        )
    )
    session.flush()

    preview = preview_hydration(session, source.id)

    assert preview.daily_cap_remaining == 0
    assert preview.can_proceed is False
    assert preview.blocking_reason is not None
    assert "cap" in preview.blocking_reason.lower()


def test_preview_returns_none_for_missing_source(session: Session) -> None:
    fake_id = uuid.uuid4()
    preview = preview_hydration(session, fake_id)
    assert preview is None


# ---------------------------------------------------------------------------
# execute_hydration
# ---------------------------------------------------------------------------


def test_execute_creates_hydrated_artists_with_metadata(
    session: Session,
    stub_enrichment_queue: MagicMock,
) -> None:
    source = _make_artist(session, name="Caamp")
    _make_similarity(
        session,
        source_artist=source,
        similar_name="The Head and the Heart",
        score=0.91,
    )
    _make_similarity(session, source_artist=source, similar_name="Mt. Joy", score=0.88)

    result = execute_hydration(
        session,
        source.id,
        admin_email="ops@greenroom.test",
        confirmed_candidates=["The Head and the Heart", "Mt. Joy"],
    )

    assert result.added_count == 2
    by_name = {a.name: a for a in result.added_artists}
    head = by_name["The Head and the Heart"]
    assert head.hydration_source == "similar_artist"
    assert head.hydrated_from_artist_id == source.id
    assert head.hydration_depth == 1
    assert head.hydrated_at is not None


def test_execute_increments_depth_from_parent(
    session: Session,
    stub_enrichment_queue: MagicMock,
) -> None:
    source = _make_artist(session, name="Caamp", hydration_depth=1)
    _make_similarity(
        session, source_artist=source, similar_name="Generation 2", score=0.9
    )

    result = execute_hydration(
        session,
        source.id,
        admin_email="ops@greenroom.test",
        confirmed_candidates=["Generation 2"],
    )

    assert result.added_artists[0].hydration_depth == 2


def test_execute_skips_already_existing_artists(
    session: Session,
    stub_enrichment_queue: MagicMock,
) -> None:
    source = _make_artist(session, name="Caamp")
    _make_artist(session, name="Mt. Joy")
    _make_similarity(session, source_artist=source, similar_name="Mt. Joy", score=0.88)

    result = execute_hydration(
        session,
        source.id,
        admin_email="ops@greenroom.test",
        confirmed_candidates=["Mt. Joy"],
    )

    assert result.added_count == 0
    assert result.skipped_count == 1


def test_execute_respects_daily_cap_mid_execution(
    session: Session,
    stub_enrichment_queue: MagicMock,
) -> None:
    source = _make_artist(session, name="Caamp")
    for i in range(MAX_ARTISTS_PER_HYDRATION):
        _make_similarity(
            session,
            source_artist=source,
            similar_name=f"Sim {i}",
            score=0.9,
        )
    # Already at cap minus 2 — only 2 of the 5 should land.
    session.add(
        HydrationLog(
            source_artist_id=source.id,
            admin_email="ops@greenroom.test",
            candidate_artists=[],
            added_artist_ids=[uuid.uuid4() for _ in range(DAILY_HYDRATION_CAP - 2)],
        )
    )
    session.flush()

    result = execute_hydration(
        session,
        source.id,
        admin_email="ops@greenroom.test",
        confirmed_candidates=[f"Sim {i}" for i in range(MAX_ARTISTS_PER_HYDRATION)],
    )

    assert result.added_count == 2
    assert result.daily_cap_hit is True


def test_execute_blocks_when_source_at_max_depth(
    session: Session,
    stub_enrichment_queue: MagicMock,
) -> None:
    source = _make_artist(session, name="Caamp", hydration_depth=MAX_HYDRATION_DEPTH)
    _make_similarity(session, source_artist=source, similar_name="Sim", score=0.9)

    result = execute_hydration(
        session,
        source.id,
        admin_email="ops@greenroom.test",
        confirmed_candidates=["Sim"],
    )

    assert result.added_count == 0
    assert result.blocking_reason is not None
    assert "depth" in result.blocking_reason.lower()


def test_execute_filters_below_threshold_candidates(
    session: Session,
    stub_enrichment_queue: MagicMock,
) -> None:
    source = _make_artist(session, name="Caamp")
    _make_similarity(session, source_artist=source, similar_name="Weak", score=0.10)

    result = execute_hydration(
        session,
        source.id,
        admin_email="ops@greenroom.test",
        confirmed_candidates=["Weak"],
    )

    assert result.added_count == 0
    assert result.filtered_count == 1


def test_execute_writes_audit_log_entry(
    session: Session,
    stub_enrichment_queue: MagicMock,
) -> None:
    source = _make_artist(session, name="Caamp")
    _make_similarity(session, source_artist=source, similar_name="Sim One", score=0.9)

    execute_hydration(
        session,
        source.id,
        admin_email="ops@greenroom.test",
        confirmed_candidates=["Sim One"],
    )

    log = session.query(HydrationLog).one()
    assert log.source_artist_id == source.id
    assert log.admin_email == "ops@greenroom.test"
    assert len(log.added_artist_ids) == 1


def test_execute_only_adds_confirmed_candidates(
    session: Session,
    stub_enrichment_queue: MagicMock,
) -> None:
    """Operator can deselect candidates in the modal — execute respects that."""
    source = _make_artist(session, name="Caamp")
    _make_similarity(session, source_artist=source, similar_name="Yes", score=0.9)
    _make_similarity(session, source_artist=source, similar_name="No", score=0.9)

    result = execute_hydration(
        session,
        source.id,
        admin_email="ops@greenroom.test",
        confirmed_candidates=["Yes"],
    )

    assert result.added_count == 1
    assert result.added_artists[0].name == "Yes"


def test_execute_queues_enrichment_tasks(
    session: Session,
    stub_enrichment_queue: MagicMock,
) -> None:
    source = _make_artist(session, name="Caamp")
    _make_similarity(session, source_artist=source, similar_name="Sim", score=0.9)

    result = execute_hydration(
        session,
        source.id,
        admin_email="ops@greenroom.test",
        confirmed_candidates=["Sim"],
    )

    assert result.added_count == 1
    artist_id = result.added_artists[0].id
    # Each new artist gets MusicBrainz, Last.fm, Last.fm-similar, and Spotify
    # enrichment tasks queued. Check at least the artist UUID was passed
    # through to the queue at least once.
    called_ids = [str(call.args[1]) for call in stub_enrichment_queue.call_args_list]
    assert str(artist_id) in called_ids


def test_execute_returns_blocking_reason_when_source_missing(
    session: Session,
    stub_enrichment_queue: MagicMock,
) -> None:
    fake = uuid.uuid4()
    result = execute_hydration(
        session,
        fake,
        admin_email="ops@greenroom.test",
        confirmed_candidates=["Anything"],
    )
    assert result.added_count == 0
    assert result.blocking_reason is not None


# ---------------------------------------------------------------------------
# get_daily_hydration_count
# ---------------------------------------------------------------------------


def test_daily_count_sums_added_artist_ids_in_last_24h(session: Session) -> None:
    source = _make_artist(session, name="Caamp")
    session.add(
        HydrationLog(
            source_artist_id=source.id,
            admin_email="ops@greenroom.test",
            candidate_artists=[],
            added_artist_ids=[uuid.uuid4() for _ in range(3)],
        )
    )
    session.add(
        HydrationLog(
            source_artist_id=source.id,
            admin_email="ops@greenroom.test",
            candidate_artists=[],
            added_artist_ids=[uuid.uuid4() for _ in range(4)],
        )
    )
    session.flush()

    assert get_daily_hydration_count(session) == 7


def test_daily_count_ignores_logs_older_than_24h(session: Session) -> None:
    source = _make_artist(session, name="Caamp")
    log = HydrationLog(
        source_artist_id=source.id,
        admin_email="ops@greenroom.test",
        candidate_artists=[],
        added_artist_ids=[uuid.uuid4() for _ in range(5)],
    )
    session.add(log)
    session.flush()
    # Bump the audit timestamp two days back.
    log.created_at = datetime.now(UTC) - timedelta(days=2)
    session.flush()

    assert get_daily_hydration_count(session) == 0


def test_daily_count_zero_when_no_logs(session: Session) -> None:
    assert get_daily_hydration_count(session) == 0

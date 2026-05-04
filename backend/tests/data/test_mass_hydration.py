"""Tests for :func:`backend.services.artist_hydration.mass_hydrate`.

Lives under ``tests/data`` because the bulk path exercises the same
audit-log + similarity tables the per-artist tests do. Each test runs
inside the rolled-back transaction provided by the data-layer conftest.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
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
    mass_hydrate,
)


def _make_artist(session: Session, *, name: str) -> Artist:
    """Insert and return a minimal :class:`Artist` row."""
    artist = Artist(
        name=name,
        normalized_name=name.lower().strip(),
        genres=[],
        hydration_depth=0,
    )
    session.add(artist)
    session.flush()
    return artist


def _seed_unresolved_similarities(
    session: Session, source: Artist, count: int, *, score: float = 0.9
) -> None:
    """Create ``count`` unresolved (and therefore eligible) similarity edges.

    Args:
        session: Active SQLAlchemy session.
        source: The source artist whose edges to populate.
        count: Number of unresolved similar artists to create.
        score: Similarity score to apply to each edge.
    """
    for i in range(count):
        session.add(
            ArtistSimilarity(
                source_artist_id=source.id,
                similar_artist_name=f"{source.name} Sim {i}",
                similar_artist_mbid=None,
                similar_artist_id=None,
                similarity_score=Decimal(f"{score:.3f}"),
                source="lastfm",
            )
        )
    session.flush()


@pytest.fixture
def stub_enrichment_queue(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Stub Celery ``send_task`` so tests don't need a broker."""
    sent = MagicMock()
    monkeypatch.setattr(artist_hydration, "_send_enrichment_task", sent)
    return sent


@pytest.fixture(autouse=True)
def _commit_session_writes(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Make ``Session.commit`` flush only so the outer rollback still cleans up."""
    original = Session.commit

    def _flush_only(self: Session) -> None:
        self.flush()

    monkeypatch.setattr(Session, "commit", _flush_only)
    yield
    monkeypatch.setattr(Session, "commit", original)


# ---------------------------------------------------------------------------
# Behavior
# ---------------------------------------------------------------------------


def test_mass_hydrate_iterates_best_candidates(
    session: Session, stub_enrichment_queue: MagicMock
) -> None:
    a = _make_artist(session, name="Caamp")
    b = _make_artist(session, name="Phoebe Bridgers")
    _seed_unresolved_similarities(session, a, 4)
    _seed_unresolved_similarities(session, b, 3)

    result = mass_hydrate(session, admin_email="scheduler@greenroom.local")

    assert result.sources_processed >= 1
    # 4 + 3 are both well under the per-call cap of 5, so both seeds
    # should land their full set.
    assert result.artists_added == 7
    assert result.daily_cap_reached is False
    audit_rows = session.query(HydrationLog).count()
    assert audit_rows >= 2


def test_mass_hydrate_stops_when_daily_cap_reached(
    session: Session, stub_enrichment_queue: MagicMock
) -> None:
    seed = _make_artist(session, name="Caamp")
    _seed_unresolved_similarities(session, seed, MAX_ARTISTS_PER_HYDRATION)

    # Pre-load the audit log so only 3 of the cap remains.
    session.add(
        HydrationLog(
            source_artist_id=seed.id,
            admin_email="ops@greenroom.test",
            candidate_artists=[],
            added_artist_ids=[uuid.uuid4() for _ in range(DAILY_HYDRATION_CAP - 3)],
        )
    )
    session.flush()

    result = mass_hydrate(session, admin_email="scheduler@greenroom.local")

    assert result.artists_added == 3
    assert result.daily_cap_reached is True


def test_mass_hydrate_records_per_source_summary(
    session: Session, stub_enrichment_queue: MagicMock
) -> None:
    a = _make_artist(session, name="Caamp")
    _seed_unresolved_similarities(session, a, 2)

    result = mass_hydrate(session, admin_email="scheduler@greenroom.local")

    assert len(result.per_source) == 1
    row = result.per_source[0]
    assert row["artist_name"] == "Caamp"
    assert row["added_count"] == 2


def test_mass_hydrate_no_op_when_no_candidates_exist(
    session: Session, stub_enrichment_queue: MagicMock
) -> None:
    result = mass_hydrate(session, admin_email="scheduler@greenroom.local")

    assert result.sources_processed == 0
    assert result.artists_added == 0
    assert result.daily_cap_reached is False


def test_mass_hydrate_skips_sources_at_max_depth(
    session: Session, stub_enrichment_queue: MagicMock
) -> None:
    deep = _make_artist(session, name="Deep One")
    deep.hydration_depth = artist_hydration.MAX_HYDRATION_DEPTH
    _seed_unresolved_similarities(session, deep, 3)
    session.flush()

    result = mass_hydrate(session, admin_email="scheduler@greenroom.local")

    assert result.artists_added == 0
    assert result.sources_skipped >= 1


def test_mass_hydrate_uses_provided_admin_email(
    session: Session, stub_enrichment_queue: MagicMock
) -> None:
    a = _make_artist(session, name="Caamp")
    _seed_unresolved_similarities(session, a, 1)

    mass_hydrate(session, admin_email="scheduler@greenroom.local")

    log = session.query(HydrationLog).first()
    assert log is not None
    assert log.admin_email == "scheduler@greenroom.local"


def test_mass_hydrate_respects_max_sources_cap(
    session: Session, stub_enrichment_queue: MagicMock
) -> None:
    # Ten seeds, all eligible. Tell mass_hydrate to only process 2.
    for i in range(10):
        seed = _make_artist(session, name=f"Seed {i}")
        _seed_unresolved_similarities(session, seed, 1)

    result = mass_hydrate(
        session,
        admin_email="scheduler@greenroom.local",
        max_sources=2,
    )

    assert result.sources_processed == 2

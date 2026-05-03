"""Shared fixtures for repository-layer tests.

These tests hit a real PostgreSQL database (``greenroom_test``) so
repository logic is exercised end-to-end against Postgres semantics
(GIN overlap, JSONB path lookup, enum handling). Unit tests that do
not need a database live elsewhere in the suite and use MagicMock
sessions.

The per-test isolation strategy is the SQLAlchemy "join an external
transaction" pattern: each test gets a fresh connection with a
transaction already begun, and on teardown that transaction is rolled
back. Every row created inside a test disappears cleanly — no TRUNCATE
churn and no leakage between tests.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from backend.data.models.cities import City
from backend.data.models.events import Event, EventStatus, EventType
from backend.data.models.region import Region
from backend.data.models.users import User
from backend.data.models.venues import Venue

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://greenroom:greenroom@localhost:5432/greenroom_test",
)


@pytest.fixture(scope="session")
def _engine() -> Iterator[Engine]:
    """Session-scoped engine bound to the test database.

    Yields:
        A SQLAlchemy Engine for the test database.
    """
    engine = create_engine(TEST_DATABASE_URL)
    # Fail fast if the database is unreachable.
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    yield engine
    engine.dispose()


@pytest.fixture
def session(_engine: Engine) -> Iterator[Session]:
    """Per-test session wrapped in a transaction that is rolled back.

    Yields:
        A SQLAlchemy Session; all writes are reverted on teardown.
    """
    connection = _engine.connect()
    transaction = connection.begin()
    sess = Session(bind=connection, expire_on_commit=False)
    try:
        yield sess
    finally:
        sess.close()
        transaction.rollback()
        connection.close()


# ---------------------------------------------------------------------------
# Model factories — small, opinionated helpers for repo tests.
# ---------------------------------------------------------------------------


@pytest.fixture
def make_region(session: Session) -> Callable[..., Region]:
    """Factory for Region rows.

    Tests that need a fresh region (for cross-region overlay scenarios)
    take this fixture; the default city factory upserts the ``dmv``
    region transparently so most tests never touch this directly.

    Returns:
        Callable that creates and flushes a Region with sensible defaults.
    """

    def _make(
        *,
        slug: str | None = None,
        name: str | None = None,
        display_name: str | None = None,
        description: str | None = None,
    ) -> Region:
        resolved_slug = slug or f"region-{uuid.uuid4().hex[:8]}"
        region = Region(
            slug=resolved_slug,
            name=name or f"Region {resolved_slug}",
            display_name=display_name or resolved_slug.upper(),
            description=description,
        )
        session.add(region)
        session.flush()
        return region

    return _make


@pytest.fixture
def make_city(
    session: Session, make_region: Callable[..., Region]
) -> Callable[..., City]:
    """Factory for City rows.

    Cities now belong to a region (Decision 061). The factory upserts
    a ``dmv`` region row when the caller doesn't pass an explicit
    ``region_obj`` so the legacy test surface (``make_city(region="DMV")``)
    keeps working without each test having to manage the region.

    Returns:
        Callable that creates and flushes a City with sensible defaults.
    """

    def _make(
        *,
        name: str = "Washington",
        slug: str | None = None,
        state: str = "DC",
        region: str = "DMV",
        region_obj: Region | None = None,
        is_active: bool = True,
    ) -> City:
        if region_obj is None:
            existing = session.execute(
                select(Region).where(Region.slug == "dmv")
            ).scalar_one_or_none()
            region_obj = existing or make_region(
                slug="dmv",
                name="DC, Maryland & Virginia",
                display_name="DMV",
            )
        city = City(
            name=name,
            slug=slug or f"city-{uuid.uuid4().hex[:8]}",
            state=state,
            region=region,
            region_id=region_obj.id,
            timezone="America/New_York",
            is_active=is_active,
        )
        session.add(city)
        session.flush()
        return city

    return _make


@pytest.fixture
def make_venue(session: Session) -> Callable[..., Venue]:
    """Factory for Venue rows.

    Returns:
        Callable that creates and flushes a Venue with sensible defaults.
    """

    def _make(
        *,
        city: City,
        name: str = "Test Venue",
        slug: str | None = None,
        external_ids: dict[str, Any] | None = None,
        is_active: bool = True,
    ) -> Venue:
        venue = Venue(
            city_id=city.id,
            name=name,
            slug=slug or f"venue-{uuid.uuid4().hex[:8]}",
            external_ids=external_ids or {},
            is_active=is_active,
        )
        session.add(venue)
        session.flush()
        return venue

    return _make


@pytest.fixture
def make_event(session: Session) -> Callable[..., Event]:
    """Factory for Event rows.

    Returns:
        Callable that creates and flushes an Event with sensible defaults.
    """

    def _make(
        *,
        venue: Venue,
        title: str = "Test Show",
        slug: str | None = None,
        starts_at: datetime | None = None,
        artists: list[str] | None = None,
        genres: list[str] | None = None,
        spotify_artist_ids: list[str] | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
        event_type: EventType = EventType.CONCERT,
        status: EventStatus = EventStatus.CONFIRMED,
        external_id: str | None = None,
        source_platform: str | None = None,
    ) -> Event:
        event = Event(
            venue_id=venue.id,
            title=title,
            slug=slug or f"event-{uuid.uuid4().hex[:8]}",
            starts_at=starts_at or datetime.now(UTC) + timedelta(days=7),
            artists=artists if artists is not None else [],
            genres=genres,
            spotify_artist_ids=spotify_artist_ids,
            min_price=min_price,
            max_price=max_price,
            event_type=event_type,
            status=status,
            external_id=external_id,
            source_platform=source_platform,
        )
        session.add(event)
        session.flush()
        return event

    return _make


@pytest.fixture
def make_user(session: Session) -> Callable[..., User]:
    """Factory for User rows.

    Returns:
        Callable that creates and flushes a User with sensible defaults.
    """

    def _make(
        *,
        email: str | None = None,
        display_name: str = "Tester",
    ) -> User:
        user = User(
            email=email or f"{uuid.uuid4().hex[:8]}@example.test",
            display_name=display_name,
            is_active=True,
        )
        session.add(user)
        session.flush()
        return user

    return _make

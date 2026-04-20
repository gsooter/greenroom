"""Unit tests for :mod:`backend.services.users`.

The validator helpers (``_validated_updates`` and its coercers) are
pure apart from a city-existence check that hits the DB. Tests stub
the city repository with a tiny in-memory fake so the validation
surface is covered without a Postgres connection.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.core.exceptions import NotFoundError, ValidationError
from backend.data.models.users import DigestFrequency, OAuthProvider
from backend.services import users as users_service

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class _FakeUser:
    """Stand-in for :class:`backend.data.models.users.User`.

    Attributes covered match the ones touched by ``serialize_user`` so
    we can exercise the serializer without SQLAlchemy machinery.
    """

    id: uuid.UUID
    email: str
    display_name: str | None
    avatar_url: str | None
    city_id: uuid.UUID | None
    digest_frequency: DigestFrequency
    genre_preferences: list[str] | None
    notification_settings: dict[str, Any] | None
    last_login_at: object | None
    created_at: object


@pytest.fixture
def fake_session() -> MagicMock:
    """Return a MagicMock that mimics a SQLAlchemy session.

    Returns:
        A MagicMock session — no DB connection is opened.
    """
    return MagicMock(name="Session")


@pytest.fixture
def existing_city_id(monkeypatch: pytest.MonkeyPatch) -> Iterator[uuid.UUID]:
    """Patch the city repository to resolve a specific UUID as existing.

    Args:
        monkeypatch: pytest's monkeypatch fixture.

    Yields:
        The UUID that the patched repo will treat as "found".
    """
    city_id = uuid.uuid4()

    def fake_lookup(session: object, cid: uuid.UUID) -> object | None:
        return object() if cid == city_id else None

    monkeypatch.setattr(
        "backend.services.users.cities_repo.get_city_by_id", fake_lookup
    )
    yield city_id


# ---------------------------------------------------------------------------
# _validated_updates — field-by-field coercion
# ---------------------------------------------------------------------------


def test_validated_updates_drops_unknown_fields(fake_session: MagicMock) -> None:
    """Fields outside the allowlist are silently dropped (no error)."""
    updates = users_service._validated_updates(
        fake_session,
        {"email": "attacker@evil.com", "is_active": False},
    )
    assert updates == {}


def test_validated_updates_accepts_display_name_string(
    fake_session: MagicMock,
) -> None:
    """A string ``display_name`` passes through unchanged."""
    updates = users_service._validated_updates(
        fake_session, {"display_name": "Greenroom User"}
    )
    assert updates == {"display_name": "Greenroom User"}


def test_validated_updates_allows_display_name_null(
    fake_session: MagicMock,
) -> None:
    """Explicit null ``display_name`` is allowed (clears the field)."""
    updates = users_service._validated_updates(fake_session, {"display_name": None})
    assert updates == {"display_name": None}


def test_validated_updates_rejects_non_string_display_name(
    fake_session: MagicMock,
) -> None:
    """Non-string ``display_name`` is a ValidationError."""
    with pytest.raises(ValidationError):
        users_service._validated_updates(fake_session, {"display_name": 42})


def test_validated_updates_accepts_existing_city(
    fake_session: MagicMock, existing_city_id: uuid.UUID
) -> None:
    """A ``city_id`` that resolves via the repo is accepted."""
    updates = users_service._validated_updates(
        fake_session, {"city_id": str(existing_city_id)}
    )
    assert updates == {"city_id": existing_city_id}


def test_validated_updates_rejects_unknown_city(
    fake_session: MagicMock, existing_city_id: uuid.UUID
) -> None:
    """A well-formed UUID that doesn't resolve is a NotFoundError."""
    del existing_city_id  # fixture only patches the repo
    with pytest.raises(NotFoundError):
        users_service._validated_updates(fake_session, {"city_id": str(uuid.uuid4())})


def test_validated_updates_rejects_malformed_city(
    fake_session: MagicMock,
) -> None:
    """A non-UUID ``city_id`` value is a ValidationError."""
    with pytest.raises(ValidationError):
        users_service._validated_updates(fake_session, {"city_id": "not-a-uuid"})


def test_validated_updates_accepts_null_city(fake_session: MagicMock) -> None:
    """``city_id`` may be cleared by passing null."""
    updates = users_service._validated_updates(fake_session, {"city_id": None})
    assert updates == {"city_id": None}


def test_validated_updates_coerces_digest_frequency(
    fake_session: MagicMock,
) -> None:
    """Valid digest frequency strings coerce to the enum type."""
    updates = users_service._validated_updates(
        fake_session, {"digest_frequency": "weekly"}
    )
    assert updates["digest_frequency"] is DigestFrequency.WEEKLY


def test_validated_updates_rejects_bad_digest_frequency(
    fake_session: MagicMock,
) -> None:
    """Unknown digest frequency values are rejected."""
    with pytest.raises(ValidationError):
        users_service._validated_updates(fake_session, {"digest_frequency": "hourly"})


def test_validated_updates_cleans_genre_list(fake_session: MagicMock) -> None:
    """Genre entries are trimmed and empty strings dropped."""
    updates = users_service._validated_updates(
        fake_session, {"genre_preferences": ["  rock", "", "indie "]}
    )
    assert updates == {"genre_preferences": ["rock", "indie"]}


def test_validated_updates_rejects_non_string_genre(
    fake_session: MagicMock,
) -> None:
    """A non-string entry in ``genre_preferences`` is a ValidationError."""
    with pytest.raises(ValidationError):
        users_service._validated_updates(
            fake_session, {"genre_preferences": ["rock", 7]}
        )


def test_validated_updates_rejects_genre_not_a_list(
    fake_session: MagicMock,
) -> None:
    """``genre_preferences`` must be a list (or null)."""
    with pytest.raises(ValidationError):
        users_service._validated_updates(fake_session, {"genre_preferences": "rock"})


def test_validated_updates_rejects_bad_notification_settings(
    fake_session: MagicMock,
) -> None:
    """``notification_settings`` must be an object or null."""
    with pytest.raises(ValidationError):
        users_service._validated_updates(
            fake_session, {"notification_settings": "email"}
        )


# ---------------------------------------------------------------------------
# serialize_user
# ---------------------------------------------------------------------------


def test_serialize_user_renders_uuids_and_enums_as_strings() -> None:
    """The serializer produces a JSON-safe payload."""
    from datetime import datetime

    user_id = uuid.uuid4()
    now = datetime(2026, 4, 17, 12, 0, 0)
    user = _FakeUser(
        id=user_id,
        email="user@example.com",
        display_name="Example",
        avatar_url=None,
        city_id=None,
        digest_frequency=DigestFrequency.DAILY,
        genre_preferences=["indie"],
        notification_settings={"email": True},
        last_login_at=now,
        created_at=now,
    )

    payload = users_service.serialize_user(user)  # type: ignore[arg-type]

    assert payload["id"] == str(user_id)
    assert payload["digest_frequency"] == "daily"
    assert payload["genre_preferences"] == ["indie"]
    assert payload["notification_settings"] == {"email": True}
    assert payload["city_id"] is None


# ---------------------------------------------------------------------------
# list_music_connections
# ---------------------------------------------------------------------------


@dataclass
class _FakeMusicConnection:
    """Minimal stand-in for :class:`MusicServiceConnection`."""

    provider: OAuthProvider


@dataclass
class _FakeMusicUser:
    """User stand-in exposing only fields ``list_music_connections`` reads."""

    music_connections: list[_FakeMusicConnection]
    spotify_top_artists: list[dict[str, Any]] | None
    spotify_synced_at: object | None
    tidal_top_artists: list[dict[str, Any]] | None
    tidal_synced_at: object | None
    apple_top_artists: list[dict[str, Any]] | None
    apple_synced_at: object | None


def test_list_music_connections_marks_only_linked_providers_connected() -> None:
    """Presence of a MusicServiceConnection row drives the connected flag."""
    user = _FakeMusicUser(
        music_connections=[_FakeMusicConnection(provider=OAuthProvider.SPOTIFY)],
        spotify_top_artists=None,
        spotify_synced_at=None,
        tidal_top_artists=None,
        tidal_synced_at=None,
        apple_top_artists=None,
        apple_synced_at=None,
    )
    result = users_service.list_music_connections(user)  # type: ignore[arg-type]
    providers = {row["provider"]: row["connected"] for row in result}
    assert providers == {"spotify": True, "tidal": False, "apple_music": False}


def test_list_music_connections_reports_sync_snapshot_and_count() -> None:
    """Connected-with-data provider reports count + ISO synced_at."""
    from datetime import UTC, datetime

    synced = datetime(2026, 4, 20, 3, 16, tzinfo=UTC)
    user = _FakeMusicUser(
        music_connections=[
            _FakeMusicConnection(provider=OAuthProvider.APPLE_MUSIC),
        ],
        spotify_top_artists=None,
        spotify_synced_at=None,
        tidal_top_artists=None,
        tidal_synced_at=None,
        apple_top_artists=[{"id": "a1"}, {"id": "a2"}],
        apple_synced_at=synced,
    )
    result = users_service.list_music_connections(user)  # type: ignore[arg-type]
    apple = next(r for r in result if r["provider"] == "apple_music")
    assert apple["connected"] is True
    assert apple["artist_count"] == 2
    assert apple["synced_at"] == synced.isoformat()


def test_list_music_connections_handles_connected_empty_library() -> None:
    """Connected provider with no cache rows reports count=0, synced_at=None.

    Surfaces "connected but empty/failed sync" to the UI so the user
    isn't stuck looking at a no-op button.
    """
    user = _FakeMusicUser(
        music_connections=[_FakeMusicConnection(provider=OAuthProvider.TIDAL)],
        spotify_top_artists=None,
        spotify_synced_at=None,
        tidal_top_artists=None,
        tidal_synced_at=None,
        apple_top_artists=None,
        apple_synced_at=None,
    )
    result = users_service.list_music_connections(user)  # type: ignore[arg-type]
    tidal = next(r for r in result if r["provider"] == "tidal")
    assert tidal["connected"] is True
    assert tidal["artist_count"] == 0
    assert tidal["synced_at"] is None


def test_list_music_connections_returns_all_providers_in_stable_order() -> None:
    """The UI relies on stable ordering: Spotify, Tidal, Apple Music."""
    user = _FakeMusicUser(
        music_connections=[],
        spotify_top_artists=None,
        spotify_synced_at=None,
        tidal_top_artists=None,
        tidal_synced_at=None,
        apple_top_artists=None,
        apple_synced_at=None,
    )
    result = users_service.list_music_connections(user)  # type: ignore[arg-type]
    assert [row["provider"] for row in result] == [
        "spotify",
        "tidal",
        "apple_music",
    ]


def test_list_music_connections_caps_artist_preview_at_24() -> None:
    """The chip row on settings is bounded — don't ship a 200-artist payload."""
    cached = [{"id": f"a{i}", "name": f"Artist {i}"} for i in range(50)]
    user = _FakeMusicUser(
        music_connections=[_FakeMusicConnection(provider=OAuthProvider.APPLE_MUSIC)],
        spotify_top_artists=None,
        spotify_synced_at=None,
        tidal_top_artists=None,
        tidal_synced_at=None,
        apple_top_artists=cached,
        apple_synced_at=None,
    )
    result = users_service.list_music_connections(user)  # type: ignore[arg-type]
    apple = next(r for r in result if r["provider"] == "apple_music")
    assert apple["artist_count"] == 50  # full count from cache
    assert len(apple["artists"]) == 24  # but preview is capped
    assert apple["artists"][0]["name"] == "Artist 0"
    assert apple["artists"][23]["name"] == "Artist 23"


def test_list_music_connections_sanitizes_artist_preview_fields() -> None:
    """A malformed cached artist row falls back to safe defaults, not a crash."""
    cached = [
        {
            "id": "ok",
            "name": "Valid Artist",
            "genres": ["rock", 7, "indie"],
            "image_url": "https://cdn.example/img.jpg",
        },
        {"id": None, "name": 42, "genres": "not-a-list", "image_url": 99},
    ]
    user = _FakeMusicUser(
        music_connections=[_FakeMusicConnection(provider=OAuthProvider.SPOTIFY)],
        spotify_top_artists=cached,
        spotify_synced_at=None,
        tidal_top_artists=None,
        tidal_synced_at=None,
        apple_top_artists=None,
        apple_synced_at=None,
    )
    result = users_service.list_music_connections(user)  # type: ignore[arg-type]
    spotify = next(r for r in result if r["provider"] == "spotify")
    artists = spotify["artists"]
    assert artists[0] == {
        "id": "ok",
        "name": "Valid Artist",
        "genres": ["rock", "indie"],
        "image_url": "https://cdn.example/img.jpg",
    }
    assert artists[1] == {
        "id": None,
        "name": "",
        "genres": [],
        "image_url": None,
    }


def test_serialize_user_surfaces_defaults_when_optional_fields_null() -> None:
    """Null optional fields collapse to empty collections where sensible."""
    from datetime import datetime

    user = _FakeUser(
        id=uuid.uuid4(),
        email="user@example.com",
        display_name=None,
        avatar_url=None,
        city_id=None,
        digest_frequency=DigestFrequency.NEVER,
        genre_preferences=None,
        notification_settings=None,
        last_login_at=None,
        created_at=datetime(2026, 1, 1),
    )
    payload = users_service.serialize_user(user)  # type: ignore[arg-type]

    assert payload["display_name"] is None
    assert payload["genre_preferences"] == []
    assert payload["notification_settings"] == {}
    assert payload["last_login_at"] is None

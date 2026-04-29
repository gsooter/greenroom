"""Unit tests for :mod:`backend.services.apple_music`.

Apple Music does not use an OAuth redirect — the backend mints an
ES256 developer token for MusicKit JS, which hands back a Music User
Token (MUT) the backend validates against Apple's REST API. Tests mock
``requests.get`` so no network traffic leaves the box, and use a
session-wide ES256 key (generated in ``conftest.py``) so ``jwt.decode``
can verify what :func:`mint_developer_token` emits.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization

from backend.core.exceptions import APPLE_MUSIC_AUTH_FAILED, AppError
from backend.services import apple_music as apple_music_service
from backend.services.apple_music import (
    SOURCE_HEAVY_ROTATION,
    SOURCE_LIBRARY,
    SOURCE_RECENTLY_PLAYED,
    _artists_from_heavy_rotation,
    _artists_from_recently_played,
    _hash_token,
    _merge_signals,
    _simplify_artist,
    get_heavy_rotation,
    get_library_artists,
    get_recently_played_tracks,
    is_configured,
    mint_developer_token,
    sync_top_artists,
    validate_music_user_token,
)
from backend.tests.conftest import APPLE_MUSIC_TEST_PEM


class _FakeResponse:
    """Stand-in for :class:`requests.Response` exposing only the fields used."""

    def __init__(
        self,
        *,
        status_code: int = 200,
        json_data: dict[str, Any] | None = None,
        text: str = "",
    ) -> None:
        self.status_code = status_code
        self._json = json_data or {}
        self.text = text

    def json(self) -> dict[str, Any]:
        return self._json


def _public_key_pem() -> bytes:
    """Return the PEM of the test key's public half for ``jwt.decode``.

    Returns:
        The SubjectPublicKeyInfo PEM bytes for the ES256 test key.
    """
    private_key = serialization.load_pem_private_key(
        APPLE_MUSIC_TEST_PEM.encode("ascii"),
        password=None,
    )
    return private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


# ---------------------------------------------------------------------------
# is_configured
# ---------------------------------------------------------------------------


def test_is_configured_true_when_env_populated() -> None:
    """conftest seeds every Apple Music env var, so this should be True."""
    assert is_configured() is True


def test_is_configured_false_when_missing_team_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing team id should fail the configuration check."""
    monkeypatch.setenv("APPLE_MUSIC_TEAM_ID", "")
    assert is_configured() is False


def test_is_configured_false_without_any_private_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without inline PEM or on-disk path, configuration is incomplete."""
    monkeypatch.setenv("APPLE_MUSIC_PRIVATE_KEY", "")
    monkeypatch.setenv("APPLE_MUSIC_PRIVATE_KEY_PATH", "")
    assert is_configured() is False


# ---------------------------------------------------------------------------
# mint_developer_token
# ---------------------------------------------------------------------------


def test_mint_developer_token_produces_valid_es256_jwt() -> None:
    """Minted token verifies against the test public key with expected claims."""
    token = mint_developer_token()
    assert isinstance(token, str) and token.count(".") == 2

    header = jwt.get_unverified_header(token)
    assert header["alg"] == "ES256"
    assert header["kid"] == "TESTKEY001"

    decoded = jwt.decode(
        token,
        _public_key_pem(),
        algorithms=["ES256"],
        options={"verify_aud": False},
    )
    assert decoded["iss"] == "TESTTEAM01"
    assert decoded["sub"] == "media.greenroom.test.web"
    assert decoded["exp"] > decoded["iat"]


def test_mint_developer_token_raises_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing config should surface APPLE_MUSIC_AUTH_FAILED at 503."""
    monkeypatch.setenv("APPLE_MUSIC_TEAM_ID", "")
    with pytest.raises(AppError) as exc_info:
        mint_developer_token()
    assert exc_info.value.code == APPLE_MUSIC_AUTH_FAILED
    assert exc_info.value.status_code == 503


def test_mint_developer_token_loads_key_from_disk(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """When only the on-disk path is set, the PEM should be read from disk."""
    key_path = tmp_path / "AuthKey_TEST.p8"
    key_path.write_text(APPLE_MUSIC_TEST_PEM, encoding="utf-8")

    monkeypatch.setenv("APPLE_MUSIC_PRIVATE_KEY", "")
    monkeypatch.setenv("APPLE_MUSIC_PRIVATE_KEY_PATH", str(key_path))

    token = mint_developer_token()
    decoded = jwt.decode(
        token,
        _public_key_pem(),
        algorithms=["ES256"],
        options={"verify_aud": False},
    )
    assert decoded["iss"] == "TESTTEAM01"


def test_load_private_key_raises_on_unreadable_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unreadable on-disk path should surface as a 500 AppError."""
    monkeypatch.setenv("APPLE_MUSIC_PRIVATE_KEY", "")
    monkeypatch.setenv("APPLE_MUSIC_PRIVATE_KEY_PATH", "/nonexistent/path/key.p8")
    with pytest.raises(AppError) as exc_info:
        mint_developer_token()
    assert exc_info.value.code == APPLE_MUSIC_AUTH_FAILED
    assert exc_info.value.status_code == 500


# ---------------------------------------------------------------------------
# validate_music_user_token
# ---------------------------------------------------------------------------


def test_validate_music_user_token_happy_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 200 storefront response should produce a populated identity."""
    monkeypatch.setattr(
        apple_music_service.requests,
        "get",
        lambda *_a, **_k: _FakeResponse(
            json_data={"data": [{"id": "us", "type": "storefronts"}]},
        ),
    )
    identity = validate_music_user_token("mut-123")
    assert identity.storefront == "us"
    assert identity.provider_user_id == _hash_token("mut-123")
    assert identity.provider_user_id != "mut-123"


def test_validate_music_user_token_rejects_non_200(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-200 response should surface as APPLE_MUSIC_AUTH_FAILED (401)."""
    monkeypatch.setattr(
        apple_music_service.requests,
        "get",
        lambda *_a, **_k: _FakeResponse(status_code=401, text="bad token"),
    )
    with pytest.raises(AppError) as exc_info:
        validate_music_user_token("stale-mut")
    assert exc_info.value.code == APPLE_MUSIC_AUTH_FAILED
    assert exc_info.value.status_code == 401


def test_validate_music_user_token_handles_missing_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty data list should yield an empty storefront but still succeed."""
    monkeypatch.setattr(
        apple_music_service.requests,
        "get",
        lambda *_a, **_k: _FakeResponse(json_data={"data": []}),
    )
    identity = validate_music_user_token("mut-xyz")
    assert identity.storefront == ""


# ---------------------------------------------------------------------------
# get_library_artists pagination
# ---------------------------------------------------------------------------


def test_get_library_artists_paginates_until_short_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Successive calls should concatenate until a short page terminates."""

    def _page(prefix: str, count: int) -> dict[str, Any]:
        return {
            "data": [
                {"id": f"{prefix}{i}", "attributes": {"name": f"{prefix}{i}"}}
                for i in range(count)
            ]
        }

    pages = [_page("a", 100), _page("b", 50)]
    calls: list[dict[str, Any]] = []

    def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        calls.append(kwargs.get("params", {}))
        return _FakeResponse(json_data=pages[len(calls) - 1])

    monkeypatch.setattr(apple_music_service.requests, "get", fake_get)
    artists = get_library_artists("mut-123", limit=200)
    assert len(artists) == 150
    assert calls[0]["offset"] == 0
    assert calls[1]["offset"] == 100


def test_get_library_artists_respects_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A limit smaller than a full page still caps the result list."""
    page = {
        "data": [{"id": f"a{i}", "attributes": {"name": f"A{i}"}} for i in range(100)]
    }
    monkeypatch.setattr(
        apple_music_service.requests,
        "get",
        lambda *_a, **_k: _FakeResponse(json_data=page),
    )
    artists = get_library_artists("mut-123", limit=25)
    assert len(artists) == 25


def test_get_library_artists_rejects_non_200(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Any non-200 page should raise APPLE_MUSIC_AUTH_FAILED (502)."""
    monkeypatch.setattr(
        apple_music_service.requests,
        "get",
        lambda *_a, **_k: _FakeResponse(status_code=500, text="boom"),
    )
    with pytest.raises(AppError) as exc_info:
        get_library_artists("mut-123", limit=10)
    assert exc_info.value.code == APPLE_MUSIC_AUTH_FAILED
    assert exc_info.value.status_code == 502


# ---------------------------------------------------------------------------
# _simplify_artist
# ---------------------------------------------------------------------------


def test_simplify_artist_substitutes_artwork_placeholders() -> None:
    """Artwork URL ``{w}`` / ``{h}`` placeholders should be replaced with 256."""
    raw = {
        "id": "l.12345",
        "attributes": {
            "name": "Phoebe Bridgers",
            "artwork": {
                "url": "https://is1.apple.com/img/{w}x{h}.jpg",
            },
        },
    }
    slim = _simplify_artist(raw)
    assert slim == {
        "id": "l.12345",
        "name": "Phoebe Bridgers",
        "genres": [],
        "image_url": "https://is1.apple.com/img/256x256.jpg",
    }


def test_simplify_artist_handles_missing_attributes() -> None:
    """Missing or malformed attributes should not raise; image_url stays None."""
    slim = _simplify_artist({"id": "l.1"})
    assert slim == {"id": "l.1", "name": "", "genres": [], "image_url": None}


def test_simplify_artist_handles_non_string_name() -> None:
    """Non-string names should be coerced to empty string, not propagated."""
    slim = _simplify_artist({"id": "l.1", "attributes": {"name": 123}})
    assert slim["name"] == ""


# ---------------------------------------------------------------------------
# sync_top_artists
# ---------------------------------------------------------------------------


def _fake_session_factory() -> tuple[Any, list[bool]]:
    """Return a stand-in session and the list it flushes into.

    Returns:
        Tuple of the fake session and a list that each ``flush`` call
        appends True to.
    """
    flushed: list[bool] = []

    class _FakeSession:
        def flush(self) -> None:
            flushed.append(True)

    return _FakeSession(), flushed


def test_sync_top_artists_merges_all_three_signals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: library, recently-played, and heavy rotation merge."""
    monkeypatch.setattr(
        apple_music_service,
        "get_library_artists",
        lambda _mut, limit=200: [
            {"id": "l.1", "attributes": {"name": "One"}},
            {"id": "l.2", "attributes": {"name": "Two"}},
        ],
    )
    monkeypatch.setattr(
        apple_music_service,
        "get_recently_played_tracks",
        lambda _mut, limit=100: [
            {"attributes": {"artistName": "Three", "genreNames": ["Indie"]}},
        ],
    )
    monkeypatch.setattr(
        apple_music_service,
        "get_heavy_rotation",
        lambda _mut, limit=50: [
            {
                "type": "albums",
                "attributes": {"artistName": "Four", "genreNames": ["Rock"]},
            },
        ],
    )

    session, flushed = _fake_session_factory()
    user = SimpleNamespace(
        id="deadbeef",
        apple_top_artist_ids=None,
        apple_top_artists=None,
        apple_synced_at=None,
    )
    count = sync_top_artists(
        session,  # type: ignore[arg-type]
        user,  # type: ignore[arg-type]
        music_user_token="mut-ok",
    )
    assert count == 4
    # Ordered affinity-descending — heavy rotation first, then recent,
    # then library.
    names = [a["name"] for a in user.apple_top_artists]
    assert names[0] == "Four"
    assert names[1] == "Three"
    assert set(names[2:]) == {"One", "Two"}
    sources = {a["name"]: a["source"] for a in user.apple_top_artists}
    assert sources == {
        "Four": SOURCE_HEAVY_ROTATION,
        "Three": SOURCE_RECENTLY_PLAYED,
        "One": SOURCE_LIBRARY,
        "Two": SOURCE_LIBRARY,
    }
    assert user.apple_synced_at is not None
    assert flushed == [True]


def test_sync_top_artists_tolerates_recent_and_heavy_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing recent/heavy endpoint should not block the library sync."""
    monkeypatch.setattr(
        apple_music_service,
        "get_library_artists",
        lambda _mut, limit=200: [
            {"id": "l.1", "attributes": {"name": "Library Only"}},
        ],
    )

    def _boom(*_a: Any, **_k: Any) -> list[dict[str, Any]]:
        raise AppError(
            code=APPLE_MUSIC_AUTH_FAILED,
            message="boom",
            status_code=502,
        )

    monkeypatch.setattr(apple_music_service, "get_recently_played_tracks", _boom)
    monkeypatch.setattr(apple_music_service, "get_heavy_rotation", _boom)

    session, _flushed = _fake_session_factory()
    user = SimpleNamespace(
        id="deadbeef",
        apple_top_artist_ids=None,
        apple_top_artists=None,
        apple_synced_at=None,
    )
    count = sync_top_artists(
        session,  # type: ignore[arg-type]
        user,  # type: ignore[arg-type]
        music_user_token="mut-ok",
    )
    assert count == 1
    assert user.apple_top_artists[0]["source"] == SOURCE_LIBRARY


# ---------------------------------------------------------------------------
# get_recently_played_tracks
# ---------------------------------------------------------------------------


def test_get_recently_played_tracks_paginates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pagination walks the 30-item pages until a short page terminates."""

    def _page(prefix: str, count: int) -> dict[str, Any]:
        return {
            "data": [
                {"id": f"{prefix}{i}", "attributes": {"artistName": f"{prefix}{i}"}}
                for i in range(count)
            ]
        }

    pages = [_page("a", 30), _page("b", 30), _page("c", 10)]
    calls: list[dict[str, Any]] = []

    def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        calls.append(kwargs.get("params", {}))
        return _FakeResponse(json_data=pages[len(calls) - 1])

    monkeypatch.setattr(apple_music_service.requests, "get", fake_get)
    tracks = get_recently_played_tracks("mut-123", limit=100)
    assert len(tracks) == 70
    assert calls[0]["offset"] == 0
    assert calls[1]["offset"] == 30
    assert calls[2]["offset"] == 60


def test_get_recently_played_tracks_rejects_non_200(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 500 from Apple surfaces as APPLE_MUSIC_AUTH_FAILED (502)."""
    monkeypatch.setattr(
        apple_music_service.requests,
        "get",
        lambda *_a, **_k: _FakeResponse(status_code=500, text="boom"),
    )
    with pytest.raises(AppError) as exc_info:
        get_recently_played_tracks("mut-123", limit=10)
    assert exc_info.value.code == APPLE_MUSIC_AUTH_FAILED
    assert exc_info.value.status_code == 502


# ---------------------------------------------------------------------------
# _artists_from_recently_played
# ---------------------------------------------------------------------------


def test_artists_from_recently_played_dedupes_by_name() -> None:
    """Repeat artists across tracks collapse to one entry preserving order."""
    tracks = [
        {"attributes": {"artistName": "Phoebe Bridgers"}},
        {"attributes": {"artistName": "phoebe bridgers"}},
        {"attributes": {"artistName": "Boygenius", "genreNames": ["Indie"]}},
        {"attributes": {"artistName": ""}},
        {"attributes": {"not_artist_name": "nope"}},
    ]
    result = _artists_from_recently_played(tracks)
    assert [a["name"] for a in result] == ["Phoebe Bridgers", "Boygenius"]
    assert result[1]["genres"] == ["Indie"]
    # Synthetic id is stable and starts with the documented prefix.
    assert result[0]["id"].startswith("am:name:")


# ---------------------------------------------------------------------------
# get_heavy_rotation
# ---------------------------------------------------------------------------


def test_get_heavy_rotation_paginates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Heavy rotation walks 10-item pages up to the caller's limit."""
    pages = [
        {
            "data": [
                {"type": "albums", "attributes": {"artistName": f"A{i}"}}
                for i in range(10)
            ]
        },
        {
            "data": [
                {"type": "albums", "attributes": {"artistName": f"B{i}"}}
                for i in range(5)
            ]
        },
    ]
    calls: list[dict[str, Any]] = []

    def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        calls.append(kwargs.get("params", {}))
        return _FakeResponse(json_data=pages[len(calls) - 1])

    monkeypatch.setattr(apple_music_service.requests, "get", fake_get)
    resources = get_heavy_rotation("mut-abc", limit=50)
    assert len(resources) == 15
    assert calls[0]["offset"] == 0
    assert calls[1]["offset"] == 10


def test_get_heavy_rotation_rejects_non_200(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 500 surfaces as APPLE_MUSIC_AUTH_FAILED (502)."""
    monkeypatch.setattr(
        apple_music_service.requests,
        "get",
        lambda *_a, **_k: _FakeResponse(status_code=500, text="boom"),
    )
    with pytest.raises(AppError) as exc_info:
        get_heavy_rotation("mut-123", limit=10)
    assert exc_info.value.code == APPLE_MUSIC_AUTH_FAILED
    assert exc_info.value.status_code == 502


# ---------------------------------------------------------------------------
# _artists_from_heavy_rotation
# ---------------------------------------------------------------------------


def test_artists_from_heavy_rotation_flattens_albums_only() -> None:
    """Albums contribute; playlists are skipped because a curator is not an artist."""
    resources = [
        {
            "type": "albums",
            "attributes": {"artistName": "Fleet Foxes", "genreNames": ["Folk"]},
        },
        {
            "type": "library-albums",
            "attributes": {"artistName": "Big Thief"},
        },
        {
            "type": "playlists",
            "attributes": {"curatorName": "Apple Music Indie"},
        },
        {"type": "albums", "attributes": {}},
    ]
    result = _artists_from_heavy_rotation(resources)
    assert [a["name"] for a in result] == ["Fleet Foxes", "Big Thief"]


# ---------------------------------------------------------------------------
# _merge_signals
# ---------------------------------------------------------------------------


def test_merge_signals_promotes_strongest_source_and_unions_genres() -> None:
    """An artist that appears in all three sources picks heavy-rotation."""
    library = [
        {
            "id": "l.42",
            "name": "Phoebe Bridgers",
            "genres": ["Indie"],
            "image_url": None,
        },
    ]
    recently_played = [
        {
            "id": "am:name:phoebe",
            "name": "Phoebe Bridgers",
            "genres": ["Indie Folk"],
            "image_url": None,
        },
    ]
    heavy_rotation = [
        {
            "id": "am:name:phoebe",
            "name": "Phoebe Bridgers",
            "genres": ["Indie Rock"],
            "image_url": "https://example.com/pb.jpg",
        },
    ]
    merged = _merge_signals(
        library=library,
        recently_played=recently_played,
        heavy_rotation=heavy_rotation,
    )
    assert len(merged) == 1
    entry = merged[0]
    assert entry["source"] == SOURCE_HEAVY_ROTATION
    assert entry["affinity_score"] == 0.9
    # Genres are unioned across sources, library-first.
    assert set(entry["genres"]) == {"Indie", "Indie Folk", "Indie Rock"}
    # Heavy-rotation's artwork wins because it ranks highest.
    assert entry["image_url"] == "https://example.com/pb.jpg"


def test_merge_signals_keeps_distinct_artists_in_affinity_order() -> None:
    """Different artists stay separate, ranked by source affinity."""
    merged = _merge_signals(
        library=[{"id": "l.1", "name": "A", "genres": [], "image_url": None}],
        recently_played=[
            {"id": "am:name:b", "name": "B", "genres": [], "image_url": None}
        ],
        heavy_rotation=[
            {"id": "am:name:c", "name": "C", "genres": [], "image_url": None}
        ],
    )
    assert [a["name"] for a in merged] == ["C", "B", "A"]
    assert [a["source"] for a in merged] == [
        SOURCE_HEAVY_ROTATION,
        SOURCE_RECENTLY_PLAYED,
        SOURCE_LIBRARY,
    ]

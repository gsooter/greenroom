"""Unit tests for :mod:`backend.services.spotify`.

Every Spotify HTTP call goes through ``requests.post`` / ``requests.get``
so tests replace those with a fake returning scripted ``Response``-like
objects. No real network calls, no live Spotify account.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.core.exceptions import AppError
from backend.data.models.users import OAuthProvider
from backend.services import spotify as spotify_service
from backend.services.spotify import (
    SpotifyProfile,
    SpotifyTokens,
    _basic_auth_header,
    _ensure_fresh_access_token,
    _parse_token_response,
    _simplify_artist,
    build_authorize_url,
    exchange_code,
    get_profile,
    get_recently_played_artists,
    get_top_artists,
    refresh_access_token,
    sync_top_artists,
)


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


@dataclass
class _FakeOAuth:
    """Stand-in for the SPOTIFY OAuth provider row."""

    provider: OAuthProvider = OAuthProvider.SPOTIFY
    access_token: str | None = "access-123"
    refresh_token: str | None = "refresh-123"
    token_expires_at: datetime | None = field(
        default_factory=lambda: datetime.now(timezone.utc) + timedelta(hours=1)
    )


@dataclass
class _FakeUser:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    oauth_providers: list[_FakeOAuth] = field(default_factory=list)
    spotify_top_artists: list[dict[str, Any]] | None = None
    spotify_top_artist_ids: list[str] | None = None
    spotify_recent_artists: list[dict[str, Any]] | None = None
    spotify_recent_artist_ids: list[str] | None = None
    spotify_synced_at: datetime | None = None


# ---------------------------------------------------------------------------
# build_authorize_url
# ---------------------------------------------------------------------------


def test_build_authorize_url_carries_state_and_scopes() -> None:
    url = build_authorize_url(state="abc-xyz")
    assert url.startswith("https://accounts.spotify.com/authorize?")
    assert "client_id=test-spotify-id" in url
    assert "state=abc-xyz" in url
    assert "scope=user-read-email%20user-top-read%20user-read-recently-played" in url


# ---------------------------------------------------------------------------
# exchange_code / refresh / token parsing
# ---------------------------------------------------------------------------


def test_exchange_code_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 200 response yields populated SpotifyTokens."""
    monkeypatch.setattr(
        spotify_service.requests,
        "post",
        lambda *_a, **_k: _FakeResponse(
            json_data={
                "access_token": "a",
                "refresh_token": "r",
                "expires_in": 3600,
                "scope": "user-top-read",
            }
        ),
    )
    tokens = exchange_code("code-abc")
    assert tokens.access_token == "a"
    assert tokens.refresh_token == "r"
    assert tokens.scope == "user-top-read"


def test_exchange_code_rejects_non_200(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        spotify_service.requests,
        "post",
        lambda *_a, **_k: _FakeResponse(status_code=400, text="bad"),
    )
    with pytest.raises(AppError):
        exchange_code("code")


def test_exchange_code_rejects_malformed_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        spotify_service.requests,
        "post",
        lambda *_a, **_k: _FakeResponse(json_data={"access_token": "a"}),
    )
    with pytest.raises(AppError):
        exchange_code("code")


def test_refresh_access_token_handles_missing_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refresh response may omit refresh_token — None is returned."""
    monkeypatch.setattr(
        spotify_service.requests,
        "post",
        lambda *_a, **_k: _FakeResponse(
            json_data={
                "access_token": "a",
                "expires_in": 3600,
            }
        ),
    )
    tokens = refresh_access_token("old-refresh")
    assert tokens.access_token == "a"
    assert tokens.refresh_token is None
    assert tokens.scope == ""


def test_parse_token_response_non_string_fields_raises() -> None:
    """Numeric access_token, bogus refresh field → SPOTIFY_AUTH_FAILED."""
    bad = _FakeResponse(
        json_data={"access_token": 42, "expires_in": 3600}
    )
    with pytest.raises(AppError):
        _parse_token_response(bad)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# get_profile / get_top_artists
# ---------------------------------------------------------------------------


def test_get_profile_returns_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        spotify_service.requests,
        "get",
        lambda *_a, **_k: _FakeResponse(
            json_data={
                "id": "u1",
                "email": "a@b.c",
                "display_name": "A",
                "images": [{"url": "https://img.test/x"}],
            }
        ),
    )
    profile = get_profile("tok")
    assert isinstance(profile, SpotifyProfile)
    assert profile.id == "u1"
    assert profile.email == "a@b.c"
    assert profile.avatar_url == "https://img.test/x"


def test_get_profile_rejects_non_200(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        spotify_service.requests,
        "get",
        lambda *_a, **_k: _FakeResponse(status_code=401),
    )
    with pytest.raises(AppError):
        get_profile("tok")


def test_get_profile_rejects_missing_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        spotify_service.requests,
        "get",
        lambda *_a, **_k: _FakeResponse(json_data={"id": "u", "images": []}),
    )
    with pytest.raises(AppError):
        get_profile("tok")


def test_get_profile_without_images_has_null_avatar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        spotify_service.requests,
        "get",
        lambda *_a, **_k: _FakeResponse(
            json_data={"id": "u", "email": "x@y.z", "images": []}
        ),
    )
    profile = get_profile("tok")
    assert profile.avatar_url is None


def test_get_top_artists_filters_non_dict_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        spotify_service.requests,
        "get",
        lambda *_a, **_k: _FakeResponse(
            json_data={
                "items": [{"id": "a"}, "garbage", {"id": "b"}]
            }
        ),
    )
    result = get_top_artists("tok")
    assert [a["id"] for a in result] == ["a", "b"]


def test_get_top_artists_raises_on_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        spotify_service.requests,
        "get",
        lambda *_a, **_k: _FakeResponse(status_code=503),
    )
    with pytest.raises(AppError):
        get_top_artists("tok")


# ---------------------------------------------------------------------------
# _simplify_artist
# ---------------------------------------------------------------------------


def test_simplify_artist_picks_smallest_image_above_128() -> None:
    artist = {
        "id": "x",
        "name": "X",
        "genres": ["indie", 42, "rock"],
        "images": [
            {"url": "https://big", "height": 640},
            {"url": "https://med", "height": 300},
            {"url": "https://small", "height": 64},
        ],
    }
    result = _simplify_artist(artist)
    assert result["image_url"] == "https://med"
    assert result["genres"] == ["indie", "rock"]


def test_simplify_artist_falls_back_to_first_when_all_small() -> None:
    artist = {
        "id": "x",
        "name": "X",
        "images": [{"url": "https://only", "height": 64}],
    }
    assert _simplify_artist(artist)["image_url"] == "https://only"


def test_simplify_artist_handles_no_images() -> None:
    assert _simplify_artist({"id": "x", "name": "X"})["image_url"] is None


# ---------------------------------------------------------------------------
# _ensure_fresh_access_token
# ---------------------------------------------------------------------------


def test_ensure_fresh_returns_none_when_no_provider_row() -> None:
    user = _FakeUser(oauth_providers=[])
    assert _ensure_fresh_access_token(MagicMock(), user) is None  # type: ignore[arg-type]


def test_ensure_fresh_uses_cached_token_when_still_valid() -> None:
    user = _FakeUser(oauth_providers=[_FakeOAuth()])
    result = _ensure_fresh_access_token(MagicMock(), user)  # type: ignore[arg-type]
    assert result is not None
    token, _oauth = result
    assert token == "access-123"


def test_ensure_fresh_refreshes_when_expired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    oauth = _FakeOAuth(
        token_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)
    )
    user = _FakeUser(oauth_providers=[oauth])

    refreshed = SpotifyTokens(
        access_token="new-access",
        refresh_token="new-refresh",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        scope="",
    )
    monkeypatch.setattr(
        spotify_service, "refresh_access_token", lambda _t: refreshed
    )
    update_mock = MagicMock()
    monkeypatch.setattr(
        spotify_service.users_repo, "update_oauth_tokens", update_mock
    )
    result = _ensure_fresh_access_token(MagicMock(), user)  # type: ignore[arg-type]
    assert result is not None
    token, _oauth = result
    assert token == "new-access"
    update_mock.assert_called_once()


def test_ensure_fresh_raises_when_expired_without_refresh_token() -> None:
    oauth = _FakeOAuth(
        token_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        refresh_token=None,
    )
    user = _FakeUser(oauth_providers=[oauth])
    with pytest.raises(AppError):
        _ensure_fresh_access_token(MagicMock(), user)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# sync_top_artists
# ---------------------------------------------------------------------------


def test_sync_top_artists_returns_zero_when_no_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _FakeUser(oauth_providers=[])
    monkeypatch.setattr(
        spotify_service, "_ensure_fresh_access_token", lambda _s, _u: None
    )
    assert sync_top_artists(MagicMock(), user) == 0  # type: ignore[arg-type]


def test_sync_top_artists_persists_simplified_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _FakeUser()
    session = MagicMock()
    monkeypatch.setattr(
        spotify_service,
        "get_top_artists",
        lambda _t, limit=200: [
            {"id": "a", "name": "A", "images": []},
            "garbage",
            {"id": "b", "name": "B", "images": []},
        ],
    )
    monkeypatch.setattr(
        spotify_service,
        "get_recently_played_artists",
        lambda _t, limit=50: [
            {"id": "c", "name": "C", "images": []},
        ],
    )
    count = sync_top_artists(session, user, access_token="tok")  # type: ignore[arg-type]
    assert count == 2
    assert user.spotify_top_artist_ids == ["a", "b"]
    assert len(user.spotify_top_artists or []) == 2
    assert user.spotify_recent_artist_ids == ["c"]
    assert len(user.spotify_recent_artists or []) == 1
    assert user.spotify_synced_at is not None
    session.flush.assert_called_once()


def test_sync_top_artists_tolerates_recently_played_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recently-played flake should not block the top-artist sync."""
    user = _FakeUser()
    session = MagicMock()
    monkeypatch.setattr(
        spotify_service,
        "get_top_artists",
        lambda _t, limit=200: [{"id": "a", "name": "A", "images": []}],
    )

    def _raise(_t: str, *, limit: int = 50) -> list[dict[str, Any]]:
        raise AppError(
            code="SPOTIFY_AUTH_FAILED",
            message="boom",
            status_code=502,
        )

    monkeypatch.setattr(
        spotify_service, "get_recently_played_artists", _raise
    )
    count = sync_top_artists(session, user, access_token="tok")  # type: ignore[arg-type]
    assert count == 1
    assert user.spotify_top_artist_ids == ["a"]
    assert user.spotify_recent_artist_ids == []
    assert user.spotify_recent_artists == []


def test_get_top_artists_paginates_beyond_fifty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requests beyond 50 items walk the Spotify offset in 50-item pages."""
    calls: list[dict[str, Any]] = []

    def _fake_get(
        url: str, *, params: dict[str, Any], **_k: Any
    ) -> _FakeResponse:
        calls.append(params)
        offset = params["offset"]
        ids = [f"a{offset + i}" for i in range(params["limit"])]
        return _FakeResponse(
            json_data={"items": [{"id": i, "name": i} for i in ids]}
        )

    monkeypatch.setattr(spotify_service.requests, "get", _fake_get)
    result = get_top_artists("tok", limit=120)
    assert len(result) == 120
    assert [c["offset"] for c in calls] == [0, 50, 100]
    assert calls[-1]["limit"] == 20


def test_get_top_artists_stops_when_page_short(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A short page signals end-of-data — no further requests are made."""
    calls: list[dict[str, Any]] = []

    def _fake_get(
        url: str, *, params: dict[str, Any], **_k: Any
    ) -> _FakeResponse:
        calls.append(params)
        items = [{"id": f"a{i}", "name": "x"} for i in range(3)]
        return _FakeResponse(json_data={"items": items})

    monkeypatch.setattr(spotify_service.requests, "get", _fake_get)
    result = get_top_artists("tok", limit=200)
    assert len(result) == 3
    assert len(calls) == 1


def test_get_recently_played_artists_flattens_and_dedupes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same artist across tracks collapses to one entry, first-seen wins."""
    payload = {
        "items": [
            {"track": {"artists": [{"id": "a", "name": "A"}]}},
            {
                "track": {
                    "artists": [
                        {"id": "b", "name": "B"},
                        {"id": "a", "name": "A"},
                    ]
                }
            },
            "garbage",
            {"track": {"artists": [{"name": "C (no id)"}]}},
            {"track": {"artists": [{"name": "C (no id)"}]}},
        ]
    }
    monkeypatch.setattr(
        spotify_service.requests,
        "get",
        lambda *_a, **_k: _FakeResponse(json_data=payload),
    )
    artists = get_recently_played_artists("tok")
    assert [a.get("id") or a.get("name") for a in artists] == [
        "a",
        "b",
        "C (no id)",
    ]


def test_get_recently_played_artists_raises_on_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        spotify_service.requests,
        "get",
        lambda *_a, **_k: _FakeResponse(status_code=503),
    )
    with pytest.raises(AppError):
        get_recently_played_artists("tok")


# ---------------------------------------------------------------------------
# _basic_auth_header
# ---------------------------------------------------------------------------


def test_basic_auth_header_encodes_client_credentials() -> None:
    header = _basic_auth_header()
    assert header.startswith("Basic ")
    # Decoding the b64 back gives the id:secret pair.
    import base64

    decoded = base64.b64decode(header.removeprefix("Basic ")).decode()
    assert decoded == "test-spotify-id:test-spotify-secret"

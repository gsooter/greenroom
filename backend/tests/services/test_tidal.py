"""Unit tests for :mod:`backend.services.tidal`.

Mirrors the shape of ``test_spotify.py``. Every Tidal HTTP call goes
through ``requests.post`` / ``requests.get``, so tests replace those
with a fake returning scripted ``Response``-like objects. No network.
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.core.exceptions import TIDAL_AUTH_FAILED, AppError
from backend.services import tidal as tidal_service
from backend.services.tidal import (
    _basic_auth_header,
    _simplify_artist,
    build_authorize_url,
    exchange_code,
    get_profile,
    get_top_artists,
    refresh_access_token,
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


# ---------------------------------------------------------------------------
# build_authorize_url
# ---------------------------------------------------------------------------


def test_build_authorize_url_carries_state_and_scopes() -> None:
    url = build_authorize_url(state="abc-xyz", code_challenge="chal-123")
    assert url.startswith("https://login.tidal.com/authorize?")
    assert "client_id=test-tidal-id" in url
    assert "state=abc-xyz" in url
    assert "scope=user.read%20collection.read" in url
    assert "code_challenge=chal-123" in url
    assert "code_challenge_method=S256" in url


def test_generate_pkce_pair_is_deterministic_to_rfc7636() -> None:
    """Challenge is base64url(sha256(verifier)) without padding."""
    import base64
    import hashlib

    from backend.services.tidal import generate_pkce_pair

    verifier, challenge = generate_pkce_pair()
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    assert challenge == expected
    assert len(verifier) >= 43


# ---------------------------------------------------------------------------
# exchange_code / refresh / token parsing
# ---------------------------------------------------------------------------


def test_exchange_code_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 200 response yields populated TidalTokens including user_id."""
    monkeypatch.setattr(
        tidal_service.requests,
        "post",
        lambda *_a, **_k: _FakeResponse(
            json_data={
                "access_token": "a",
                "refresh_token": "r",
                "expires_in": 3600,
                "scope": "user.read",
                "user_id": 777,
            }
        ),
    )
    tokens = exchange_code("code-abc", code_verifier="v-123")
    assert tokens.access_token == "a"
    assert tokens.refresh_token == "r"
    assert tokens.scope == "user.read"
    assert tokens.user_id == "777"


def test_exchange_code_tolerates_missing_user_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refresh responses may omit ``user_id``; the dataclass stays valid."""
    monkeypatch.setattr(
        tidal_service.requests,
        "post",
        lambda *_a, **_k: _FakeResponse(
            json_data={"access_token": "a", "expires_in": 3600, "refresh_token": "r"}
        ),
    )
    tokens = exchange_code("code-abc", code_verifier="v")
    assert tokens.user_id is None


def test_exchange_code_rejects_non_200(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-2xx token response raises TIDAL_AUTH_FAILED."""
    monkeypatch.setattr(
        tidal_service.requests,
        "post",
        lambda *_a, **_k: _FakeResponse(status_code=400, text="bad"),
    )
    with pytest.raises(AppError) as excinfo:
        exchange_code("code-abc", code_verifier="v")
    assert excinfo.value.code == TIDAL_AUTH_FAILED


def test_exchange_code_rejects_incomplete_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing access_token or expires_in is surfaced as an auth failure."""
    monkeypatch.setattr(
        tidal_service.requests,
        "post",
        lambda *_a, **_k: _FakeResponse(json_data={"access_token": "a"}),
    )
    with pytest.raises(AppError) as excinfo:
        exchange_code("code", code_verifier="v")
    assert excinfo.value.code == TIDAL_AUTH_FAILED


def test_exchange_code_sends_pkce_verifier(monkeypatch: pytest.MonkeyPatch) -> None:
    """The verifier and client_id must land in the token POST body."""
    captured: dict[str, Any] = {}

    def fake_post(_url: str, **kwargs: Any) -> _FakeResponse:
        captured.update(kwargs.get("data", {}))
        return _FakeResponse(
            json_data={"access_token": "a", "expires_in": 3600, "refresh_token": "r"}
        )

    monkeypatch.setattr(tidal_service.requests, "post", fake_post)
    exchange_code("code-xyz", code_verifier="verifier-xyz")
    assert captured["code_verifier"] == "verifier-xyz"
    assert captured["client_id"] == "test-tidal-id"
    assert captured["grant_type"] == "authorization_code"


def test_refresh_access_token_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refresh parses exactly like the initial exchange."""
    monkeypatch.setattr(
        tidal_service.requests,
        "post",
        lambda *_a, **_k: _FakeResponse(
            json_data={
                "access_token": "new",
                "expires_in": 3600,
            }
        ),
    )
    tokens = refresh_access_token("prev-refresh")
    assert tokens.access_token == "new"
    assert tokens.refresh_token is None


def test_basic_auth_header_uses_test_creds() -> None:
    """Basic header is base64(client_id:client_secret) from settings."""
    import base64

    header = _basic_auth_header()
    assert header.startswith("Basic ")
    decoded = base64.b64decode(header[len("Basic ") :]).decode()
    assert decoded == "test-tidal-id:test-tidal-secret"


# ---------------------------------------------------------------------------
# get_profile
# ---------------------------------------------------------------------------


def test_get_profile_parses_v2_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    """v2 ``{data: {id, attributes}}`` shape is unpacked correctly."""
    monkeypatch.setattr(
        tidal_service.requests,
        "get",
        lambda *_a, **_k: _FakeResponse(
            json_data={
                "data": {
                    "id": "42",
                    "attributes": {
                        "email": "p@example.test",
                        "username": "Pat",
                        "picture": "https://img/x.jpg",
                    },
                }
            }
        ),
    )
    profile = get_profile("tok", "42")
    assert profile.id == "42"
    assert profile.email == "p@example.test"
    assert profile.display_name == "Pat"
    assert profile.avatar_url == "https://img/x.jpg"


def test_get_profile_targets_user_id_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """The request URL embeds the user_id — there is no /users/me."""
    captured: dict[str, Any] = {}

    def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        captured["url"] = url
        captured["params"] = kwargs.get("params")
        return _FakeResponse(json_data={"data": {"id": "99", "attributes": {}}})

    monkeypatch.setattr(tidal_service.requests, "get", fake_get)
    get_profile("tok", "99")
    assert captured["url"].endswith("/users/99")
    assert captured["params"] == {"countryCode": "US"}


def test_get_profile_rejects_non_200(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        tidal_service.requests,
        "get",
        lambda *_a, **_k: _FakeResponse(status_code=401, text="nope"),
    )
    with pytest.raises(AppError) as excinfo:
        get_profile("tok", "42")
    assert excinfo.value.code == TIDAL_AUTH_FAILED


def test_get_profile_requires_data_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    """A flat/non-JSON:API response is treated as a Tidal-side failure."""
    monkeypatch.setattr(
        tidal_service.requests,
        "get",
        lambda *_a, **_k: _FakeResponse(json_data={"id": "99"}),
    )
    with pytest.raises(AppError) as excinfo:
        get_profile("tok", "99")
    assert excinfo.value.code == TIDAL_AUTH_FAILED


# ---------------------------------------------------------------------------
# get_top_artists — resource + include=items
# ---------------------------------------------------------------------------


def test_get_top_artists_returns_sideloaded_artists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single ?include=artists call surfaces the included artists."""
    captured: dict[str, Any] = {}

    def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        captured["url"] = url
        captured["params"] = kwargs.get("params")
        return _FakeResponse(
            json_data={
                "data": {
                    "id": "u",
                    "type": "userCollection",
                    "relationships": {"artists": {"links": {}}},
                },
                "included": [
                    {"id": "1", "type": "artists", "attributes": {"name": "A1"}},
                    {"id": "2", "type": "artists", "attributes": {"name": "A2"}},
                    {"id": "ignored", "type": "artworks"},
                ],
            }
        )

    monkeypatch.setattr(tidal_service.requests, "get", fake_get)
    artists = get_top_artists("tok", "u", limit=50)
    assert [a["id"] for a in artists] == ["1", "2"]
    assert captured["url"].endswith("/userCollections/u")
    assert captured["params"] == {"include": "artists"}


def test_get_top_artists_walks_artists_relationship_next(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``data.relationships.artists.links.next`` cursor is followed."""
    pages = [
        _FakeResponse(
            json_data={
                "data": {
                    "id": "u",
                    "relationships": {
                        "artists": {
                            "links": {
                                "next": (
                                    "/userCollections/u?include=artists&page[cursor]=p2"
                                )
                            }
                        }
                    },
                },
                "included": [
                    {"id": "1", "type": "artists", "attributes": {"name": "A1"}},
                ],
            }
        ),
        _FakeResponse(
            json_data={
                "data": {"id": "u", "relationships": {"artists": {"links": {}}}},
                "included": [
                    {"id": "2", "type": "artists", "attributes": {"name": "A2"}},
                ],
            }
        ),
    ]

    def fake_get(*_a: Any, **_k: Any) -> _FakeResponse:
        return pages.pop(0)

    monkeypatch.setattr(tidal_service.requests, "get", fake_get)
    artists = get_top_artists("tok", "u", limit=50)
    assert [a["id"] for a in artists] == ["1", "2"]


def test_get_top_artists_returns_empty_when_no_included(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty collection → single call, empty list."""
    calls: list[str] = []

    def fake_get(url: str, **_k: Any) -> _FakeResponse:
        calls.append(url)
        return _FakeResponse(
            json_data={
                "data": {"id": "u", "relationships": {"artists": {"links": {}}}},
                "included": [],
            }
        )

    monkeypatch.setattr(tidal_service.requests, "get", fake_get)
    artists = get_top_artists("tok", "u", limit=50)
    assert artists == []
    assert len(calls) == 1


def test_get_top_artists_rejects_non_200(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-200 from the collection fetch surfaces as TIDAL_AUTH_FAILED."""
    monkeypatch.setattr(
        tidal_service.requests,
        "get",
        lambda *_a, **_k: _FakeResponse(status_code=500, text="err"),
    )
    with pytest.raises(AppError) as excinfo:
        get_top_artists("tok", "u")
    assert excinfo.value.code == TIDAL_AUTH_FAILED


def test_get_top_artists_deduplicates_across_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated artist ids across pages are kept once in order."""
    pages = [
        _FakeResponse(
            json_data={
                "data": {
                    "id": "u",
                    "relationships": {
                        "artists": {"links": {"next": "/x?page[cursor]=p2"}}
                    },
                },
                "included": [
                    {"id": "1", "type": "artists"},
                    {"id": "2", "type": "artists"},
                ],
            }
        ),
        _FakeResponse(
            json_data={
                "data": {"id": "u", "relationships": {"artists": {"links": {}}}},
                "included": [
                    {"id": "2", "type": "artists"},
                    {"id": "3", "type": "artists"},
                ],
            }
        ),
    ]

    def fake_get(*_a: Any, **_k: Any) -> _FakeResponse:
        return pages.pop(0)

    monkeypatch.setattr(tidal_service.requests, "get", fake_get)
    artists = get_top_artists("tok", "u", limit=50)
    assert [a["id"] for a in artists] == ["1", "2", "3"]


# ---------------------------------------------------------------------------
# _simplify_artist
# ---------------------------------------------------------------------------


def test_simplify_artist_picks_largest_image_from_image_links() -> None:
    """``imageLinks`` is a list of sizes — pick the widest one."""
    simplified = _simplify_artist(
        {
            "id": "1",
            "attributes": {
                "name": "Bands",
                "imageLinks": [
                    {"href": "https://img/sm.jpg", "meta": {"width": 160}},
                    {"href": "https://img/lg.jpg", "meta": {"width": 640}},
                    {"href": "https://img/md.jpg", "meta": {"width": 320}},
                ],
            },
        }
    )
    assert simplified == {
        "id": "1",
        "name": "Bands",
        "genres": [],
        "image_url": "https://img/lg.jpg",
    }


def test_simplify_artist_falls_back_to_flat_shape() -> None:
    """Non-JSON:API payloads still yield a usable slim dict."""
    simplified = _simplify_artist(
        {"id": "2", "name": "Echo", "picture": "https://img/e.jpg"}
    )
    assert simplified == {
        "id": "2",
        "name": "Echo",
        "genres": [],
        "image_url": "https://img/e.jpg",
    }

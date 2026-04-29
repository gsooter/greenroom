"""Unit tests for :mod:`backend.services.apple_maps`.

Exercises the MapKit JS token minter and the Redis-backed cache
behavior. ES256 signing uses the session-wide test key seeded in
``conftest.py``; ``jwt.decode`` with the paired public half verifies
both the header and claims.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization

from backend.core.exceptions import APPLE_MAPS_UNAVAILABLE, AppError
from backend.services import apple_maps as service
from backend.tests.conftest import APPLE_MAPKIT_TEST_PEM


@pytest.fixture(autouse=True)
def _disable_module_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the module-level Redis client out of the way.

    Tests that want caching pass their own ``_FakeRedis`` explicitly.
    Unit tests that pass ``redis_client=None`` should behave as if Redis
    were unreachable so each mint call produces a fresh token.
    """
    service.reset_redis_client_for_tests()
    monkeypatch.setattr(service, "_get_redis", lambda: None)


def _public_key_pem() -> bytes:
    """Return the PEM of the MapKit test key's public half.

    Returns:
        The SubjectPublicKeyInfo PEM bytes for the ES256 test key.
    """
    private_key = serialization.load_pem_private_key(
        APPLE_MAPKIT_TEST_PEM.encode("ascii"),
        password=None,
    )
    return private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


class _FakeRedis:
    """Minimal in-memory stand-in for the redis client methods we use."""

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.expirations: dict[str, int] = {}

    def get(self, key: str) -> bytes | None:
        return self.store.get(key)

    def set(
        self,
        key: str,
        value: str | bytes,
        *,
        ex: int | None = None,
    ) -> None:
        self.store[key] = value.encode() if isinstance(value, str) else value
        if ex is not None:
            self.expirations[key] = ex


# ---------------------------------------------------------------------------
# is_configured
# ---------------------------------------------------------------------------


def test_is_configured_true_when_env_populated() -> None:
    """conftest seeds every MapKit env var, so this should be True."""
    assert service.is_configured() is True


def test_is_configured_false_without_team_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPLE_MAPKIT_TEAM_ID", "")
    assert service.is_configured() is False


def test_is_configured_false_without_any_private_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPLE_MAPKIT_PRIVATE_KEY", "")
    monkeypatch.setenv("APPLE_MAPKIT_PRIVATE_KEY_PATH", "")
    assert service.is_configured() is False


# ---------------------------------------------------------------------------
# mint_mapkit_token
# ---------------------------------------------------------------------------


def test_mint_mapkit_token_signs_with_es256_and_includes_origin() -> None:
    payload = service.mint_mapkit_token(
        origin="https://example.test",
        redis_client=None,
    )
    assert set(payload) == {"token", "expires_at"}

    token = payload["token"]
    assert isinstance(token, str)
    header = jwt.get_unverified_header(token)
    assert header["alg"] == "ES256"
    assert header["kid"] == "TESTMAP001"
    assert header["typ"] == "JWT"

    decoded = jwt.decode(token, _public_key_pem(), algorithms=["ES256"])
    assert decoded["iss"] == "TESTTEAM01"
    assert decoded["origin"] == "https://example.test"
    assert decoded["exp"] > decoded["iat"]


def test_mint_mapkit_token_omits_origin_claim_when_none() -> None:
    payload = service.mint_mapkit_token(origin=None, redis_client=None)
    decoded = jwt.decode(payload["token"], _public_key_pem(), algorithms=["ES256"])
    assert "origin" not in decoded


def test_mint_mapkit_token_expires_thirty_minutes_from_anchor() -> None:
    anchor = datetime(2026, 4, 1, 12, 0, 0, tzinfo=UTC)
    payload = service.mint_mapkit_token(now=anchor, redis_client=None)
    expected = int((anchor + timedelta(minutes=30)).timestamp())
    assert payload["expires_at"] == expected


def test_mint_mapkit_token_raises_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPLE_MAPKIT_TEAM_ID", "")
    with pytest.raises(AppError) as exc:
        service.mint_mapkit_token(redis_client=None)
    assert exc.value.code == APPLE_MAPS_UNAVAILABLE
    assert exc.value.status_code == 503


def test_mint_mapkit_token_returns_cached_payload_when_fresh() -> None:
    fake = _FakeRedis()
    first = service.mint_mapkit_token(origin="o1", redis_client=fake)
    second = service.mint_mapkit_token(origin="o1", redis_client=fake)
    # Second call must return the exact cached payload (byte-identical
    # token) rather than re-sign with a new iat.
    assert first == second


def test_mint_mapkit_token_skips_cached_entry_when_expired() -> None:
    fake = _FakeRedis()
    expired_payload: dict[str, Any] = {
        "token": "stale.jwt.value",
        "expires_at": int(datetime.now(UTC).timestamp()) - 60,
    }
    fake.store["apple_maps:mapkit_token:v1:o1"] = json.dumps(expired_payload).encode()

    payload = service.mint_mapkit_token(origin="o1", redis_client=fake)
    assert payload["token"] != "stale.jwt.value"
    # New entry was written with the 25-minute TTL.
    assert fake.expirations["apple_maps:mapkit_token:v1:o1"] == 25 * 60


def test_mint_mapkit_token_ignores_cache_entries_missing_fields() -> None:
    fake = _FakeRedis()
    fake.store["apple_maps:mapkit_token:v1:o1"] = b'{"token": null}'
    payload = service.mint_mapkit_token(origin="o1", redis_client=fake)
    assert isinstance(payload["token"], str)
    assert len(payload["token"]) > 0


def test_mint_mapkit_token_tolerates_non_json_cache_blob() -> None:
    fake = _FakeRedis()
    fake.store["apple_maps:mapkit_token:v1:o1"] = b"not-json"
    payload = service.mint_mapkit_token(origin="o1", redis_client=fake)
    assert isinstance(payload["token"], str)


def test_mint_mapkit_token_buckets_by_origin_in_cache() -> None:
    fake = _FakeRedis()
    first = service.mint_mapkit_token(origin="a", redis_client=fake)
    second = service.mint_mapkit_token(origin="b", redis_client=fake)
    # Different origins live under different keys and therefore produce
    # distinct signed tokens.
    assert first["token"] != second["token"]
    assert "apple_maps:mapkit_token:v1:a" in fake.store
    assert "apple_maps:mapkit_token:v1:b" in fake.store


def test_mint_mapkit_token_loads_key_from_disk_when_inline_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    key_path = tmp_path / "mapkit.p8"
    key_path.write_text(APPLE_MAPKIT_TEST_PEM, encoding="utf-8")

    monkeypatch.setenv("APPLE_MAPKIT_PRIVATE_KEY", "")
    monkeypatch.setenv("APPLE_MAPKIT_PRIVATE_KEY_PATH", str(key_path))

    payload = service.mint_mapkit_token(redis_client=None)
    decoded = jwt.decode(payload["token"], _public_key_pem(), algorithms=["ES256"])
    assert decoded["iss"] == "TESTTEAM01"


def test_mint_mapkit_token_raises_when_key_path_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPLE_MAPKIT_PRIVATE_KEY", "")
    monkeypatch.setenv("APPLE_MAPKIT_PRIVATE_KEY_PATH", "/nonexistent/definitely.p8")
    with pytest.raises(AppError) as exc:
        service.mint_mapkit_token(redis_client=None)
    assert exc.value.status_code == 500
    assert exc.value.code == APPLE_MAPS_UNAVAILABLE


# ---------------------------------------------------------------------------
# build_snapshot_url
# ---------------------------------------------------------------------------


def _parse_snapshot_url(url: str) -> dict[str, str]:
    """Return the query params of a signed snapshot URL as a dict.

    Args:
        url: The full signed snapshot URL.

    Returns:
        Mapping of query param name to value (the signature included).
    """
    from urllib.parse import parse_qsl, urlsplit

    parts = urlsplit(url)
    return dict(parse_qsl(parts.query))


def test_build_snapshot_url_includes_credentials_and_signature() -> None:
    url = service.build_snapshot_url(
        latitude=38.9,
        longitude=-77.0,
        redis_client=None,
    )
    assert url.startswith("https://snapshot.apple-mapkit.com/api/v1/snapshot?")
    params = _parse_snapshot_url(url)
    assert params["teamId"] == "TESTTEAM01"
    assert params["keyId"] == "TESTMAP001"
    assert params["center"] == "38.900000,-77.000000"
    assert params["size"] == "600x400"
    assert params["scale"] == "2"
    assert params["colorScheme"] == "light"
    # Unpadded url-safe base64 of a 64-byte ECDSA P-256 output is 86 chars.
    assert len(params["signature"]) == 86
    assert "=" not in params["signature"]


def test_build_snapshot_url_verifies_against_public_key() -> None:
    import base64
    from urllib.parse import urlsplit

    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import (
        encode_dss_signature,
    )

    url = service.build_snapshot_url(
        latitude=10.0,
        longitude=20.0,
        redis_client=None,
    )
    parts = urlsplit(url)
    # The signature is computed over path+query MINUS ``signature=``
    # itself; reconstruct that canonical string.
    query_without_sig = "&".join(
        pair for pair in parts.query.split("&") if not pair.startswith("signature=")
    )
    to_verify = f"{parts.path}?{query_without_sig}".encode("ascii")

    signature_b64 = dict(p.split("=", 1) for p in parts.query.split("&"))["signature"]
    padded = signature_b64 + "=" * (-len(signature_b64) % 4)
    signature_bytes = base64.urlsafe_b64decode(padded)
    r = int.from_bytes(signature_bytes[:32], "big")
    s = int.from_bytes(signature_bytes[32:], "big")
    der = encode_dss_signature(r, s)

    private_key = serialization.load_pem_private_key(
        APPLE_MAPKIT_TEST_PEM.encode("ascii"),
        password=None,
    )
    assert isinstance(private_key, ec.EllipticCurvePrivateKey)
    private_key.public_key().verify(der, to_verify, ec.ECDSA(hashes.SHA256()))


def test_build_snapshot_url_raises_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPLE_MAPKIT_TEAM_ID", "")
    with pytest.raises(AppError) as exc:
        service.build_snapshot_url(latitude=0.0, longitude=0.0, redis_client=None)
    assert exc.value.code == APPLE_MAPS_UNAVAILABLE
    assert exc.value.status_code == 503


def test_build_snapshot_url_clamps_dimensions() -> None:
    url = service.build_snapshot_url(
        latitude=0.0,
        longitude=0.0,
        width=5000,
        height=5000,
        redis_client=None,
    )
    params = _parse_snapshot_url(url)
    assert params["size"] == "640x640"


def test_build_snapshot_url_coerces_invalid_scheme_to_light() -> None:
    url = service.build_snapshot_url(
        latitude=0.0,
        longitude=0.0,
        color_scheme="lavender",
        redis_client=None,
    )
    params = _parse_snapshot_url(url)
    assert params["colorScheme"] == "light"


def test_build_snapshot_url_includes_pin_annotation_when_labeled() -> None:
    url = service.build_snapshot_url(
        latitude=1.0,
        longitude=2.0,
        annotation_label="GR",
        redis_client=None,
    )
    params = _parse_snapshot_url(url)
    assert "annotations" in params
    assert "glyphText" in params["annotations"]
    assert "GR" in params["annotations"]


def test_build_snapshot_url_caches_and_returns_same_url() -> None:
    fake = _FakeRedis()
    first = service.build_snapshot_url(latitude=5.0, longitude=5.0, redis_client=fake)
    second = service.build_snapshot_url(latitude=5.0, longitude=5.0, redis_client=fake)
    # ECDSA signatures embed fresh randomness, so without the cache two
    # calls disagree. Equality proves the cache served the second call.
    assert first == second
    stored_ttl = next(iter(fake.expirations.values()))
    assert stored_ttl == 24 * 60 * 60


def test_build_snapshot_url_buckets_cache_by_zoom_and_size() -> None:
    fake = _FakeRedis()
    a = service.build_snapshot_url(
        latitude=0.0, longitude=0.0, zoom=12, redis_client=fake
    )
    b = service.build_snapshot_url(
        latitude=0.0, longitude=0.0, zoom=18, redis_client=fake
    )
    assert a != b
    assert len(fake.store) == 2


# ---------------------------------------------------------------------------
# fetch_nearby_poi
# ---------------------------------------------------------------------------


class _StubResponse:
    """Minimal requests-like response double used by the HTTP stub."""

    def __init__(
        self,
        *,
        status_code: int = 200,
        payload: Any = None,
        json_error: Exception | None = None,
    ) -> None:
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._payload = payload
        self._json_error = json_error

    def json(self) -> Any:
        if self._json_error is not None:
            raise self._json_error
        return self._payload


class _StubHttp:
    """Records every GET and replays canned responses in order."""

    def __init__(self, responses: list[_StubResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def get(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 5.0,
    ) -> _StubResponse:
        self.calls.append(
            {"url": url, "params": params or {}, "headers": headers or {}}
        )
        return self.responses.pop(0)


def _nearby_response() -> _StubResponse:
    """Build a stubbed ``/v1/search`` response with two in-range POIs.

    Includes a landmark outside both our category filter and the radius
    cap so the caller can assert both filters kicked in.
    """
    return _StubResponse(
        status_code=200,
        payload={
            "results": [
                {
                    "name": "Ben's Chili Bowl",
                    "poiCategory": "Restaurant",
                    "formattedAddressLines": ["1213 U St NW", "Washington, DC"],
                    "coordinate": {"latitude": 38.9173, "longitude": -77.0287},
                },
                {
                    "name": "The Gibson",
                    "poiCategory": "Bar",
                    "formattedAddressLines": ["2009 14th St NW"],
                    "coordinate": {"latitude": 38.9195, "longitude": -77.0319},
                },
                # Too far — gets filtered by the 400m radius cap.
                {
                    "name": "Union Station",
                    "poiCategory": "Landmark",
                    "formattedAddressLines": ["50 Massachusetts Ave NE"],
                    "coordinate": {"latitude": 38.8973, "longitude": -77.0063},
                },
            ]
        },
    )


def _nearby_responses(n: int) -> list[_StubResponse]:
    """Return ``n`` identical nearby stubs — one per category query."""
    return [_nearby_response() for _ in range(n)]


def _token_response() -> _StubResponse:
    """Canned ``/v1/token`` exchange response."""
    return _StubResponse(
        status_code=200,
        payload={"accessToken": "AT-123", "expiresInSeconds": 1800},
    )


def test_fetch_nearby_poi_exchanges_token_and_parses_results() -> None:
    http = _StubHttp([_token_response(), *_nearby_responses(3)])
    pois = service.fetch_nearby_poi(
        latitude=38.917,
        longitude=-77.032,
        http_client=http,
        redis_client=None,
    )
    # Results deduplicated across three per-category queries, then
    # sorted by ascending distance with the landmark dropped by both
    # the radius cap and the category filter.
    assert [poi["name"] for poi in pois] == ["The Gibson", "Ben's Chili Bowl"]
    distances = [poi["distance_m"] for poi in pois]
    assert distances == sorted(distances)
    assert all(d <= 400 for d in distances)
    # First request minted the token; the rest hit /v1/search.
    assert http.calls[0]["url"].endswith("/v1/token")
    search_calls = [c for c in http.calls if c["url"].endswith("/v1/search")]
    assert len(search_calls) == 3
    assert search_calls[0]["headers"]["Authorization"] == "Bearer AT-123"
    # Each category drives its own /v1/search call with the category
    # name (lowercased) as the query term.
    assert [c["params"]["q"] for c in search_calls] == ["restaurant", "bar", "cafe"]
    assert all("includePoiCategories" not in c["params"] for c in search_calls)
    assert search_calls[0]["params"]["searchLocation"] == "38.917000,-77.032000"


def test_fetch_nearby_poi_caches_access_token_in_redis() -> None:
    fake = _FakeRedis()
    # Two venues with default categories → 1 token + 3 + 3 search stubs.
    http = _StubHttp([_token_response(), *_nearby_responses(6)])
    service.fetch_nearby_poi(
        latitude=38.917,
        longitude=-77.032,
        http_client=http,
        redis_client=fake,
    )
    service.fetch_nearby_poi(
        latitude=38.920,
        longitude=-77.040,
        http_client=http,
        redis_client=fake,
    )
    token_calls = [c for c in http.calls if c["url"].endswith("/v1/token")]
    assert len(token_calls) == 1
    # Access token cached with a safety-margin TTL.
    assert fake.expirations["apple_maps:access_token:v1"] == 1800 - 60


def test_fetch_nearby_poi_serves_cached_results_without_http() -> None:
    fake = _FakeRedis()
    # 1 token + 3 per-category searches on the first call; the second
    # call hits the Redis cache and makes no HTTP requests at all.
    http = _StubHttp([_token_response(), *_nearby_responses(3)])
    first = service.fetch_nearby_poi(
        latitude=38.917,
        longitude=-77.032,
        http_client=http,
        redis_client=fake,
    )
    second = service.fetch_nearby_poi(
        latitude=38.917,
        longitude=-77.032,
        http_client=http,
        redis_client=fake,
    )
    assert first == second
    assert len(http.calls) == 4  # token + three category searches


def test_fetch_nearby_poi_raises_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPLE_MAPKIT_TEAM_ID", "")
    with pytest.raises(AppError) as exc:
        service.fetch_nearby_poi(latitude=0.0, longitude=0.0, redis_client=None)
    assert exc.value.code == APPLE_MAPS_UNAVAILABLE
    assert exc.value.status_code == 503


def test_fetch_nearby_poi_raises_when_apple_returns_error() -> None:
    http = _StubHttp([_token_response(), _StubResponse(status_code=500, payload={})])
    with pytest.raises(AppError) as exc:
        service.fetch_nearby_poi(
            latitude=38.917,
            longitude=-77.032,
            http_client=http,
            redis_client=None,
        )
    assert exc.value.status_code == 502
    assert exc.value.code == APPLE_MAPS_UNAVAILABLE


def test_fetch_nearby_poi_raises_when_token_exchange_rejected() -> None:
    http = _StubHttp([_StubResponse(status_code=401, payload={})])
    with pytest.raises(AppError) as exc:
        service.fetch_nearby_poi(
            latitude=38.917,
            longitude=-77.032,
            http_client=http,
            redis_client=None,
        )
    assert exc.value.status_code == 502
    assert exc.value.code == APPLE_MAPS_UNAVAILABLE


def test_fetch_nearby_poi_honors_custom_categories_and_limit() -> None:
    http = _StubHttp([_token_response(), _nearby_response()])
    pois = service.fetch_nearby_poi(
        latitude=38.917,
        longitude=-77.032,
        categories=("Bar",),
        limit=1,
        http_client=http,
        redis_client=None,
    )
    # Single category → single /v1/search call, and the category name
    # becomes the query term. Post-filter keeps only Bar records, limit
    # trims to one.
    assert [poi["name"] for poi in pois] == ["The Gibson"]
    search_calls = [c for c in http.calls if c["url"].endswith("/v1/search")]
    assert len(search_calls) == 1
    assert search_calls[0]["params"]["q"] == "bar"
    assert "includePoiCategories" not in search_calls[0]["params"]


def test_fetch_nearby_poi_drops_records_missing_coordinates() -> None:
    # Single category so the test only needs one /v1/search stub.
    http = _StubHttp(
        [
            _token_response(),
            _StubResponse(
                status_code=200,
                payload={
                    "results": [
                        {"name": "No coords", "poiCategory": "Bar"},
                        "not-a-dict",
                        {
                            "name": "OK",
                            "poiCategory": "Bar",
                            "coordinate": {"latitude": 38.917, "longitude": -77.032},
                            "formattedAddressLines": ["14th & V"],
                        },
                    ]
                },
            ),
        ]
    )
    pois = service.fetch_nearby_poi(
        latitude=38.917,
        longitude=-77.032,
        categories=("Bar",),
        http_client=http,
        redis_client=None,
    )
    assert [poi["name"] for poi in pois] == ["OK"]


# ---------------------------------------------------------------------------
# search_nearby_places — typed dataclass surface used by the map flows
# ---------------------------------------------------------------------------


def test_search_nearby_places_returns_dataclasses() -> None:
    """The typed wrapper hands back ``NearbyPlace`` objects, not dicts."""
    http = _StubHttp([_token_response(), *_nearby_responses(3)])
    places = service.search_nearby_places(
        latitude=38.917,
        longitude=-77.032,
        http_client=http,
        redis_client=None,
    )
    assert all(isinstance(p, service.NearbyPlace) for p in places)
    names = [p.name for p in places]
    assert names == ["The Gibson", "Ben's Chili Bowl"]
    assert places[0].distance_m <= 400


def test_search_nearby_places_propagates_radius_and_categories() -> None:
    """Custom radius/categories flow through to ``fetch_nearby_poi``."""
    http = _StubHttp([_token_response(), _nearby_response()])
    places = service.search_nearby_places(
        latitude=38.917,
        longitude=-77.032,
        categories=("Bar",),
        radius_m=600,
        limit=1,
        http_client=http,
        redis_client=None,
    )
    assert len(places) == 1
    # The single category drove a single /v1/search call with the
    # category name as the query term — Apple's /v1/search does not
    # honor includePoiCategories, so the filter is applied post hoc.
    search_calls = [c for c in http.calls if c["url"].endswith("/v1/search")]
    assert len(search_calls) == 1
    assert search_calls[0]["params"]["q"] == "bar"


def test_search_nearby_places_forwards_query_as_q() -> None:
    """A user-typed ``query`` is sent to Apple instead of post-filtered.

    When the tip form's autocomplete hands us a string, we trust Apple's
    ranking of that term rather than running per-category fan-out.
    """
    http = _StubHttp([_token_response(), _nearby_response()])
    places = service.search_nearby_places(
        latitude=38.917,
        longitude=-77.032,
        query="taco",
        http_client=http,
        redis_client=None,
    )
    search_calls = [c for c in http.calls if c["url"].endswith("/v1/search")]
    assert len(search_calls) == 1
    assert search_calls[0]["params"]["q"] == "taco"
    # With a query in play we don't restrict by category — Apple's
    # relevance ranking is the better judge.
    assert {p.name for p in places} >= {"The Gibson", "Ben's Chili Bowl"}


# ---------------------------------------------------------------------------
# verify_place_by_name / verify_place_by_address — geocoding gate
# ---------------------------------------------------------------------------


def _geocode_response(
    name: str = "Black Cat",
    address: str = "1811 14th St NW, Washington, DC",
    lat: float = 38.9152,
    lng: float = -77.0316,
) -> _StubResponse:
    """Build an Apple ``/v1/geocode`` style response with one result."""
    return _StubResponse(
        status_code=200,
        payload={
            "results": [
                {
                    "name": name,
                    "formattedAddressLines": [
                        s.strip() for s in address.split(",") if s.strip()
                    ],
                    "structuredAddress": {"fullThoroughfare": address},
                    "coordinate": {"latitude": lat, "longitude": lng},
                }
            ]
        },
    )


def _empty_geocode_response() -> _StubResponse:
    """Apple sometimes returns ``{"results": []}`` for unknown queries."""
    return _StubResponse(status_code=200, payload={"results": []})


def test_verify_place_by_name_accepts_close_match() -> None:
    """A 0.85+ similarity match returns a ``VerifiedPlace``."""
    http = _StubHttp([_token_response(), _geocode_response()])
    place = service.verify_place_by_name(
        query="Black Cat DC",
        near_latitude=38.917,
        near_longitude=-77.032,
        http_client=http,
        redis_client=None,
    )
    assert place is not None
    assert place.name == "Black Cat"
    assert place.latitude == 38.9152
    assert place.longitude == -77.0316


def test_verify_place_by_name_rejects_low_similarity() -> None:
    """Below the 0.80 cutoff the verifier returns None."""
    http = _StubHttp(
        [
            _token_response(),
            _geocode_response(name="Howard Theatre"),
        ]
    )
    place = service.verify_place_by_name(
        query="Black Cat",
        near_latitude=38.917,
        near_longitude=-77.032,
        http_client=http,
        redis_client=None,
    )
    assert place is None


def test_verify_place_by_name_returns_none_for_empty_results() -> None:
    """No results from Apple → no verified place."""
    http = _StubHttp([_token_response(), _empty_geocode_response()])
    place = service.verify_place_by_name(
        query="Bogus Cafe That Does Not Exist",
        near_latitude=38.917,
        near_longitude=-77.032,
        http_client=http,
        redis_client=None,
    )
    assert place is None


def test_verify_place_by_address_accepts_match() -> None:
    """A textual address that matches the canonical address verifies."""
    http = _StubHttp([_token_response(), _geocode_response()])
    place = service.verify_place_by_address(
        query="1811 14th St NW Washington DC",
        http_client=http,
        redis_client=None,
    )
    assert place is not None
    assert "14th St" in (place.address or "")


def test_verify_place_by_address_rejects_far_off_address() -> None:
    """Apple returning a different street drops below the gate."""
    http = _StubHttp(
        [
            _token_response(),
            _geocode_response(
                address="100 F St NE, Washington, DC",
            ),
        ]
    )
    place = service.verify_place_by_address(
        query="1811 14th St NW Washington DC",
        http_client=http,
        redis_client=None,
    )
    assert place is None


def test_verify_place_by_name_raises_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same configuration gate as the other Maps Server API helpers."""
    monkeypatch.setenv("APPLE_MAPKIT_TEAM_ID", "")
    with pytest.raises(AppError) as exc:
        service.verify_place_by_name(
            query="anything",
            near_latitude=0.0,
            near_longitude=0.0,
            redis_client=None,
        )
    assert exc.value.code == APPLE_MAPS_UNAVAILABLE


def test_verify_place_by_name_raises_on_apple_error() -> None:
    """Apple returning 500 on geocode bubbles up as a 502 AppError."""
    http = _StubHttp([_token_response(), _StubResponse(status_code=500, payload={})])
    with pytest.raises(AppError) as exc:
        service.verify_place_by_name(
            query="Black Cat",
            near_latitude=38.917,
            near_longitude=-77.032,
            http_client=http,
            redis_client=None,
        )
    assert exc.value.status_code == 502

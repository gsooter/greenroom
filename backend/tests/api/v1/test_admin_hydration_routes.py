"""Route tests for the admin hydration endpoints."""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest
from flask.testing import FlaskClient

from backend.api.v1 import admin as admin_route
from backend.core.config import get_settings


def _hdr() -> dict[str, str]:
    """Build the X-Admin-Key header for admin route tests.

    Returns:
        Header dict with the test admin secret.
    """
    return {"X-Admin-Key": get_settings().admin_secret_key}


def _stub_artist(artist_id: uuid.UUID, name: str = "Caamp") -> MagicMock:
    """Build a MagicMock that quacks like an :class:`Artist` row.

    Args:
        artist_id: UUID to attach to the stub.
        name: Display name to attach.

    Returns:
        A MagicMock with the attributes the route serializer needs.
    """
    artist = MagicMock()
    artist.id = artist_id
    artist.name = name
    artist.normalized_name = name.lower()
    artist.hydration_depth = 0
    artist.hydration_source = None
    artist.hydrated_from_artist_id = None
    artist.hydrated_at = None
    return artist


# ---------------------------------------------------------------------------
# Auth gating
# ---------------------------------------------------------------------------


def test_preview_endpoint_requires_admin_key(client: FlaskClient) -> None:
    resp = client.get(f"/api/v1/admin/artists/{uuid.uuid4()}/hydration-preview")
    assert resp.status_code == 401


def test_execute_endpoint_requires_admin_key(client: FlaskClient) -> None:
    resp = client.post(
        f"/api/v1/admin/artists/{uuid.uuid4()}/hydrate",
        json={"admin_email": "ops@x", "confirmed_candidates": []},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Preview endpoint
# ---------------------------------------------------------------------------


def test_preview_returns_payload(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    artist_id = uuid.uuid4()
    artist = _stub_artist(artist_id)

    from backend.services.artist_hydration import (
        HydrationCandidate,
        HydrationPreview,
    )

    preview = HydrationPreview(
        source_artist=artist,
        candidates=[
            HydrationCandidate(
                similar_artist_name="Mt. Joy",
                similar_artist_mbid=None,
                similarity_score=0.88,
                status="eligible",
                existing_artist_id=None,
            )
        ],
        eligible_count=1,
        would_add_count=1,
        daily_cap_remaining=99,
        can_proceed=True,
        blocking_reason=None,
    )
    monkeypatch.setattr(admin_route, "preview_hydration", lambda _s, _id: preview)

    resp = client.get(
        f"/api/v1/admin/artists/{artist_id}/hydration-preview", headers=_hdr()
    )
    assert resp.status_code == 200
    body = resp.get_json()["data"]
    assert body["source_artist"]["id"] == str(artist_id)
    assert body["eligible_count"] == 1
    assert body["would_add_count"] == 1
    assert body["daily_cap_remaining"] == 99
    assert body["can_proceed"] is True
    assert body["candidates"][0]["similar_artist_name"] == "Mt. Joy"
    assert body["candidates"][0]["status"] == "eligible"


def test_preview_returns_404_when_artist_missing(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(admin_route, "preview_hydration", lambda _s, _id: None)
    resp = client.get(
        f"/api/v1/admin/artists/{uuid.uuid4()}/hydration-preview", headers=_hdr()
    )
    assert resp.status_code == 404


def test_preview_validates_uuid(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    resp = client.get(
        "/api/v1/admin/artists/not-a-uuid/hydration-preview", headers=_hdr()
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Execute endpoint
# ---------------------------------------------------------------------------


def test_execute_returns_added_artist_metadata(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    artist_id = uuid.uuid4()
    new_id = uuid.uuid4()
    new_artist = _stub_artist(new_id, name="Mt. Joy")
    new_artist.hydration_depth = 1
    new_artist.hydration_source = "similar_artist"
    new_artist.hydrated_from_artist_id = artist_id

    from backend.services.artist_hydration import HydrationResult

    result = HydrationResult(
        source_artist_id=artist_id,
        added_artists=[new_artist],
        added_count=1,
        skipped_count=0,
        filtered_count=0,
        daily_cap_hit=False,
        blocking_reason=None,
    )
    captured: dict[str, Any] = {}

    def fake_execute(_s: Any, sid: uuid.UUID, **kw: Any) -> HydrationResult:
        captured["source"] = sid
        captured.update(kw)
        return result

    monkeypatch.setattr(admin_route, "execute_hydration", fake_execute)

    resp = client.post(
        f"/api/v1/admin/artists/{artist_id}/hydrate",
        json={
            "admin_email": "ops@greenroom.test",
            "confirmed_candidates": ["Mt. Joy"],
        },
        headers=_hdr(),
    )
    assert resp.status_code == 200
    body = resp.get_json()["data"]
    assert body["added_count"] == 1
    assert body["added_artists"][0]["name"] == "Mt. Joy"
    assert body["added_artists"][0]["hydration_depth"] == 1
    assert captured["admin_email"] == "ops@greenroom.test"
    assert captured["confirmed_candidates"] == ["Mt. Joy"]


def test_execute_rejects_missing_admin_email(client: FlaskClient) -> None:
    resp = client.post(
        f"/api/v1/admin/artists/{uuid.uuid4()}/hydrate",
        json={"confirmed_candidates": []},
        headers=_hdr(),
    )
    assert resp.status_code == 422


def test_execute_rejects_invalid_payload_shape(client: FlaskClient) -> None:
    resp = client.post(
        f"/api/v1/admin/artists/{uuid.uuid4()}/hydrate",
        data="not-json",
        headers=_hdr(),
    )
    assert resp.status_code == 422


def test_execute_returns_blocking_reason_on_no_op(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    artist_id = uuid.uuid4()
    from backend.services.artist_hydration import HydrationResult

    result = HydrationResult(
        source_artist_id=artist_id,
        added_artists=[],
        added_count=0,
        skipped_count=0,
        filtered_count=0,
        daily_cap_hit=False,
        blocking_reason="No artist found with id ...",
    )
    monkeypatch.setattr(admin_route, "execute_hydration", lambda *a, **kw: result)
    resp = client.post(
        f"/api/v1/admin/artists/{artist_id}/hydrate",
        json={
            "admin_email": "ops@greenroom.test",
            "confirmed_candidates": ["Anything"],
        },
        headers=_hdr(),
    )
    assert resp.status_code == 200
    body = resp.get_json()["data"]
    assert body["added_count"] == 0
    assert body["blocking_reason"].startswith("No artist found")

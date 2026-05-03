"""Tests for the public ``/api/v1/push/vapid-public-key`` endpoint.

The endpoint normalizes whatever the operator put in ``VAPID_PUBLIC_KEY``
(raw base64url or a SubjectPublicKeyInfo PEM) into the raw form the
browser's ``pushManager.subscribe`` expects. These tests pin that
normalization so a future regression — say, returning the env-var
value verbatim — surfaces immediately rather than at the next push
deploy.
"""

from __future__ import annotations

import base64

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from flask.testing import FlaskClient


def _generate_keypair() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def _public_pem(key: ec.EllipticCurvePrivateKey) -> str:
    return (
        key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )


def _expected_raw(key: ec.EllipticCurvePrivateKey) -> str:
    raw = key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def test_get_vapid_public_key_returns_empty_when_unset(
    client: FlaskClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dev parity: empty env → empty response so the prompt hides."""
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "")
    response = client.get("/api/v1/push/vapid-public-key")
    assert response.status_code == 200
    assert response.get_json() == {"data": {"public_key": ""}}


def test_get_vapid_public_key_passes_through_raw_base64url(
    client: FlaskClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = _generate_keypair()
    raw = _expected_raw(key)
    monkeypatch.setenv("VAPID_PUBLIC_KEY", raw)
    response = client.get("/api/v1/push/vapid-public-key")
    assert response.status_code == 200
    assert response.get_json() == {"data": {"public_key": raw}}


def test_get_vapid_public_key_converts_pem_to_raw(
    client: FlaskClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A PEM in the env var must come out as the browser-friendly raw form."""
    key = _generate_keypair()
    monkeypatch.setenv("VAPID_PUBLIC_KEY", _public_pem(key))
    response = client.get("/api/v1/push/vapid-public-key")
    assert response.status_code == 200
    body = response.get_json()
    assert body == {"data": {"public_key": _expected_raw(key)}}
    # Sanity: the returned value contains only base64url-safe chars and
    # is the expected ~88-char length for an uncompressed P-256 point.
    public = body["data"]["public_key"]
    assert all(c.isalnum() or c in "-_" for c in public)
    assert 86 <= len(public) <= 88


def test_get_vapid_public_key_returns_empty_when_pem_is_malformed(
    client: FlaskClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Garbled config logs an error and degrades to "push unavailable"
    rather than 500-ing every browser that polls for the key."""
    monkeypatch.setenv(
        "VAPID_PUBLIC_KEY",
        "-----BEGIN PUBLIC KEY-----\nNOT VALID BASE64\n-----END PUBLIC KEY-----",
    )
    response = client.get("/api/v1/push/vapid-public-key")
    assert response.status_code == 200
    assert response.get_json() == {"data": {"public_key": ""}}

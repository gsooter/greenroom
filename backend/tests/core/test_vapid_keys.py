"""Tests for VAPID key normalization.

The normalizer accepts either raw base64url or PEM-encoded keys and
always returns the raw base64url form Web Push expects. These tests
generate fresh ECDSA keypairs at runtime to keep the suite hermetic
(no committed key material) while still exercising the round-trip
with real cryptography library output.
"""

from __future__ import annotations

import base64

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from backend.core.vapid_keys import to_raw_private_key, to_raw_public_key


def _generate_ec_pair() -> ec.EllipticCurvePrivateKey:
    """Mint a fresh P-256 keypair for round-trip tests."""
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


def _private_pem_pkcs8(key: ec.EllipticCurvePrivateKey) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")


def _private_pem_sec1(key: ec.EllipticCurvePrivateKey) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode("ascii")


def _expected_raw_public(key: ec.EllipticCurvePrivateKey) -> str:
    raw = key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _expected_raw_private(key: ec.EllipticCurvePrivateKey) -> str:
    scalar = key.private_numbers().private_value.to_bytes(32, "big")
    return base64.urlsafe_b64encode(scalar).rstrip(b"=").decode("ascii")


def test_to_raw_public_key_passes_through_raw_base64url() -> None:
    key = _generate_ec_pair()
    raw = _expected_raw_public(key)
    assert to_raw_public_key(raw) == raw


def test_to_raw_public_key_strips_outer_whitespace_when_already_raw() -> None:
    key = _generate_ec_pair()
    raw = _expected_raw_public(key)
    assert to_raw_public_key(f"  {raw}\n") == raw


def test_to_raw_public_key_extracts_uncompressed_point_from_pem() -> None:
    """The most common production foot-gun: pasting an SPKI PEM."""
    key = _generate_ec_pair()
    pem = _public_pem(key)
    assert to_raw_public_key(pem) == _expected_raw_public(key)


def test_to_raw_public_key_handles_pem_with_trailing_whitespace() -> None:
    key = _generate_ec_pair()
    pem = _public_pem(key) + "\n   \n"
    assert to_raw_public_key(pem) == _expected_raw_public(key)


def test_to_raw_public_key_returns_empty_for_empty_input() -> None:
    """Dev environments leave the env var unset; preserve the no-op."""
    assert to_raw_public_key("") == ""
    assert to_raw_public_key("   \n") == ""


def test_to_raw_public_key_rejects_non_p256_curve() -> None:
    """Web Push only accepts P-256; surface a clear error otherwise."""
    key = ec.generate_private_key(ec.SECP384R1())
    pem = (
        key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )
    with pytest.raises(ValueError, match="P-256"):
        to_raw_public_key(pem)


def test_to_raw_public_key_rejects_non_ec_key() -> None:
    rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = (
        rsa_key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )
    with pytest.raises(ValueError, match="EC public key"):
        to_raw_public_key(pem)


def test_to_raw_private_key_passes_through_raw_base64url() -> None:
    key = _generate_ec_pair()
    raw = _expected_raw_private(key)
    assert to_raw_private_key(raw) == raw


def test_to_raw_private_key_extracts_scalar_from_pkcs8_pem() -> None:
    key = _generate_ec_pair()
    pem = _private_pem_pkcs8(key)
    assert to_raw_private_key(pem) == _expected_raw_private(key)


def test_to_raw_private_key_extracts_scalar_from_sec1_pem() -> None:
    """py-vapid emits SEC1 (-----BEGIN EC PRIVATE KEY-----) by default."""
    key = _generate_ec_pair()
    pem = _private_pem_sec1(key)
    assert to_raw_private_key(pem) == _expected_raw_private(key)


def test_to_raw_private_key_returns_empty_for_empty_input() -> None:
    assert to_raw_private_key("") == ""
    assert to_raw_private_key("\n  ") == ""


def test_to_raw_private_key_rejects_non_p256_curve() -> None:
    key = ec.generate_private_key(ec.SECP384R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    with pytest.raises(ValueError, match="P-256"):
        to_raw_private_key(pem)

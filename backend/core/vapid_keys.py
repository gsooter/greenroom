"""VAPID key normalization.

The Web Push spec wants the VAPID keys in a very specific shape: the
public key is the 65-byte uncompressed P-256 point (``0x04`` ‖ X ‖ Y)
base64url-encoded, and the private key is the 32-byte scalar
base64url-encoded. Tools like ``web-push generate-vapid-keys --json``
emit exactly that, but operators routinely paste PEM-formatted keys
from OpenSSL / py-vapid into deploy dashboards, which the browser's
``pushManager.subscribe`` then rejects as "invalid characters."

The two ``to_raw_*`` helpers below accept either form and always
return the raw base64url shape downstream consumers want. Calling
them is idempotent — already-raw input is returned unchanged after a
cheap whitespace strip.
"""

from __future__ import annotations

import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def _looks_like_pem(value: str) -> bool:
    """Cheap detector for PEM input.

    Returns True when ``value`` starts with a PEM armor line, which
    rules out raw base64url since ``-`` is the URL-safe alphabet's
    62nd character but ``-----BEGIN`` is unambiguous.

    Args:
        value: Whitespace-stripped candidate key string.

    Returns:
        True if the input looks PEM-encoded; False otherwise.
    """
    return value.startswith("-----BEGIN")


def _b64url_no_pad(raw: bytes) -> str:
    """Encode ``raw`` as base64url without ``=`` padding.

    The Web Push spec uses the JSON Web Signature flavor of base64url,
    which strips trailing ``=`` characters. Doing the strip here keeps
    callers from having to remember.

    Args:
        raw: Bytes to encode.

    Returns:
        URL-safe base64 string with no padding.
    """
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def to_raw_public_key(value: str) -> str:
    """Return the VAPID public key as raw base64url.

    Accepts either:
    * The raw uncompressed-point form already (passes through after
      a whitespace strip).
    * A SubjectPublicKeyInfo PEM (``-----BEGIN PUBLIC KEY-----``) —
      the body is decoded, the P-256 point extracted in uncompressed
      form, and re-encoded as base64url.

    Args:
        value: The configured ``VAPID_PUBLIC_KEY`` env-var contents.

    Returns:
        The 87- or 88-character base64url public key the browser's
        ``pushManager.subscribe`` expects. Empty string in, empty
        string out — used by callers that want to keep the
        "unconfigured in dev" no-op semantics.

    Raises:
        ValueError: If the PEM is malformed or contains a non-EC /
            non-P-256 key.
    """
    cleaned = value.strip()
    if not cleaned:
        return ""
    if not _looks_like_pem(cleaned):
        return cleaned
    public_key = serialization.load_pem_public_key(cleaned.encode("ascii"))
    if not isinstance(public_key, ec.EllipticCurvePublicKey):
        raise ValueError("VAPID_PUBLIC_KEY PEM is not an EC public key.")
    if not isinstance(public_key.curve, ec.SECP256R1):
        raise ValueError(
            "VAPID_PUBLIC_KEY PEM is not a P-256 (secp256r1) key — "
            "Web Push only accepts P-256.",
        )
    raw = public_key.public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    return _b64url_no_pad(raw)


def to_raw_private_key(value: str) -> str:
    """Return the VAPID private key as raw base64url.

    Accepts either:
    * The raw 32-byte-scalar base64url form (passes through after a
      whitespace strip).
    * A PKCS#8 PEM (``-----BEGIN PRIVATE KEY-----``) or SEC1 PEM
      (``-----BEGIN EC PRIVATE KEY-----``). The scalar is extracted
      and re-encoded as base64url.

    Args:
        value: The configured ``VAPID_PRIVATE_KEY`` env-var contents.

    Returns:
        The base64url-encoded scalar pywebpush expects. Empty in →
        empty out so the dispatcher's "no VAPID configured" branch
        keeps working in dev.

    Raises:
        ValueError: If the PEM is malformed or contains a non-EC /
            non-P-256 key.
    """
    cleaned = value.strip()
    if not cleaned:
        return ""
    if not _looks_like_pem(cleaned):
        return cleaned
    private_key = serialization.load_pem_private_key(
        cleaned.encode("ascii"),
        password=None,
    )
    if not isinstance(private_key, ec.EllipticCurvePrivateKey):
        raise ValueError("VAPID_PRIVATE_KEY PEM is not an EC private key.")
    if not isinstance(private_key.curve, ec.SECP256R1):
        raise ValueError(
            "VAPID_PRIVATE_KEY PEM is not a P-256 (secp256r1) key — "
            "Web Push only accepts P-256.",
        )
    scalar = private_key.private_numbers().private_value
    raw = scalar.to_bytes(32, "big")
    return _b64url_no_pad(raw)

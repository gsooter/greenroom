"""Apple Music connect — service skeleton.

Apple Music does **not** use the OAuth redirect pattern Spotify and
Tidal share. Instead:

1. The server mints a short-lived *developer token* — an ES256-signed
   JWT over the MusicKit .p8 private key — and hands it to the browser.
2. The browser runs MusicKit JS, which prompts the user for permission
   and in exchange hands back a long-lived *Music User Token* (MUT).
3. The browser POSTs the MUT to us; we validate it against Apple's
   API (any authenticated endpoint works; ``/v1/me/storefront`` is
   cheap) and persist it on a :class:`MusicServiceConnection` row.

Developer tokens are capped at 6 months but we mint them per-request
so they're always fresh enough for the browser to use for minutes.

**Pending credentials.** The skeleton below is wired end-to-end but
every function that touches Apple's API guards on :func:`is_configured`.
Until the Apple Developer Program approves the account and the
following env vars are populated, every public function here raises
``APPLE_MUSIC_AUTH_FAILED`` (503) so the route layer has a clear
no-op to surface:

* ``APPLE_MUSIC_TEAM_ID``
* ``APPLE_MUSIC_KEY_ID``
* ``APPLE_MUSIC_PRIVATE_KEY``     (PEM of the .p8)  or
  ``APPLE_MUSIC_PRIVATE_KEY_PATH`` (dev convenience — path to .p8)
* ``APPLE_MUSIC_BUNDLE_ID``

TODO(phase5): re-verify the storefront validation path
(``/v1/me/storefront``) against Apple's most current docs once the
end-to-end MusicKit flow has been smoke-tested in prod — the MusicKit
surface has historically churned on validation endpoints.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import jwt
import requests

from backend.core.config import get_settings
from backend.core.exceptions import APPLE_MUSIC_AUTH_FAILED, AppError
from backend.core.logging import get_logger

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from backend.data.models.users import User

logger = get_logger(__name__)

API_BASE = "https://api.music.apple.com/v1"
_DEV_TOKEN_TTL = timedelta(hours=12)
_DEV_TOKEN_ALGORITHM = "ES256"
_HTTP_TIMEOUT = 15.0


@dataclass(frozen=True)
class AppleMusicIdentity:
    """Minimal identity returned after validating a Music User Token.

    Apple Music does not hand back an account profile the way Spotify
    and Tidal do. The storefront response is the closest thing — it
    tells us *which* store the user is scoped to but not who they are.
    We use the MUT itself as the stable external identifier so we can
    still satisfy :class:`MusicServiceConnection.provider_user_id`.

    Attributes:
        provider_user_id: Opaque token we persist. Today this is a hash
            of the MUT (so we do not leak the raw MUT into logs or the
            provider-user-id index).
        storefront: Two-letter country code of the user's Apple Music
            storefront, e.g. ``"us"`` / ``"gb"``.
    """

    provider_user_id: str
    storefront: str


def is_configured() -> bool:
    """Return True when all Apple Developer credentials are populated.

    Returns:
        True if the team id, key id, bundle id, and a private key
        (inline or file) are all set. False otherwise — every other
        function in this module raises when this returns False.
    """
    settings = get_settings()
    has_key = bool(settings.apple_music_private_key) or bool(
        settings.apple_music_private_key_path
    )
    return bool(
        settings.apple_music_team_id
        and settings.apple_music_key_id
        and settings.apple_music_bundle_id
        and has_key
    )


def mint_developer_token(*, ttl: timedelta = _DEV_TOKEN_TTL) -> str:
    """Mint a short-lived ES256 developer token for MusicKit JS.

    Apple's documented max TTL is 6 months; we default to 12 hours so
    a leaked token is not useful for long. The frontend re-mints via
    ``GET /api/v1/auth/apple-music/developer-token`` on demand.

    Args:
        ttl: How long the token should be valid. Capped by Apple at
            6 months; we default to 12 hours.

    Returns:
        The encoded JWT, ready to be handed to MusicKit JS.

    Raises:
        AppError: ``APPLE_MUSIC_AUTH_FAILED`` (503) if Apple Music
            credentials are not configured, or (500) if the private
            key fails to load.
    """
    if not is_configured():
        raise AppError(
            code=APPLE_MUSIC_AUTH_FAILED,
            message="Apple Music is not configured on this environment.",
            status_code=503,
        )
    settings = get_settings()
    private_key = _load_private_key()
    now = datetime.now(UTC)
    claims = {
        "iss": settings.apple_music_team_id,
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
        "sub": settings.apple_music_bundle_id,
    }
    headers = {
        "alg": _DEV_TOKEN_ALGORITHM,
        "kid": settings.apple_music_key_id,
    }
    return jwt.encode(
        claims,
        private_key,
        algorithm=_DEV_TOKEN_ALGORITHM,
        headers=headers,
    )


def validate_music_user_token(music_user_token: str) -> AppleMusicIdentity:
    """Validate a MusicKit-issued Music User Token against Apple's API.

    Makes a single ``GET /v1/me/storefront`` call — any authenticated
    endpoint works; storefront is the cheapest. A non-2xx response
    means the MUT is stale, revoked, or forged.

    Args:
        music_user_token: Token returned to the browser by MusicKit JS.

    Returns:
        :class:`AppleMusicIdentity` describing the validated caller.

    Raises:
        AppError: ``APPLE_MUSIC_AUTH_FAILED`` if the MUT is rejected
            or the service is not configured.
    """
    developer_token = mint_developer_token()
    response = requests.get(
        f"{API_BASE}/me/storefront",
        headers={
            "Authorization": f"Bearer {developer_token}",
            "Music-User-Token": music_user_token,
        },
        timeout=_HTTP_TIMEOUT,
    )
    if response.status_code != 200:
        logger.warning(
            "Apple Music storefront check failed: %d %s",
            response.status_code,
            response.text[:500],
        )
        raise AppError(
            code=APPLE_MUSIC_AUTH_FAILED,
            message="Apple Music rejected the provided user token.",
            status_code=401,
        )
    body = response.json()
    data = body.get("data") if isinstance(body, dict) else None
    storefront = ""
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict):
            storefront = str(first.get("id", ""))
    return AppleMusicIdentity(
        provider_user_id=_hash_token(music_user_token),
        storefront=storefront,
    )


def get_library_artists(
    music_user_token: str,
    *,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Fetch the user's library artists from Apple Music, paginating.

    Apple caps ``/v1/me/library/artists`` at 100 items per call; we
    walk the offset in 100-item pages until we hit ``limit`` or a
    short page.

    TODO(signal-refinement): Apple Music also exposes listening data
    that we ignore today. When we revisit recommendation signals,
    fold in:

    * ``GET /v1/me/history/heavy-rotation`` — albums/playlists the
      user plays most; flatten to artists for a "top artists" parity
      with Spotify.
    * ``GET /v1/me/recent/played/tracks`` — last ~30 tracks; dedupe
      to artists for a "recently played" signal.

    The settings-page caption ("Uses artists saved in your library")
    needs to change in lockstep with this — update
    ``PROVIDER_SIGNAL_NOTE.apple_music`` in ``frontend/src/app/
    settings/page.tsx`` when the extra signals are wired in.

    Args:
        music_user_token: Valid Music User Token from MusicKit JS.
        limit: Total number of artists to return. Defaults to 200.

    Returns:
        List of raw Apple Music library-artist dicts (id + attributes).

    Raises:
        AppError: ``APPLE_MUSIC_AUTH_FAILED`` on HTTP failure.
    """
    developer_token = mint_developer_token()
    page_size = min(100, max(1, limit))
    collected: list[dict[str, Any]] = []
    offset = 0
    while len(collected) < limit:
        remaining = limit - len(collected)
        params: dict[str, str | int] = {
            "limit": min(page_size, remaining),
            "offset": offset,
        }
        response = requests.get(
            f"{API_BASE}/me/library/artists",
            params=params,
            headers={
                "Authorization": f"Bearer {developer_token}",
                "Music-User-Token": music_user_token,
            },
            timeout=_HTTP_TIMEOUT,
        )
        if response.status_code != 200:
            raise AppError(
                code=APPLE_MUSIC_AUTH_FAILED,
                message="Failed to fetch Apple Music library artists.",
                status_code=502,
            )
        body = response.json()
        items = body.get("data") if isinstance(body, dict) else None
        page = [item for item in (items or []) if isinstance(item, dict)]
        collected.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
    return collected[:limit]


def _simplify_artist(artist: dict[str, Any]) -> dict[str, Any]:
    """Reduce a raw Apple Music library-artist payload to the slim shape.

    Apple wraps library resources in ``{id, attributes}``. Artwork
    is a template URL with ``{w}``/``{h}`` placeholders we substitute
    so the frontend does not have to.

    Args:
        artist: Raw library-artist dict.

    Returns:
        Slim dict with id, name, empty genres list, and image_url.
    """
    artist_id = str(artist.get("id", ""))
    attrs = artist.get("attributes")
    if not isinstance(attrs, dict):
        attrs = {}
    name = attrs.get("name") or ""
    artwork = attrs.get("artwork")
    image_url: str | None = None
    if isinstance(artwork, dict):
        template = artwork.get("url")
        if isinstance(template, str) and template:
            image_url = template.replace("{w}", "256").replace("{h}", "256")
    return {
        "id": artist_id,
        "name": name if isinstance(name, str) else "",
        "genres": [],
        "image_url": image_url,
    }


def sync_top_artists(
    session: Session,
    user: User,
    *,
    music_user_token: str,
    limit: int = 200,
) -> int:
    """Pull the user's Apple Music library snapshot and persist it.

    Writes into the Apple-specific cache columns (``apple_top_*``) so
    Spotify and Tidal syncs do not clobber this user's Apple Music
    artists. The artist-match scorer unions across all three caches.

    Args:
        session: Active SQLAlchemy session.
        user: The user to sync.
        music_user_token: Music User Token issued by MusicKit JS.
        limit: Number of library artists to fetch.

    Returns:
        The number of artists persisted.

    Raises:
        AppError: ``APPLE_MUSIC_AUTH_FAILED`` on a Apple call failure.
    """
    raw = get_library_artists(music_user_token, limit=limit)
    simplified = [_simplify_artist(a) for a in raw if isinstance(a, dict)]
    user.apple_top_artist_ids = [a["id"] for a in simplified if a.get("id")]
    user.apple_top_artists = simplified
    user.apple_synced_at = datetime.now(UTC)
    session.flush()
    return len(simplified)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_private_key() -> str:
    """Return the .p8 private key contents, reading from disk if needed.

    Returns:
        The PEM-encoded private key as a string.

    Raises:
        AppError: ``APPLE_MUSIC_AUTH_FAILED`` (500) if neither the
            inline key nor the on-disk key can be loaded.
    """
    settings = get_settings()
    if settings.apple_music_private_key:
        return settings.apple_music_private_key
    path = settings.apple_music_private_key_path
    if path:
        try:
            return Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            raise AppError(
                code=APPLE_MUSIC_AUTH_FAILED,
                message="Failed to load Apple Music private key from disk.",
                status_code=500,
            ) from exc
    raise AppError(
        code=APPLE_MUSIC_AUTH_FAILED,
        message="Apple Music private key is not configured.",
        status_code=503,
    )


def _hash_token(token: str) -> str:
    """Produce a stable, non-reversible identifier for a Music User Token.

    Args:
        token: The raw Music User Token.

    Returns:
        A 64-char hex SHA-256 digest suitable for use as the
        ``provider_user_id`` column value. Stable across reconnects
        only while the MUT stays the same — Apple rotates MUTs when
        the user changes their password, which we handle as a
        reconnect (old row is orphaned, new row is created).
    """
    import hashlib

    return hashlib.sha256(token.encode("utf-8")).hexdigest()

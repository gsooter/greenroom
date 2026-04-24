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

**Runtime contract.** Every public function here guards on
:func:`is_configured`. When any of the following env vars is missing —
for example on a CI runner or a local dev box without real Apple
credentials — each call raises ``APPLE_MUSIC_AUTH_FAILED`` (503) so
the route layer has a clear no-op to surface rather than ES256-signing
with a bogus key:

* ``APPLE_MUSIC_TEAM_ID``
* ``APPLE_MUSIC_KEY_ID``
* ``APPLE_MUSIC_PRIVATE_KEY``     (PEM of the .p8)  or
  ``APPLE_MUSIC_PRIVATE_KEY_PATH`` (dev convenience — path to .p8)
* ``APPLE_MUSIC_BUNDLE_ID``
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

# Per-source affinity scores attached to every entry in
# ``users.apple_top_artists``. Heavy rotation is the strongest expressed
# taste signal Apple surfaces (Apple's own "most-played" bucket), recently
# played captures active listening, and the library is the weakest of the
# three because library membership can be years old and doesn't imply
# active interest. The scorer does not consume these yet — they exist
# so a future cross-provider affinity model (see DECISIONS entry for
# per-source affinity) can rank without another schema change.
SOURCE_HEAVY_ROTATION = "heavy_rotation"
SOURCE_RECENTLY_PLAYED = "recently_played"
SOURCE_LIBRARY = "library"
_SOURCE_AFFINITY: dict[str, float] = {
    SOURCE_HEAVY_ROTATION: 0.9,
    SOURCE_RECENTLY_PLAYED: 0.6,
    SOURCE_LIBRARY: 0.4,
}
# Upper bound for the recently-played tracks pull. Apple caps the
# endpoint at 30 items per call; we paginate to 100 to match the
# sprint-prompt target without hitting the ``offset`` ceiling.
_RECENT_DEFAULT_LIMIT = 100
_RECENT_PAGE_SIZE = 30
# Heavy rotation is a short list in practice (Apple surfaces ~10 items
# for a typical user); 50 is a safe ceiling.
_HEAVY_ROTATION_DEFAULT_LIMIT = 50
_HEAVY_ROTATION_PAGE_SIZE = 10


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

    Used as the *baseline* signal in :func:`sync_top_artists` alongside
    recently-played and heavy-rotation. See ``_SOURCE_AFFINITY`` for
    how library entries weigh against the stronger listening signals.

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


def get_recently_played_tracks(
    music_user_token: str,
    *,
    limit: int = _RECENT_DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    """Fetch the user's most recently played tracks from Apple Music.

    Apple paginates ``/v1/me/recent/played/tracks`` at 30 items per
    page; we walk the offset until we hit ``limit`` or Apple returns
    a short page. The raw track dicts are returned verbatim — callers
    use :func:`_artists_from_recently_played` to flatten them to a
    unique artist list.

    Args:
        music_user_token: Valid Music User Token from MusicKit JS.
        limit: Total number of tracks to return. Defaults to 100.

    Returns:
        List of raw Apple Music track dicts (id + attributes).

    Raises:
        AppError: ``APPLE_MUSIC_AUTH_FAILED`` on HTTP failure.
    """
    developer_token = mint_developer_token()
    collected: list[dict[str, Any]] = []
    offset = 0
    while len(collected) < limit:
        remaining = limit - len(collected)
        page_size = min(_RECENT_PAGE_SIZE, remaining)
        response = requests.get(
            f"{API_BASE}/me/recent/played/tracks",
            params={"limit": page_size, "offset": offset},
            headers={
                "Authorization": f"Bearer {developer_token}",
                "Music-User-Token": music_user_token,
            },
            timeout=_HTTP_TIMEOUT,
        )
        if response.status_code != 200:
            raise AppError(
                code=APPLE_MUSIC_AUTH_FAILED,
                message="Failed to fetch Apple Music recently-played tracks.",
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


def get_heavy_rotation(
    music_user_token: str,
    *,
    limit: int = _HEAVY_ROTATION_DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    """Fetch the user's Apple Music heavy-rotation content.

    Heavy rotation is a curated list of albums and playlists that Apple
    Music surfaces as the user's most-played content. The resources
    are typed (``albums``, ``playlists``) — callers flatten them to
    artists via :func:`_artists_from_heavy_rotation`.

    Args:
        music_user_token: Valid Music User Token from MusicKit JS.
        limit: Total number of resources to return. Defaults to 50.

    Returns:
        List of raw Apple Music heavy-rotation resource dicts.

    Raises:
        AppError: ``APPLE_MUSIC_AUTH_FAILED`` on HTTP failure.
    """
    developer_token = mint_developer_token()
    collected: list[dict[str, Any]] = []
    offset = 0
    while len(collected) < limit:
        remaining = limit - len(collected)
        page_size = min(_HEAVY_ROTATION_PAGE_SIZE, remaining)
        response = requests.get(
            f"{API_BASE}/me/history/heavy-rotation",
            params={"limit": page_size, "offset": offset},
            headers={
                "Authorization": f"Bearer {developer_token}",
                "Music-User-Token": music_user_token,
            },
            timeout=_HTTP_TIMEOUT,
        )
        if response.status_code != 200:
            raise AppError(
                code=APPLE_MUSIC_AUTH_FAILED,
                message="Failed to fetch Apple Music heavy rotation.",
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


def _artists_from_recently_played(
    tracks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Flatten recently-played tracks to a unique artist list.

    Apple's track payloads only carry the artist's display name
    (``attributes.artistName``), not a structured artist object with an
    id — the catalog artist id lives under ``relationships.artists`` and
    is not always expanded in the default response. We key dedupe on
    the lowercase name so repeat artists across multiple tracks
    collapse to a single entry, and synthesize a stable ``am:<hash>``
    id so downstream id-based lookups still work.

    Args:
        tracks: Raw track dicts as returned by
            :func:`get_recently_played_tracks`.

    Returns:
        List of slim artist dicts in first-seen order. Each dict has
        ``{id, name, genres: [], image_url}`` — the same shape as
        library artists.
    """
    import hashlib

    seen_names: set[str] = set()
    out: list[dict[str, Any]] = []
    for track in tracks:
        attrs = track.get("attributes") if isinstance(track, dict) else None
        if not isinstance(attrs, dict):
            continue
        name = attrs.get("artistName")
        if not isinstance(name, str) or not name.strip():
            continue
        key = name.strip().lower()
        if key in seen_names:
            continue
        seen_names.add(key)
        synthetic_id = "am:name:" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
        out.append(
            {
                "id": synthetic_id,
                "name": name.strip(),
                "genres": [
                    g for g in (attrs.get("genreNames") or []) if isinstance(g, str)
                ],
                "image_url": _extract_artwork_url(attrs.get("artwork")),
            }
        )
    return out


def _artists_from_heavy_rotation(
    resources: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Flatten heavy-rotation resources (albums, playlists) to artists.

    Heavy-rotation payloads come as a mixed list of albums and
    playlists. Albums carry an ``artistName`` on the resource itself.
    Playlists carry a ``curatorName`` that we deliberately skip —
    "Apple Music Indie" or "Morning Mix" is not a listening-history
    signal about a specific artist. Deduplication is the same as
    recently-played: lowercase name with a synthetic ``am:`` id.

    Args:
        resources: Raw Apple Music heavy-rotation resource dicts.

    Returns:
        List of slim artist dicts in first-seen order, same shape as
        library artists.
    """
    import hashlib

    seen_names: set[str] = set()
    out: list[dict[str, Any]] = []
    for resource in resources:
        if not isinstance(resource, dict):
            continue
        rtype = resource.get("type")
        attrs = resource.get("attributes")
        if not isinstance(attrs, dict):
            continue
        if rtype in {"albums", "library-albums"}:
            name = attrs.get("artistName")
        else:
            # Playlists and anything else: skip. A playlist's curator
            # is not a listening signal about a specific artist.
            continue
        if not isinstance(name, str) or not name.strip():
            continue
        key = name.strip().lower()
        if key in seen_names:
            continue
        seen_names.add(key)
        synthetic_id = "am:name:" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
        out.append(
            {
                "id": synthetic_id,
                "name": name.strip(),
                "genres": [
                    g for g in (attrs.get("genreNames") or []) if isinstance(g, str)
                ],
                "image_url": _extract_artwork_url(attrs.get("artwork")),
            }
        )
    return out


def _extract_artwork_url(artwork: Any) -> str | None:
    """Resolve the ``{w}``/``{h}`` placeholders in an Apple artwork URL.

    Args:
        artwork: The ``attributes.artwork`` object from an Apple Music
            resource, or any other value.

    Returns:
        A concrete artwork URL sized to 256x256, or ``None`` when the
        artwork object is missing or malformed.
    """
    if not isinstance(artwork, dict):
        return None
    template = artwork.get("url")
    if not isinstance(template, str) or not template:
        return None
    return template.replace("{w}", "256").replace("{h}", "256")


def _merge_signals(
    *,
    library: list[dict[str, Any]],
    recently_played: list[dict[str, Any]],
    heavy_rotation: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge per-source artist lists into a single ranked artist list.

    Dedupe key precedence:
    1. ``id`` when it is a real library id (starts with ``l.``) — those
       ids are stable across calls and services.
    2. Normalized ``name`` (lowercased + stripped) — the synthetic
       ``am:`` ids produced from recently-played/heavy-rotation names
       collide on this key, which is exactly what we want so a
       heavy-rotation artist that also appears in the library collapses.

    When an artist appears in multiple sources we keep the entry with
    the highest :data:`_SOURCE_AFFINITY` score, but merge in the other
    source's genre list so the library/recently-played ``genres`` can
    enrich a heavy-rotation entry that happened to be genre-less.

    Every returned entry carries two extra fields vs. the raw
    :func:`_simplify_artist` shape: ``source`` (one of the
    ``SOURCE_*`` constants) and ``affinity_score`` (the matching
    :data:`_SOURCE_AFFINITY` value). The artist-match scorer ignores
    them for now; they exist so a later unified affinity model (see
    DECISIONS) can rank without another schema change.

    Args:
        library: Simplified library-artist dicts.
        recently_played: Simplified recently-played artist dicts.
        heavy_rotation: Simplified heavy-rotation artist dicts.

    Returns:
        Merged artist list ordered by affinity score descending, with
        ties broken by source (heavy-rotation > recently-played >
        library) and by first-seen order within each source.
    """
    tagged: list[tuple[str, dict[str, Any]]] = []
    for source, entries in (
        (SOURCE_HEAVY_ROTATION, heavy_rotation),
        (SOURCE_RECENTLY_PLAYED, recently_played),
        (SOURCE_LIBRARY, library),
    ):
        for entry in entries:
            tagged.append((source, entry))

    by_key: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for source, entry in tagged:
        name = entry.get("name") or ""
        normalized = name.strip().lower()
        if not normalized:
            continue
        key = f"name:{normalized}"
        affinity = _SOURCE_AFFINITY[source]
        if key not in by_key:
            merged = {
                **entry,
                "source": source,
                "affinity_score": affinity,
            }
            by_key[key] = merged
            order.append(key)
            continue
        existing = by_key[key]
        # Genre backfill — a heavy-rotation album entry often carries
        # richer genres than a library entry, or vice versa. Union them.
        existing_genres = list(existing.get("genres") or [])
        for genre in entry.get("genres") or []:
            if genre not in existing_genres:
                existing_genres.append(genre)
        # Prefer a real Apple library id (``l.*``) over a synthetic
        # ``am:name:*`` id — the library id is stable across sessions
        # and is what lets the frontend fetch catalog metadata later.
        existing_id = existing.get("id") or ""
        incoming_id = entry.get("id") or ""
        if (
            isinstance(incoming_id, str)
            and incoming_id.startswith("l.")
            and not (isinstance(existing_id, str) and existing_id.startswith("l."))
        ):
            existing["id"] = incoming_id
        if affinity > existing.get("affinity_score", 0.0):
            # Upgrade to the stronger source, but preserve the unioned
            # genres and the stable id we just resolved.
            preserved_id = existing.get("id")
            existing.update(entry)
            existing["id"] = preserved_id
            existing["source"] = source
            existing["affinity_score"] = affinity
        existing["genres"] = existing_genres

    merged_list = [by_key[k] for k in order]
    merged_list.sort(key=lambda a: -float(a.get("affinity_score", 0.0)))
    return merged_list


def sync_top_artists(
    session: Session,
    user: User,
    *,
    music_user_token: str,
    limit: int = 200,
    recent_limit: int = _RECENT_DEFAULT_LIMIT,
    heavy_rotation_limit: int = _HEAVY_ROTATION_DEFAULT_LIMIT,
) -> int:
    """Pull the user's Apple Music listening snapshot and persist it.

    Fetches three signals in one call:

    * Library artists — the breadth of what the user has saved.
    * Recently-played tracks (flattened to artists) — active listening.
    * Heavy rotation (albums flattened to artists) — dominant taste.

    The three lists are merged by :func:`_merge_signals`: dedupe by
    library id or normalized artist name, union genres, and stamp each
    entry with a ``source`` and ``affinity_score`` so a future unified
    affinity model (see DECISIONS) can rank without another schema
    change. The persisted list is ordered affinity-descending so the
    scorer's first-write-wins dedupe (see
    :class:`backend.recommendations.scorers.artist_match.ArtistMatchScorer`)
    keeps the strongest signal.

    If the recently-played or heavy-rotation endpoints fail (502 from
    Apple, rate limiting, or a transient stale-MUT error specifically on
    those endpoints), the failure is logged and the sync still persists
    whatever signals did succeed — degraded data is strictly better than
    no sync at all. The library fetch remains load-bearing; its failure
    still propagates.

    Args:
        session: Active SQLAlchemy session.
        user: The user to sync.
        music_user_token: Music User Token issued by MusicKit JS.
        limit: Number of library artists to fetch.
        recent_limit: Number of recently-played tracks to pull before
            flattening to unique artists. Defaults to 100.
        heavy_rotation_limit: Number of heavy-rotation resources to
            pull before flattening. Defaults to 50.

    Returns:
        The number of artists persisted after merging all three sources.

    Raises:
        AppError: ``APPLE_MUSIC_AUTH_FAILED`` on a library-fetch failure.
            Recently-played and heavy-rotation failures are swallowed
            and logged.
    """
    raw_library = get_library_artists(music_user_token, limit=limit)
    library = [_simplify_artist(a) for a in raw_library if isinstance(a, dict)]

    try:
        raw_recent = get_recently_played_tracks(music_user_token, limit=recent_limit)
    except AppError:
        logger.warning(
            "apple_music_recently_played_fetch_failed",
            extra={"user_id": str(user.id)},
        )
        raw_recent = []
    recently_played = _artists_from_recently_played(raw_recent)

    try:
        raw_heavy = get_heavy_rotation(music_user_token, limit=heavy_rotation_limit)
    except AppError:
        logger.warning(
            "apple_music_heavy_rotation_fetch_failed",
            extra={"user_id": str(user.id)},
        )
        raw_heavy = []
    heavy_rotation = _artists_from_heavy_rotation(raw_heavy)

    merged = _merge_signals(
        library=library,
        recently_played=recently_played,
        heavy_rotation=heavy_rotation,
    )
    user.apple_top_artist_ids = [a["id"] for a in merged if a.get("id")]
    user.apple_top_artists = merged
    user.apple_synced_at = datetime.now(UTC)
    session.flush()
    return len(merged)


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

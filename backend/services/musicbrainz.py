"""MusicBrainz client and match-selection logic.

MusicBrainz is the first genre enrichment source layered on top of
Spotify (Decision 056). The free, community-curated database has
broader long-tail coverage than Spotify and ships ``genres`` (curated)
plus ``tags`` (free-form) on every artist.

Two endpoints are wrapped here:

* :func:`search_musicbrainz_artist` — ``GET /artist?query=...`` returns
  up to 5 candidate matches with MBIDs and a relevance score.
* :func:`fetch_artist_details` — ``GET /artist/{mbid}?inc=genres+tags``
  returns the full artist record. We pull both ``genres`` and ``tags``
  blobs and store them verbatim so a later normalization sprint can
  weight them.

:func:`find_best_match` blends MusicBrainz's own relevance score with
``SequenceMatcher`` name similarity. The 50/50 weighting matters because
MusicBrainz often returns *something* with a non-trivial score even for
nonsense queries, and name similarity is the cheaper sanity check.

Etiquette: MusicBrainz documents a 1 req/sec/IP rate limit and asks
clients to send a descriptive User-Agent. Pacing is enforced at the
task layer (:mod:`backend.services.musicbrainz_tasks`); this module
just carries the User-Agent on every request.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

import requests

from backend.core.logging import get_logger
from backend.core.text import normalize_artist_name

logger = get_logger(__name__)

API_BASE = "https://musicbrainz.org/ws/2"
USER_AGENT = "Greenroom/1.0 (greenroom_support@gstwentyseven.com)"
HTTP_TIMEOUT = 15.0
SEARCH_LIMIT = 5
CONFIDENCE_THRESHOLD = 0.75
# MusicBrainz returns a score in 0-100; 100 is treated as a perfect
# match. Normalize to 0-1 for the weighted blend.
_MB_SCORE_MAX = 100.0


class MusicBrainzAPIError(Exception):
    """Raised on any non-2xx response from the MusicBrainz API.

    The Celery task layer treats this as retryable (503/504/connection
    errors are by far the most common cause) but logs the underlying
    HTTP status so on-call can distinguish a true outage from a one-off.

    Attributes:
        status_code: HTTP status returned by MusicBrainz, or 0 when the
            error happened before a response was received (e.g. DNS,
            connection reset).
    """

    def __init__(self, message: str, *, status_code: int = 0) -> None:
        """Initialize a MusicBrainzAPIError.

        Args:
            message: Human-readable description of the failure.
            status_code: HTTP status code returned by MusicBrainz, or 0
                when no response was received.
        """
        super().__init__(message)
        self.status_code = status_code


class MusicBrainzNotFoundError(Exception):
    """Raised when an MBID lookup returns 404.

    Distinct from :class:`MusicBrainzAPIError` so the task layer can
    treat 404 as "no record" and mark the artist enriched without
    retrying.
    """


@dataclass(frozen=True)
class MusicBrainzCandidate:
    """One artist from a MusicBrainz search result.

    Attributes:
        mbid: MusicBrainz ID (UUID string) for the artist.
        name: Artist display name as MusicBrainz has it.
        score: MusicBrainz relevance score (0-100).
        disambiguation: Short clarifying note when the same name belongs
            to multiple artists ("US singer", "UK band"), else None.
        country: ISO country code, if known.
        type: Entity subtype — typically "Person" or "Group" — or None.
    """

    mbid: str
    name: str
    score: int
    disambiguation: str | None
    country: str | None
    type: str | None


@dataclass(frozen=True)
class MusicBrainzArtistDetails:
    """Full artist payload pulled by MBID.

    Attributes:
        mbid: MusicBrainz ID echoed back by the API.
        name: Artist display name.
        genres: Raw ``genres`` list with ``name`` and ``count`` fields
            preserved. Empty list when the artist has no curated genres.
        tags: Raw ``tags`` list with ``name`` and ``count`` fields
            preserved. Empty list when the artist has no tags.
    """

    mbid: str
    name: str
    genres: list[dict[str, Any]]
    tags: list[dict[str, Any]]


def _request_json(
    path: str,
    params: dict[str, Any],
    *,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    """Perform a GET against the MusicBrainz API and return parsed JSON.

    Args:
        path: API path beginning with a slash (e.g. ``/artist``).
        params: Query parameters. ``fmt=json`` is added automatically.
        session: Optional ``requests.Session`` for HTTP injection in
            tests. Falls back to ``requests`` module-level functions.

    Returns:
        The decoded JSON payload as a dict.

    Raises:
        MusicBrainzNotFoundError: Server returned 404.
        MusicBrainzAPIError: Any other non-2xx response or transport
            error.
    """
    url = f"{API_BASE}{path}"
    query = {**params, "fmt": "json"}
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    http = session if session is not None else requests
    try:
        response = http.get(
            url,
            params=query,
            headers=headers,
            timeout=HTTP_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise MusicBrainzAPIError(
            f"MusicBrainz request failed: {exc}",
            status_code=0,
        ) from exc

    if response.status_code == 404:
        raise MusicBrainzNotFoundError(f"MusicBrainz returned 404 for {path}")
    if response.status_code >= 400:
        raise MusicBrainzAPIError(
            (f"MusicBrainz returned {response.status_code} for {path}"),
            status_code=response.status_code,
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise MusicBrainzAPIError(
            f"MusicBrainz returned non-JSON body for {path}",
            status_code=response.status_code,
        ) from exc
    if not isinstance(payload, dict):
        raise MusicBrainzAPIError(
            f"MusicBrainz returned non-object body for {path}",
            status_code=response.status_code,
        )
    return payload


def search_musicbrainz_artist(
    artist_name: str,
    *,
    session: requests.Session | None = None,
) -> list[MusicBrainzCandidate]:
    """Search MusicBrainz for candidate artists matching ``artist_name``.

    Args:
        artist_name: Raw scraper artist name. Whitespace is stripped
            before sending. An empty result is returned for blank names
            without hitting the API.
        session: Optional ``requests.Session`` used by tests for HTTP
            injection.

    Returns:
        Up to :data:`SEARCH_LIMIT` candidates ranked by MusicBrainz's
        relevance score. Empty list when MusicBrainz has no matches.

    Raises:
        MusicBrainzAPIError: MusicBrainz returned a non-2xx response or
            the request itself failed.
    """
    cleaned = artist_name.strip()
    if not cleaned:
        return []

    payload = _request_json(
        "/artist",
        {
            "query": f"artist:{cleaned}",
            "limit": SEARCH_LIMIT,
        },
        session=session,
    )
    raw_candidates = payload.get("artists") or []
    if not isinstance(raw_candidates, list):
        return []

    out: list[MusicBrainzCandidate] = []
    for entry in raw_candidates:
        if not isinstance(entry, dict):
            continue
        mbid = entry.get("id")
        name = entry.get("name")
        if not isinstance(mbid, str) or not isinstance(name, str):
            continue
        if not mbid.strip() or not name.strip():
            continue
        score_raw = entry.get("score", 0)
        try:
            score = int(score_raw)
        except (TypeError, ValueError):
            score = 0
        disambiguation = entry.get("disambiguation")
        country = entry.get("country")
        artist_type = entry.get("type")
        out.append(
            MusicBrainzCandidate(
                mbid=mbid.strip(),
                name=name.strip(),
                score=score,
                disambiguation=(
                    disambiguation
                    if isinstance(disambiguation, str) and disambiguation.strip()
                    else None
                ),
                country=(
                    country if isinstance(country, str) and country.strip() else None
                ),
                type=(
                    artist_type
                    if isinstance(artist_type, str) and artist_type.strip()
                    else None
                ),
            )
        )
    return out


def fetch_artist_details(
    mbid: str,
    *,
    session: requests.Session | None = None,
) -> MusicBrainzArtistDetails:
    """Fetch full artist details for an MBID, including genres and tags.

    Args:
        mbid: MusicBrainz artist ID (UUID-shaped string).
        session: Optional ``requests.Session`` used by tests.

    Returns:
        A :class:`MusicBrainzArtistDetails` carrying the raw ``genres``
        and ``tags`` payloads. Each entry preserves ``name`` and
        ``count`` so downstream normalization can weight by votes.

    Raises:
        MusicBrainzNotFoundError: ``mbid`` does not exist in
            MusicBrainz.
        MusicBrainzAPIError: Any other API or transport error.
    """
    payload = _request_json(
        f"/artist/{mbid}",
        {"inc": "genres+tags"},
        session=session,
    )
    name = payload.get("name", "") or ""
    genres = _clean_tag_list(payload.get("genres"))
    tags = _clean_tag_list(payload.get("tags"))
    return MusicBrainzArtistDetails(
        mbid=mbid,
        name=name,
        genres=genres,
        tags=tags,
    )


def _clean_tag_list(raw: object) -> list[dict[str, Any]]:
    """Filter a MusicBrainz tag/genre array down to the fields we keep.

    MusicBrainz returns objects with ``name``, ``count``, and sometimes
    other metadata. We keep ``name`` and ``count`` only — anything
    further is noise for a future normalization sprint and would just
    grow the JSONB blob.

    Args:
        raw: The unparsed value at ``payload["genres"]`` or
            ``payload["tags"]``. Often a list, but defensively typed as
            ``object`` since payloads from third-party APIs lie.

    Returns:
        List of ``{"name": str, "count": int}`` dicts. Empty list when
        the input is missing, malformed, or contains no usable entries.
    """
    if not isinstance(raw, list):
        return []
    cleaned: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        count_raw = entry.get("count", 0)
        try:
            count = int(count_raw)
        except (TypeError, ValueError):
            count = 0
        cleaned.append({"name": name.strip(), "count": count})
    return cleaned


def _name_similarity(target: str, candidate: str) -> float:
    """Return a 0-1 ``SequenceMatcher`` ratio on normalized names.

    Both inputs run through :func:`normalize_artist_name` (the same
    primitive ingestion uses) so case and diacritics never tip the
    score one way or the other.

    Args:
        target: The scraped artist name we're trying to match.
        candidate: A candidate name returned by MusicBrainz.

    Returns:
        Similarity ratio in 0.0-1.0. 0.0 when either side normalizes
        to an empty string.
    """
    target_key = normalize_artist_name(target)
    candidate_key = normalize_artist_name(candidate)
    if not target_key or not candidate_key:
        return 0.0
    return SequenceMatcher(None, target_key, candidate_key).ratio()


def find_best_match(
    artist_name: str,
    candidates: list[MusicBrainzCandidate],
) -> tuple[MusicBrainzCandidate, float] | None:
    """Pick the most plausible MusicBrainz candidate for ``artist_name``.

    Confidence is the equal-weighted average of MusicBrainz's own
    relevance score (normalized to 0-1) and a ``SequenceMatcher`` ratio
    on normalized names. A 50/50 split prevents either signal from
    dominating: MusicBrainz can be over-confident on partial matches,
    and pure name similarity ignores all the disambiguation work the
    MusicBrainz community has already done.

    Args:
        artist_name: The scraper's artist name, before normalization.
        candidates: Output of :func:`search_musicbrainz_artist`.

    Returns:
        A ``(candidate, confidence)`` tuple when at least one candidate
        clears :data:`CONFIDENCE_THRESHOLD`, else None.
    """
    if not candidates:
        return None

    best: MusicBrainzCandidate | None = None
    best_confidence = 0.0
    for candidate in candidates:
        mb_score = max(0.0, min(candidate.score / _MB_SCORE_MAX, 1.0))
        name_score = _name_similarity(artist_name, candidate.name)
        confidence = 0.5 * mb_score + 0.5 * name_score
        if confidence > best_confidence:
            best_confidence = confidence
            best = candidate

    if best is None or best_confidence < CONFIDENCE_THRESHOLD:
        return None
    return best, best_confidence

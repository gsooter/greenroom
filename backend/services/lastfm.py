"""Last.fm client and match-selection logic.

Last.fm is the second genre enrichment source (Decision 057), layered
alongside MusicBrainz (Decision 056). Where MusicBrainz contributes
curated genres + tags, Last.fm contributes user-applied tags ordered
by popularity — often more current and more granular than label
taxonomies.

Three endpoints are wrapped here, all under ``ws.audioscrobbler.com/2.0``:

* :func:`search_lastfm_artist` — ``method=artist.search`` returns up to
  5 candidate matches with names, MBIDs (when known), listener counts,
  and Last.fm URLs.
* :func:`fetch_artist_info_by_name` — ``method=artist.getInfo&artist=...
  &autocorrect=1``. Last.fm silently corrects minor casing/spelling
  variations (e.g. "phoebe bridgers" -> "Phoebe Bridgers") before
  responding, which is exactly what we want for noisy scraper names.
* :func:`fetch_artist_info_by_mbid` — ``method=artist.getInfo&mbid=...``.
  Used when Sprint 1A populated ``musicbrainz_id`` on the artist; the
  MBID lookup is exact and bypasses fuzzy name matching entirely.

:func:`find_best_match` blends a 70/30 weighted average of name
similarity (``SequenceMatcher`` on normalized names) and listener-count
percentile across the returned candidates. Listener count is the key
disambiguation signal Last.fm exposes — the right artist almost always
has more listeners than wrong matches with similar names.

Etiquette: Last.fm allows 5 req/sec/key but their guidelines suggest
staying well below. Pacing is enforced at the task layer
(:mod:`backend.services.lastfm_tasks`) at 4 req/sec; this module just
carries the User-Agent on every request.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

import requests

from backend.core.config import get_settings
from backend.core.logging import get_logger
from backend.core.text import normalize_artist_name

logger = get_logger(__name__)

API_BASE = "https://ws.audioscrobbler.com/2.0/"
USER_AGENT = "Greenroom/1.0 (greenroom_support@gstwentyseven.com)"
HTTP_TIMEOUT = 15.0
SEARCH_LIMIT = 5
CONFIDENCE_THRESHOLD = 0.75
# 70% name similarity, 30% listener-count percentile. Name dominates
# because Last.fm sometimes returns popular but wrong artists when the
# query is a substring of a real name; listener count is the tiebreaker
# between candidates whose names are similarly close to the query.
_NAME_WEIGHT = 0.7
_LISTENER_WEIGHT = 0.3
# Last.fm's "artist not found" error code on getInfo responses.
_ERROR_NOT_FOUND = 6


class LastFMAPIError(Exception):
    """Raised on any non-2xx response from the Last.fm API.

    The Celery task layer treats this as retryable (503/429/connection
    errors are by far the most common cause) but logs the underlying
    HTTP status so on-call can distinguish a true outage from a one-off.

    Attributes:
        status_code: HTTP status returned by Last.fm, or 0 when the
            error happened before a response was received (e.g. DNS,
            connection reset).
    """

    def __init__(self, message: str, *, status_code: int = 0) -> None:
        """Initialize a LastFMAPIError.

        Args:
            message: Human-readable description of the failure.
            status_code: HTTP status code returned by Last.fm, or 0
                when no response was received.
        """
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class LastFMCandidate:
    """One artist from a Last.fm ``artist.search`` result.

    Attributes:
        name: Artist display name as Last.fm has it.
        mbid: MusicBrainz ID when Last.fm has linked one to the artist,
            else None. Last.fm returns an empty string when none is
            known; we normalize that to None.
        listener_count: Number of unique Last.fm listeners — used as a
            popularity signal for disambiguation.
        url: Canonical Last.fm artist page URL.
    """

    name: str
    mbid: str | None
    listener_count: int
    url: str


@dataclass(frozen=True)
class LastFMArtistInfo:
    """Full artist payload returned by ``artist.getInfo``.

    Attributes:
        name: Artist display name (post-autocorrect when applicable).
        mbid: MusicBrainz ID known to Last.fm, or None.
        listener_count: Total unique listeners.
        url: Canonical Last.fm artist page URL.
        tags: Raw tag objects with ``name`` and ``url`` fields,
            preserving Last.fm's popularity ordering.
        bio_summary: Short bio blurb. Stored even though we don't use
            it yet because Last.fm returns it for free; saves a
            re-enrichment pass when we want to show artist bios later.
    """

    name: str
    mbid: str | None
    listener_count: int
    url: str
    tags: list[dict[str, Any]]
    bio_summary: str | None


def _get_api_key() -> str:
    """Return the configured Last.fm API key.

    Wrapped so tests can monkeypatch it without going through the full
    pydantic settings load.

    Returns:
        The configured API key. Empty string when unset, in which case
        the request will fail at the API layer with 403 — surfaced to
        the caller as :class:`LastFMAPIError`.
    """
    return get_settings().lastfm_api_key


def _request_json(
    params: dict[str, Any],
    *,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    """Perform a GET against the Last.fm API and return parsed JSON.

    Args:
        params: Query parameters. ``api_key`` and ``format=json`` are
            added automatically.
        session: Optional ``requests.Session`` for HTTP injection in
            tests. Falls back to ``requests`` module-level functions.

    Returns:
        The decoded JSON payload as a dict.

    Raises:
        LastFMAPIError: Any non-2xx response or transport error.
    """
    query = {**params, "api_key": _get_api_key(), "format": "json"}
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    http = session if session is not None else requests
    try:
        response = http.get(
            API_BASE,
            params=query,
            headers=headers,
            timeout=HTTP_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise LastFMAPIError(
            f"Last.fm request failed: {exc}",
            status_code=0,
        ) from exc

    if response.status_code >= 400:
        raise LastFMAPIError(
            f"Last.fm returned {response.status_code}",
            status_code=response.status_code,
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise LastFMAPIError(
            "Last.fm returned non-JSON body",
            status_code=response.status_code,
        ) from exc
    if not isinstance(payload, dict):
        raise LastFMAPIError(
            "Last.fm returned non-object body",
            status_code=response.status_code,
        )
    return payload


def _coerce_int(raw: object) -> int:
    """Best-effort int coercion that defaults to zero on failure.

    Last.fm returns numeric fields as strings (``"523412"``) and we want
    integers. Bad values stay at zero rather than blowing up the parse.

    Args:
        raw: The candidate value (string, int, or anything else).

    Returns:
        The coerced integer, or 0 when ``raw`` cannot be parsed.
    """
    if isinstance(raw, bool):
        return int(raw)
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        try:
            return int(raw)
        except ValueError:
            return 0
    return 0


def _as_list(raw: object) -> list[Any]:
    """Normalize Last.fm fields that may be ``[a, b]`` or just ``a``.

    Last.fm collapses single-element lists down to bare objects in many
    response shapes (``tag``, ``artist``, etc.). This helper restores
    the list form so callers can always iterate.

    Args:
        raw: The field value as returned by Last.fm.

    Returns:
        A list — empty when ``raw`` is None, ``[raw]`` when ``raw`` is a
        single dict, or ``raw`` itself when it's already a list.
    """
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        return [raw]
    return []


def search_lastfm_artist(
    artist_name: str,
    *,
    session: requests.Session | None = None,
) -> list[LastFMCandidate]:
    """Search Last.fm for candidate artists matching ``artist_name``.

    Args:
        artist_name: Raw scraper artist name. Whitespace is stripped
            before sending. An empty result is returned for blank names
            without hitting the API.
        session: Optional ``requests.Session`` used by tests for HTTP
            injection.

    Returns:
        Up to :data:`SEARCH_LIMIT` candidates in Last.fm's relevance
        order. Empty list when Last.fm has no matches.

    Raises:
        LastFMAPIError: Last.fm returned a non-2xx response or the
            request itself failed.
    """
    cleaned = artist_name.strip()
    if not cleaned:
        return []

    payload = _request_json(
        {
            "method": "artist.search",
            "artist": cleaned,
            "limit": SEARCH_LIMIT,
        },
        session=session,
    )
    results = payload.get("results")
    if not isinstance(results, dict):
        return []
    matches = results.get("artistmatches")
    if not isinstance(matches, dict):
        return []
    raw_artists = _as_list(matches.get("artist"))

    out: list[LastFMCandidate] = []
    for entry in raw_artists:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        mbid_raw = entry.get("mbid")
        mbid: str | None
        if isinstance(mbid_raw, str) and mbid_raw.strip():
            mbid = mbid_raw.strip()
        else:
            mbid = None
        url_raw = entry.get("url")
        url = url_raw.strip() if isinstance(url_raw, str) else ""
        listeners = _coerce_int(entry.get("listeners", 0))
        out.append(
            LastFMCandidate(
                name=name.strip(),
                mbid=mbid,
                listener_count=listeners,
                url=url,
            )
        )
    return out


def _parse_artist_info(payload: dict[str, Any]) -> LastFMArtistInfo | None:
    """Parse an ``artist.getInfo`` response into a :class:`LastFMArtistInfo`.

    Returns None when Last.fm reports the artist as unknown (error code
    6) so callers can distinguish "no match" from "transport error".

    Args:
        payload: The raw decoded JSON body.

    Returns:
        A :class:`LastFMArtistInfo`, or None when the response carries
        a "not found" error.
    """
    if payload.get("error") == _ERROR_NOT_FOUND:
        return None
    artist = payload.get("artist")
    if not isinstance(artist, dict):
        return None
    name = artist.get("name")
    if not isinstance(name, str) or not name.strip():
        return None

    mbid_raw = artist.get("mbid")
    mbid: str | None = (
        mbid_raw.strip() if isinstance(mbid_raw, str) and mbid_raw.strip() else None
    )

    url_raw = artist.get("url")
    url = url_raw.strip() if isinstance(url_raw, str) else ""

    stats = artist.get("stats")
    listeners = 0
    if isinstance(stats, dict):
        listeners = _coerce_int(stats.get("listeners", 0))

    tags_section = artist.get("tags")
    tag_entries: list[Any] = []
    if isinstance(tags_section, dict):
        tag_entries = _as_list(tags_section.get("tag"))
    tags = _clean_tag_list(tag_entries)

    bio_section = artist.get("bio")
    bio_summary: str | None = None
    if isinstance(bio_section, dict):
        summary = bio_section.get("summary")
        if isinstance(summary, str) and summary.strip():
            bio_summary = summary.strip()

    return LastFMArtistInfo(
        name=name.strip(),
        mbid=mbid,
        listener_count=listeners,
        url=url,
        tags=tags,
        bio_summary=bio_summary,
    )


def _clean_tag_list(raw: list[Any]) -> list[dict[str, Any]]:
    """Filter a Last.fm tag array down to the fields we keep.

    Last.fm tag entries carry ``name`` and ``url`` (and sometimes
    ``count``, though it's typically empty in ``artist.getInfo``).
    We keep ``name`` and ``url``; everything else is noise for a future
    normalization sprint.

    Args:
        raw: The list at ``artist.tags.tag`` from Last.fm.

    Returns:
        List of ``{"name": str, "url": str}`` dicts. Empty list when
        the input contains no usable entries.
    """
    cleaned: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        url_raw = entry.get("url")
        url = url_raw.strip() if isinstance(url_raw, str) else ""
        cleaned.append({"name": name.strip(), "url": url})
    return cleaned


def fetch_artist_info_by_name(
    artist_name: str,
    *,
    session: requests.Session | None = None,
) -> LastFMArtistInfo | None:
    """Fetch full artist info by name with autocorrect enabled.

    Last.fm's ``autocorrect=1`` quietly fixes minor name variations
    (case, spacing, plural-form mistakes) before resolving the artist.
    Returns None when the artist is not found on Last.fm — no error
    raised so the task layer can mark the row enriched without retry.

    Args:
        artist_name: Artist name to look up. Stripped before sending.
            Returns None for blank input without calling the API.
        session: Optional ``requests.Session`` used by tests.

    Returns:
        A :class:`LastFMArtistInfo`, or None when Last.fm has no record
        of this artist or the input is blank.

    Raises:
        LastFMAPIError: Any non-404 transport or HTTP failure.
    """
    cleaned = artist_name.strip()
    if not cleaned:
        return None
    payload = _request_json(
        {
            "method": "artist.getInfo",
            "artist": cleaned,
            "autocorrect": 1,
        },
        session=session,
    )
    return _parse_artist_info(payload)


def fetch_artist_info_by_mbid(
    mbid: str,
    *,
    session: requests.Session | None = None,
) -> LastFMArtistInfo | None:
    """Fetch full artist info by MusicBrainz ID.

    More accurate than name-based lookup when an MBID is available
    because it bypasses fuzzy matching entirely. Returns None when the
    MBID is not in Last.fm's database.

    Args:
        mbid: MusicBrainz artist ID. Stripped before sending; blank
            input returns None without calling the API.
        session: Optional ``requests.Session`` used by tests.

    Returns:
        A :class:`LastFMArtistInfo`, or None when Last.fm has no record
        for this MBID or the input is blank.

    Raises:
        LastFMAPIError: Any non-404 transport or HTTP failure.
    """
    cleaned = mbid.strip()
    if not cleaned:
        return None
    payload = _request_json(
        {
            "method": "artist.getInfo",
            "mbid": cleaned,
        },
        session=session,
    )
    return _parse_artist_info(payload)


def _name_similarity(target: str, candidate: str) -> float:
    """Return a 0-1 ``SequenceMatcher`` ratio on normalized names.

    Both inputs run through :func:`normalize_artist_name` so case and
    diacritics never tip the score.

    Args:
        target: The scraped artist name.
        candidate: A candidate name returned by Last.fm.

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
    candidates: list[LastFMCandidate],
) -> tuple[LastFMCandidate, float] | None:
    """Pick the most plausible Last.fm candidate for ``artist_name``.

    Confidence blends two signals:

    * 70% — ``SequenceMatcher`` ratio of the normalized names
    * 30% — listener-count percentile *within this candidate set*

    The percentile (``listeners / max(listeners)``) is local to the
    response, not global, so a query that only surfaces niche artists
    still gets a meaningful tiebreaker. Name dominates because Last.fm
    occasionally returns very popular but wrong artists when the query
    is a substring; listener count breaks ties between similar-name
    candidates.

    Args:
        artist_name: The scraper's artist name, before normalization.
        candidates: Output of :func:`search_lastfm_artist`.

    Returns:
        A ``(candidate, confidence)`` tuple when at least one candidate
        clears :data:`CONFIDENCE_THRESHOLD`, else None.
    """
    if not candidates:
        return None

    max_listeners = max(c.listener_count for c in candidates)
    best: LastFMCandidate | None = None
    best_confidence = 0.0
    for candidate in candidates:
        name_score = _name_similarity(artist_name, candidate.name)
        if max_listeners > 0:
            listener_score = candidate.listener_count / max_listeners
        else:
            listener_score = 0.0
        confidence = _NAME_WEIGHT * name_score + _LISTENER_WEIGHT * listener_score
        if confidence > best_confidence:
            best_confidence = confidence
            best = candidate

    if best is None or best_confidence < CONFIDENCE_THRESHOLD:
        return None
    return best, min(best_confidence, 1.0)

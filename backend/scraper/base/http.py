"""Shared HTTP utilities for HTML-based scrapers.

Every scraper that fetches a public web page (the generic HTML
platform scraper, Black Cat's custom scraper, any future venue-specific
scrapers) should go through :func:`fetch_html`. Centralizing the user
agent, timeout, and retry/backoff behavior means we can tune the whole
scraper fleet from one place and avoid rate-limiting surprises.
"""

from __future__ import annotations

import time
from typing import Final

import requests

from backend.core.logging import get_logger

logger = get_logger(__name__)

USER_AGENT: Final[str] = (
    "GreenroomBot/0.1 (+https://greenroom.concerts; contact: greenroom.dmv@gmail.com)"
)

DEFAULT_TIMEOUT: Final[float] = 20.0
DEFAULT_MAX_RETRIES: Final[int] = 3
INITIAL_BACKOFF: Final[float] = 1.0


class HttpFetchError(Exception):
    """Raised when an HTTP fetch fails after all retry attempts.

    Scraper code should either let this propagate (runner will mark
    the run failed) or catch it to fall back to an alternative source.
    """


def fetch_html(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    max_retries: int = DEFAULT_MAX_RETRIES,
    headers: dict[str, str] | None = None,
) -> str:
    """Fetch a URL and return the decoded HTML body.

    Retries on transient failures (HTTP 429, 5xx, connection errors)
    with exponential backoff. Raises :class:`HttpFetchError` if every
    attempt fails.

    Args:
        url: Fully qualified HTTP(S) URL to fetch.
        timeout: Per-request timeout in seconds.
        max_retries: Number of attempts before giving up.
        headers: Optional additional request headers. Caller-supplied
            values override the defaults.

    Returns:
        Response body as a ``str``.

    Raises:
        HttpFetchError: If the URL cannot be fetched successfully.
    """
    base_headers: dict[str, str] = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if headers:
        base_headers.update(headers)

    backoff = INITIAL_BACKOFF
    last_error: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            response = requests.get(url, headers=base_headers, timeout=timeout)

            if response.status_code == 429 or response.status_code >= 500:
                logger.warning(
                    "Transient HTTP %d for %s (attempt %d/%d), backing off %.1fs.",
                    response.status_code,
                    url,
                    attempt,
                    max_retries,
                    backoff,
                )
                time.sleep(backoff)
                backoff *= 2
                continue

            response.raise_for_status()
            return response.text

        except requests.RequestException as exc:
            last_error = exc
            logger.warning(
                "HTTP error fetching %s (attempt %d/%d): %s",
                url,
                attempt,
                max_retries,
                exc,
            )
            time.sleep(backoff)
            backoff *= 2

    raise HttpFetchError(
        f"Failed to fetch {url} after {max_retries} attempts: {last_error}"
    )

"""Per-endpoint rate limiting backed by Redis.

Used to throttle the public identity proxies — ``/auth/magic-link/request``
is the highest-risk vector (email spam, enumeration) and the OAuth and
passkey completions are protected against credential-stuffing-style
probes.

Storage is a fixed-window counter per ``(rule_name, key)`` tuple,
incremented with ``INCR`` + ``EXPIRE`` on the first hit of each window.
Windows are short — typically 60 seconds to 1 hour — so fixed-window
imprecision at the boundary is acceptable for abuse prevention.

If Redis is unreachable the limiter **fails open**: the request is
allowed through with a warning log. Blocking legitimate traffic on
infrastructure glitches would be worse than a transient loss of
throttling. Redis should be healthy 100% of the time in production
anyway (Celery and the rest of the stack all rely on it).
"""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar, cast

import redis
from flask import request

from backend.core.config import get_settings
from backend.core.exceptions import RateLimitExceededError
from backend.core.logging import get_logger

logger = get_logger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

_redis_client: redis.Redis | None = None


def _get_redis() -> redis.Redis | None:
    """Return a lazily-initialized module-level Redis client.

    Returns:
        A connected Redis client, or ``None`` if the URL is unset or a
        client could not be created. Callers must treat ``None`` and
        connection errors as fail-open signals.
    """
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    try:
        url = get_settings().redis_url
    except Exception:  # pragma: no cover - defensive: settings may fail
        logger.warning("rate_limit_settings_unavailable")
        return None
    if not url:
        return None
    try:
        _redis_client = redis.Redis.from_url(url, socket_timeout=1.0)
    except Exception:
        logger.warning("rate_limit_redis_init_failed")
        return None
    return _redis_client


def reset_redis_client_for_tests() -> None:
    """Drop the cached Redis client so tests can inject their own.

    Only call from tests; has no runtime purpose.
    """
    global _redis_client
    _redis_client = None


def get_request_ip() -> str:
    """Extract the caller's IP, honoring the edge proxy's ``X-Forwarded-For``.

    Railway and Vercel both set ``X-Forwarded-For``. The first entry is
    the original client; later entries are intermediary proxies that we
    ignore. Falls back to ``request.remote_addr`` (direct connection)
    and finally the literal ``"unknown"`` so the limiter always has a
    key to bucket on.

    Returns:
        A stable-ish string identifying the caller.
    """
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        first = forwarded.split(",", 1)[0].strip()
        if first:
            return first
    return request.remote_addr or "unknown"


def _check_and_increment(
    client: redis.Redis,
    *,
    cache_key: str,
    limit: int,
    window_seconds: int,
) -> tuple[bool, int | None]:
    """Run the atomic INCR + EXPIRE and report whether the caller is over.

    Args:
        client: The Redis client to use.
        cache_key: Fully-qualified key for this (rule, subject) pair.
        limit: Maximum number of hits permitted in the window.
        window_seconds: Length of the fixed window in seconds.

    Returns:
        Tuple of ``(is_blocked, retry_after_seconds)``. ``retry_after``
        is ``None`` when the caller is still within the limit.

    Raises:
        redis.RedisError: If Redis is unreachable or errors out — the
            caller catches this and fails open.
    """
    pipe = client.pipeline()
    pipe.incr(cache_key, 1)
    pipe.ttl(cache_key)
    results = pipe.execute()
    current = int(results[0])
    ttl = int(results[1])
    if current == 1 or ttl < 0:
        client.expire(cache_key, window_seconds)
        ttl = window_seconds
    if current > limit:
        return True, max(ttl, 1)
    return False, None


def rate_limit(
    name: str,
    *,
    limit: int,
    window_seconds: int,
    key_fn: Callable[[], str] | None = None,
) -> Callable[[F], F]:
    """Decorator that throttles a Flask route to ``limit`` hits per window.

    Args:
        name: Short rule identifier included in the Redis key. Must be
            unique per-route per-subject so collisions never cross
            endpoints (e.g. ``"magic_link_request_ip"``).
        limit: Maximum allowed calls per window per subject.
        window_seconds: Length of the fixed window in seconds.
        key_fn: Callable that produces the bucket subject for the
            current request (typically an IP or email). Defaults to
            :func:`get_request_ip`. Must return a non-empty string.

    Returns:
        A decorator that wraps the route.

    Raises:
        RateLimitExceededError: At decoration-time the decorator itself
            raises nothing; at call-time the wrapped function raises
            :class:`RateLimitExceededError` when the caller is over the
            limit.
    """
    resolver = key_fn if key_fn is not None else get_request_ip

    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            client = _get_redis()
            if client is None:
                return func(*args, **kwargs)

            try:
                subject = resolver()
            except Exception:
                logger.warning("rate_limit_key_resolution_failed", extra={"rule": name})
                return func(*args, **kwargs)
            if not subject:
                return func(*args, **kwargs)

            cache_key = f"rl:{name}:{subject}"
            try:
                blocked, retry_after = _check_and_increment(
                    client,
                    cache_key=cache_key,
                    limit=limit,
                    window_seconds=window_seconds,
                )
            except redis.RedisError:
                logger.warning("rate_limit_redis_error", extra={"rule": name})
                return func(*args, **kwargs)

            if blocked:
                logger.info(
                    "rate_limit_blocked",
                    extra={"rule": name, "subject": subject, "limit": limit},
                )
                raise RateLimitExceededError(retry_after_seconds=retry_after)
            return func(*args, **kwargs)

        return cast("F", wrapper)

    return decorator

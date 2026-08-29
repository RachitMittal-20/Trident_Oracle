"""In-process rate limiting for endpoints that accept input from outside
this system's own trusted clients -- file uploads, webhook deliveries,
approval-link redemptions. CLAUDE.md forbids Redis/Celery/any broker, so
this is a plain in-memory token bucket per (endpoint, client IP), the same
technique extractors/ratelimit.py already uses for the Gemini API's own
quota -- but non-blocking: an HTTP request that has no token left gets a
429 immediately, never a server-side sleep, which is the opposite of what
extractors.ratelimit.TokenBucket.acquire() does for a background batch job.

In-memory only: correct for this project's single-process deployment
(CLAUDE.md's Postgres-only queue, no horizontal API scaling story), not a
distributed rate limit. Restarting the process resets every bucket.
"""

import threading
import time
from collections.abc import Callable

from fastapi import HTTPException, Request


class _Bucket:
    def __init__(self, rate_per_minute: float, capacity: float) -> None:
        self._rate_per_second = rate_per_minute / 60.0
        self._capacity = capacity
        self._tokens = capacity
        self._last_refill = time.monotonic()

    def try_acquire(self) -> bool:
        now = time.monotonic()
        elapsed = max(0.0, now - self._last_refill)
        self._last_refill = now
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate_per_second)
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False


class RateLimiter:
    """One instance per protected endpoint (module-level singletons below) --
    buckets are keyed by client IP so one caller exhausting their own quota
    never blocks any other caller."""

    def __init__(self, rate_per_minute: float, capacity: float | None = None) -> None:
        self._rate_per_minute = rate_per_minute
        self._capacity = capacity if capacity is not None else rate_per_minute
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> bool:
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(self._rate_per_minute, self._capacity)
                self._buckets[key] = bucket
            return bucket.try_acquire()


def _client_key(request: Request) -> str:
    # X-Forwarded-For isn't trusted here (no reverse proxy config in this
    # project sanitizes it), so this is the direct peer address only --
    # good enough for "don't let one caller hammer this endpoint", not a
    # claim about the real client behind a proxy.
    return request.client.host if request.client else "unknown"


def rate_limit_dependency(limiter: RateLimiter) -> Callable[[Request], None]:
    def dependency(request: Request) -> None:
        if not limiter.check(_client_key(request)):
            raise HTTPException(
                status_code=429, detail="rate limit exceeded -- try again shortly"
            )

    return dependency


# Module-level singletons: one bucket set per protected endpoint, not
# shared, so a burst on one never eats another's quota.
upload_rate_limiter = RateLimiter(rate_per_minute=10)
webhook_rate_limiter = RateLimiter(rate_per_minute=30)
approval_rate_limiter = RateLimiter(rate_per_minute=20)

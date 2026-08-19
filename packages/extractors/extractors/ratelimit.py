"""A token-bucket rate limiter, used to respect Gemini's free-tier RPM quota.

The clock and sleep function are injectable so tests can drive the bucket with
a fake clock instead of waiting on the real one.
"""

import time
from collections.abc import Callable


class TokenBucket:
    """Refills at `rate_per_minute` tokens/minute, up to `capacity`. `acquire`
    blocks (via `sleep`) until a token is available."""

    def __init__(
        self,
        rate_per_minute: float,
        capacity: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if rate_per_minute <= 0:
            raise ValueError("rate_per_minute must be positive")
        self._rate_per_second = rate_per_minute / 60.0
        self._capacity = capacity if capacity is not None else rate_per_minute
        self._clock = clock
        self._sleep = sleep
        self._tokens = float(self._capacity)
        self._last_refill = clock()

    def _refill(self) -> None:
        now = self._clock()
        elapsed = max(0.0, now - self._last_refill)
        self._last_refill = now
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate_per_second)

    def acquire(self, tokens: float = 1.0) -> None:
        if tokens > self._capacity:
            raise ValueError(f"cannot acquire {tokens} tokens: bucket capacity is {self._capacity}")
        while True:
            self._refill()
            if self._tokens >= tokens:
                self._tokens -= tokens
                return
            deficit = tokens - self._tokens
            self._sleep(deficit / self._rate_per_second)

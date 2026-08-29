"""A token-bucket rate limiter, used to respect Gemini's free-tier RPM quota.

The clock and sleep function are injectable so tests can drive the bucket with
a fake clock instead of waiting on the real one.
"""

import threading
import time
from collections.abc import Callable


class TokenBucket:
    """Refills at `rate_per_minute` tokens/minute, up to `capacity`. `acquire`
    blocks (via `sleep`) until a token is available.

    Thread-safe: a single TokenBucket instance is meant to be shared across
    every worker in packages/evals/evals/runner.py's bounded-concurrency
    pool -- one bucket per worker thread would let each thread burst up to
    the full RPM independently, defeating the whole point of a shared rate
    limit. The lock is held only around the refill-and-decrement check, not
    around `sleep` itself, so a thread waiting out a deficit doesn't block
    every other thread from refilling and returning immediately.
    """

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
        self._lock = threading.Lock()

    def _refill_and_take(self, tokens: float) -> float | None:
        """Under lock: refill, then take `tokens` if available. Returns None
        on success, or the deficit (still under lock, using a consistent
        snapshot of `_tokens`) to sleep out and retry."""
        with self._lock:
            now = self._clock()
            elapsed = max(0.0, now - self._last_refill)
            self._last_refill = now
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate_per_second)
            if self._tokens >= tokens:
                self._tokens -= tokens
                return None
            return tokens - self._tokens

    def acquire(self, tokens: float = 1.0) -> None:
        if tokens > self._capacity:
            raise ValueError(f"cannot acquire {tokens} tokens: bucket capacity is {self._capacity}")
        while True:
            deficit = self._refill_and_take(tokens)
            if deficit is None:
                return
            self._sleep(deficit / self._rate_per_second)

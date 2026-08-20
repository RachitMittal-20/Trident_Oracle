"""Exponential backoff with jitter for job retries. Pure: takes no clock, no
implicit randomness (an rng is injectable) -- returns a delay, never an
absolute time, so the caller (apps/worker) adds it to its own now().
"""

import random
from datetime import timedelta


def compute_backoff(
    attempts: int, base_delay: timedelta, rng: random.Random | None = None
) -> timedelta:
    """delay = (2^attempts * base_delay) +/- random(0, base_delay).

    `attempts` is the attempt count *after* incrementing for the failure
    that triggered this call -- e.g. the first failure (attempts becomes 1)
    should be passed as 1, not 0.
    """
    if attempts < 0:
        raise ValueError("attempts must not be negative")
    if base_delay < timedelta(0):
        raise ValueError("base_delay must not be negative")

    rng = rng if rng is not None else random.Random()
    exponential = base_delay * (2**attempts)
    jitter_seconds = rng.uniform(-base_delay.total_seconds(), base_delay.total_seconds())
    delay = exponential + timedelta(seconds=jitter_seconds)

    # The formula can bottom out at (or, on floating point, fractionally
    # below) zero when jitter cancels the exponential term -- floor there so
    # a job can never be immediately reclaimed in a tight retry loop.
    return delay if delay > timedelta(0) else timedelta(0)

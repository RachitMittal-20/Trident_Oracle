import pytest
from extractors.ratelimit import TokenBucket


class FakeClock:
    """A controllable clock: acquire() calling `sleep` advances `now` by the
    requested amount instead of actually waiting, so the test runs instantly
    while still exercising the bucket's real refill math."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def test_bucket_starts_full_and_allows_burst_up_to_capacity() -> None:
    clock = FakeClock()
    bucket = TokenBucket(rate_per_minute=10, clock=clock.time, sleep=clock.sleep)

    for _ in range(10):
        bucket.acquire()

    assert clock.slept == []  # no waiting needed for the initial burst


def test_bucket_blocks_once_capacity_is_exhausted() -> None:
    clock = FakeClock()
    bucket = TokenBucket(rate_per_minute=10, clock=clock.time, sleep=clock.sleep)

    for _ in range(10):
        bucket.acquire()
    bucket.acquire()  # 11th request must wait for a refill

    assert clock.slept
    # at 10/min = 1 token per 6s, the 11th request should wait ~6s
    assert clock.slept[0] == pytest.approx(6.0, abs=0.01)


def test_bucket_refills_over_time() -> None:
    clock = FakeClock()
    bucket = TokenBucket(rate_per_minute=60, capacity=1, clock=clock.time, sleep=clock.sleep)

    bucket.acquire()
    clock.now += 1.0  # 60/min = 1 token/sec, so 1s fully refills
    bucket.acquire()

    assert clock.slept == []  # second acquire found a token already available


def test_bucket_never_exceeds_capacity() -> None:
    clock = FakeClock()
    bucket = TokenBucket(rate_per_minute=60, capacity=5, clock=clock.time, sleep=clock.sleep)

    clock.now += 1000.0  # plenty of time to overflow if refill weren't capped
    for _ in range(5):
        bucket.acquire()

    assert clock.slept == []
    bucket.acquire()
    assert clock.slept  # 6th request still had to wait -- capacity is 5, not unlimited


def test_bucket_rejects_non_positive_rate() -> None:
    with pytest.raises(ValueError):
        TokenBucket(rate_per_minute=0)


def test_bucket_rejects_acquiring_more_than_capacity() -> None:
    bucket = TokenBucket(rate_per_minute=10, capacity=5)
    with pytest.raises(ValueError):
        bucket.acquire(tokens=10)

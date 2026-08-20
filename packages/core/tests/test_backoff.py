import random
from datetime import timedelta

import pytest
from core.queue.backoff import compute_backoff

BASE = timedelta(seconds=60)


def test_backoff_schedule_across_attempts_with_zero_jitter() -> None:
    # rng.uniform(-x, x) with a fixed seed is deterministic; instead of
    # relying on a specific seed's output, patch uniform to isolate the
    # exponential term and verify the doubling schedule exactly.
    rng = random.Random()
    rng.uniform = lambda a, b: 0.0  # type: ignore[method-assign]

    expected = {
        0: timedelta(seconds=60),  # 2^0 * 60
        1: timedelta(seconds=120),  # 2^1 * 60
        2: timedelta(seconds=240),  # 2^2 * 60
        3: timedelta(seconds=480),  # 2^3 * 60
        5: timedelta(seconds=1920),  # 2^5 * 60
    }
    for attempts, expected_delay in expected.items():
        assert compute_backoff(attempts, BASE, rng=rng) == expected_delay


def test_jitter_stays_within_base_delay_of_the_exponential_term() -> None:
    rng = random.Random(42)
    for attempts in range(6):
        exponential = BASE * (2**attempts)
        delay = compute_backoff(attempts, BASE, rng=rng)
        assert exponential - BASE <= delay <= exponential + BASE


def test_delay_never_negative_even_at_maximal_negative_jitter() -> None:
    rng = random.Random()
    # Always the most negative jitter possible.
    rng.uniform = lambda a, b: a  # type: ignore[method-assign]

    delay = compute_backoff(0, BASE, rng=rng)
    assert delay >= timedelta(0)


def test_schedule_is_deterministic_for_a_fixed_seed() -> None:
    schedule_a = [compute_backoff(a, BASE, rng=random.Random(7)) for a in range(5)]
    schedule_b = [compute_backoff(a, BASE, rng=random.Random(7)) for a in range(5)]
    assert schedule_a == schedule_b


def test_schedule_increases_monotonically_with_zero_jitter() -> None:
    rng = random.Random()
    rng.uniform = lambda a, b: 0.0  # type: ignore[method-assign]

    delays = [compute_backoff(a, BASE, rng=rng) for a in range(6)]
    assert delays == sorted(delays)
    assert len(set(delays)) == len(delays)  # strictly increasing, not just non-decreasing


def test_rejects_negative_attempts() -> None:
    with pytest.raises(ValueError):
        compute_backoff(-1, BASE)


def test_rejects_negative_base_delay() -> None:
    with pytest.raises(ValueError):
        compute_backoff(0, timedelta(seconds=-1))


def test_default_rng_produces_a_value_without_an_injected_rng() -> None:
    # No rng passed -- exercises the real random.Random() default path.
    delay = compute_backoff(1, BASE)
    assert timedelta(0) <= delay <= BASE * 2 + BASE

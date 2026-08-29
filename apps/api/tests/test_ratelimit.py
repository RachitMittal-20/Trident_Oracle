from unittest.mock import MagicMock

import pytest
from api.ratelimit import RateLimiter, rate_limit_dependency
from fastapi import HTTPException


def test_rate_limiter_allows_requests_up_to_capacity() -> None:
    limiter = RateLimiter(rate_per_minute=5, capacity=5)
    for _ in range(5):
        assert limiter.check("client-a") is True


def test_rate_limiter_rejects_once_capacity_is_exhausted() -> None:
    limiter = RateLimiter(rate_per_minute=5, capacity=5)
    for _ in range(5):
        limiter.check("client-a")
    assert limiter.check("client-a") is False


def test_rate_limiter_keys_are_independent() -> None:
    # One client exhausting its bucket must never affect another's --
    # otherwise a single abusive caller could deny service to everyone.
    limiter = RateLimiter(rate_per_minute=1, capacity=1)
    assert limiter.check("client-a") is True
    assert limiter.check("client-a") is False
    assert limiter.check("client-b") is True


def _fake_request(client_ip: str) -> MagicMock:
    request = MagicMock()
    request.client.host = client_ip
    return request


def test_rate_limit_dependency_raises_429_once_exhausted() -> None:
    limiter = RateLimiter(rate_per_minute=2, capacity=2)
    dependency = rate_limit_dependency(limiter)
    request = _fake_request("1.2.3.4")

    dependency(request)  # 1st: fine
    dependency(request)  # 2nd: fine

    with pytest.raises(HTTPException) as exc_info:
        dependency(request)  # 3rd: exhausted
    assert exc_info.value.status_code == 429


def test_rate_limit_dependency_keys_by_client_ip() -> None:
    limiter = RateLimiter(rate_per_minute=1, capacity=1)
    dependency = rate_limit_dependency(limiter)

    dependency(_fake_request("1.1.1.1"))
    with pytest.raises(HTTPException):
        dependency(_fake_request("1.1.1.1"))

    # A different caller is unaffected by the first one's exhausted bucket.
    dependency(_fake_request("2.2.2.2"))

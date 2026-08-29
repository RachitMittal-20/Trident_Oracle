"""Confirms query-param validation on list endpoints returns 422 (via
FastAPI/Pydantic) before any database work happens -- no live Postgres
needed, since the `conn` dependency is overridden with a stub that raises
if anything ever tries to use it as a real connection.
"""

from collections.abc import Generator

import pytest
from api.config import get_connection, get_storage
from api.main import app
from fastapi.testclient import TestClient


class _Exploding:
    """Any attribute access means real work almost happened -- validation
    should have rejected the request before we get anywhere near this."""

    def __getattr__(self, name: str) -> None:
        raise AssertionError(f".{name} was accessed -- validation did not short-circuit")


def _fake_get_connection() -> Generator[_Exploding, None, None]:
    yield _Exploding()


def _fake_get_storage() -> _Exploding:
    return _Exploding()


@pytest.fixture(autouse=True)
def _override_connection() -> Generator[None, None, None]:
    app.dependency_overrides[get_connection] = _fake_get_connection
    app.dependency_overrides[get_storage] = _fake_get_storage
    yield
    app.dependency_overrides.clear()


client = TestClient(app)
TENANT_ID = "00000000-0000-0000-0000-000000000000"


def test_malformed_date_from_is_rejected_with_422_not_500() -> None:
    response = client.get(
        "/v1/exceptions", params={"tenant_id": TENANT_ID, "date_from": "not-a-date"}
    )
    assert response.status_code == 422


def test_malformed_date_to_is_rejected_with_422_not_500() -> None:
    response = client.get(
        "/v1/exceptions", params={"tenant_id": TENANT_ID, "date_to": "2026-13-99"}
    )
    assert response.status_code == 422


def test_exceptions_page_size_above_cap_is_rejected() -> None:
    response = client.get(
        "/v1/exceptions", params={"tenant_id": TENANT_ID, "page_size": 100_000}
    )
    assert response.status_code == 422


def test_invoices_page_size_above_cap_is_rejected() -> None:
    response = client.get("/v1/invoices", params={"tenant_id": TENANT_ID, "page_size": 10_000})
    assert response.status_code == 422


def test_invoices_page_below_one_is_rejected() -> None:
    response = client.get("/v1/invoices", params={"tenant_id": TENANT_ID, "page": 0})
    assert response.status_code == 422


def test_deliveries_limit_above_cap_is_rejected() -> None:
    response = client.get("/v1/deliveries", params={"tenant_id": TENANT_ID, "limit": 5_000})
    assert response.status_code == 422


def test_benchmarks_runs_limit_above_cap_is_rejected() -> None:
    response = client.get("/v1/benchmarks/runs", params={"limit": 10_000})
    assert response.status_code == 422


def test_benchmarks_failures_limit_above_cap_is_rejected() -> None:
    response = client.get(
        f"/v1/benchmarks/runs/{TENANT_ID}/failures", params={"limit": 10_000}
    )
    assert response.status_code == 422


def test_analytics_summary_days_above_cap_is_rejected() -> None:
    response = client.get(
        "/v1/analytics/summary", params={"tenant_id": TENANT_ID, "days": 999_999}
    )
    assert response.status_code == 422


def test_analytics_volume_days_below_one_is_rejected() -> None:
    response = client.get(
        "/v1/analytics/volume-over-time", params={"tenant_id": TENANT_ID, "days": 0}
    )
    assert response.status_code == 422

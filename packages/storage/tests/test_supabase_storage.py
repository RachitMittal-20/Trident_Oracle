"""Unit tests for SupabaseStorage using httpx.MockTransport -- no network
call is ever made. These verify the request shape (URL, method, headers,
body) and response handling against Supabase's documented API contract,
not a real bucket -- see supabase_storage.py's module docstring.
"""

import json
from collections.abc import Callable

import httpx
import pytest
from core.errors import StorageError
from storage.supabase_storage import SupabaseStorage

BASE_URL = "https://project.supabase.co"
SERVICE_KEY = "test-service-key"
BUCKET = "invoices"


def _storage(handler_fn: Callable[[httpx.Request], httpx.Response]) -> SupabaseStorage:
    transport = httpx.MockTransport(handler_fn)
    return SupabaseStorage(BASE_URL, SERVICE_KEY, BUCKET, client=httpx.Client(transport=transport))


def test_upload_posts_to_object_endpoint() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"Key": f"{BUCKET}/t/i/f.pdf"})

    storage = _storage(handler)
    storage.upload("t/i/f.pdf", b"file bytes", "application/pdf")

    assert len(requests) == 1
    req = requests[0]
    assert req.method == "POST"
    assert req.url == f"{BASE_URL}/storage/v1/object/{BUCKET}/t/i/f.pdf"
    assert req.headers["content-type"] == "application/pdf"
    assert req.headers["x-upsert"] == "true"
    assert req.content == b"file bytes"


def test_upload_error_raises_storage_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="permission denied")

    storage = _storage(handler)
    with pytest.raises(StorageError):
        storage.upload("t/i/f.pdf", b"x", "application/pdf")


def test_download_gets_object_endpoint_and_returns_bytes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url == f"{BASE_URL}/storage/v1/object/{BUCKET}/t/i/f.pdf"
        return httpx.Response(200, content=b"file bytes")

    storage = _storage(handler)
    result = storage.download("t/i/f.pdf")

    assert result == b"file bytes"


def test_download_missing_raises_storage_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    storage = _storage(handler)
    with pytest.raises(StorageError):
        storage.download("t/i/missing.pdf")


def test_signed_url_posts_to_sign_endpoint_with_expiry() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url == f"{BASE_URL}/storage/v1/object/sign/{BUCKET}/t/i/f.pdf"
        body = json.loads(request.content)
        assert body == {"expiresIn": 3600}
        return httpx.Response(
            200, json={"signedURL": f"/object/sign/{BUCKET}/t/i/f.pdf?token=abc123"}
        )

    storage = _storage(handler)
    url = storage.signed_url("t/i/f.pdf", 3600)

    assert url == f"{BASE_URL}/storage/v1/object/sign/{BUCKET}/t/i/f.pdf?token=abc123"


def test_signed_url_error_raises_storage_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    storage = _storage(handler)
    with pytest.raises(StorageError):
        storage.signed_url("t/i/f.pdf", 3600)


def test_signed_url_missing_field_raises_storage_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    storage = _storage(handler)
    with pytest.raises(StorageError):
        storage.signed_url("t/i/f.pdf", 3600)


def test_client_sends_auth_headers() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {SERVICE_KEY}"
        assert request.headers["apikey"] == SERVICE_KEY
        return httpx.Response(200, content=b"x")

    storage = _storage(handler)
    storage.download("t/i/f.pdf")

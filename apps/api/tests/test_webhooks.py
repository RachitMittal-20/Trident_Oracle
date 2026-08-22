"""Pure unit tests for api/webhooks.py's signature verification and SSRF
guard -- no DB, no real network call. See test_webhooks_integration.py for
the full POST /v1/webhooks/invoices flow against a live Postgres.
"""

import socket
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from api.webhooks import compute_signature, fetch_file_url, verify_signature
from fastapi import HTTPException

SECRET = "test-signing-secret"
NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def _timestamp(dt: datetime = NOW) -> str:
    return str(int(dt.timestamp()))


# --- valid signature accepted ------------------------------------------------


def test_valid_signature_is_accepted() -> None:
    body = b'{"tenant_id": "abc"}'
    ts = _timestamp()
    signature = compute_signature(SECRET, ts, body)

    verify_signature(
        body, timestamp_header=ts, signature_header=signature, secret=SECRET, now=NOW
    )  # must not raise


# --- wrong signature rejected -------------------------------------------------


def test_wrong_signature_is_rejected() -> None:
    body = b'{"tenant_id": "abc"}'
    ts = _timestamp()

    with pytest.raises(HTTPException) as exc_info:
        verify_signature(
            body, timestamp_header=ts, signature_header="0" * 64, secret=SECRET, now=NOW
        )
    assert exc_info.value.status_code == 401


def test_signature_computed_with_wrong_secret_is_rejected() -> None:
    body = b'{"tenant_id": "abc"}'
    ts = _timestamp()
    wrong_signature = compute_signature("a-different-secret", ts, body)

    with pytest.raises(HTTPException) as exc_info:
        verify_signature(
            body, timestamp_header=ts, signature_header=wrong_signature, secret=SECRET, now=NOW
        )
    assert exc_info.value.status_code == 401


# --- stale timestamp rejected -------------------------------------------------


def test_stale_timestamp_is_rejected() -> None:
    body = b"{}"
    old_ts = _timestamp(NOW - timedelta(minutes=6))
    signature = compute_signature(SECRET, old_ts, body)

    with pytest.raises(HTTPException) as exc_info:
        verify_signature(
            body, timestamp_header=old_ts, signature_header=signature, secret=SECRET, now=NOW
        )
    assert exc_info.value.status_code == 401


def test_timestamp_too_far_in_the_future_is_rejected() -> None:
    body = b"{}"
    future_ts = _timestamp(NOW + timedelta(minutes=6))
    signature = compute_signature(SECRET, future_ts, body)

    with pytest.raises(HTTPException) as exc_info:
        verify_signature(
            body, timestamp_header=future_ts, signature_header=signature, secret=SECRET, now=NOW
        )
    assert exc_info.value.status_code == 401


def test_timestamp_exactly_at_the_boundary_is_accepted() -> None:
    body = b"{}"
    boundary_ts = _timestamp(NOW - timedelta(minutes=5))
    signature = compute_signature(SECRET, boundary_ts, body)

    verify_signature(
        body, timestamp_header=boundary_ts, signature_header=signature, secret=SECRET, now=NOW
    )  # must not raise -- exactly 300s is still within the window


def test_missing_headers_are_rejected() -> None:
    with pytest.raises(HTTPException) as exc_info:
        verify_signature(
            b"{}", timestamp_header=None, signature_header=None, secret=SECRET, now=NOW
        )
    assert exc_info.value.status_code == 401


def test_non_integer_timestamp_is_rejected() -> None:
    with pytest.raises(HTTPException) as exc_info:
        verify_signature(
            b"{}", timestamp_header="not-a-number", signature_header="x", secret=SECRET, now=NOW
        )
    assert exc_info.value.status_code == 401


# --- body tampering rejected ---------------------------------------------


def test_tampered_body_is_rejected() -> None:
    original_body = b'{"tenant_id": "abc", "file_base64": "AAAA"}'
    tampered_body = b'{"tenant_id": "abc", "file_base64": "BBBB"}'
    ts = _timestamp()
    signature = compute_signature(SECRET, ts, original_body)  # signed the ORIGINAL body

    with pytest.raises(HTTPException) as exc_info:
        verify_signature(
            tampered_body, timestamp_header=ts, signature_header=signature, secret=SECRET, now=NOW
        )
    assert exc_info.value.status_code == 401


def test_tampered_timestamp_is_rejected_even_with_a_body_matching_signature() -> None:
    # Signature covers "{timestamp}.{raw_body}" -- changing the timestamp
    # after signing must also invalidate the signature, not just the body.
    body = b"{}"
    ts = _timestamp()
    signature = compute_signature(SECRET, ts, body)
    other_ts = _timestamp(NOW - timedelta(seconds=1))

    with pytest.raises(HTTPException) as exc_info:
        verify_signature(
            body, timestamp_header=other_ts, signature_header=signature, secret=SECRET, now=NOW
        )
    assert exc_info.value.status_code == 401


# --- fetch_file_url: SSRF guard ----------------------------------------------


def test_fetch_file_url_rejects_non_http_scheme() -> None:
    with pytest.raises(ValueError, match="scheme"):
        fetch_file_url("ftp://example.com/invoice.pdf")


def test_fetch_file_url_rejects_file_scheme() -> None:
    with pytest.raises(ValueError, match="scheme"):
        fetch_file_url("file:///etc/passwd")


def test_fetch_file_url_rejects_a_hostname_resolving_to_loopback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda host, port: [(2, 1, 6, "", ("127.0.0.1", 0))]
    )
    with pytest.raises(ValueError, match="private/reserved"):
        fetch_file_url("http://internal.example/secret")


def test_fetch_file_url_rejects_a_hostname_resolving_to_private_range(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda host, port: [(2, 1, 6, "", ("10.0.0.5", 0))]
    )
    with pytest.raises(ValueError, match="private/reserved"):
        fetch_file_url("http://internal.example/secret")


def test_fetch_file_url_rejects_the_cloud_metadata_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 169.254.169.254 -- the AWS/GCP/Azure instance-metadata endpoint, the
    # single most common real-world SSRF exploitation target.
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda host, port: [(2, 1, 6, "", ("169.254.169.254", 0))]
    )
    with pytest.raises(ValueError, match="private/reserved"):
        fetch_file_url("http://metadata.internal/latest/meta-data/")


def test_fetch_file_url_accepts_a_public_address_and_returns_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda host, port: [(2, 1, 6, "", ("93.184.216.34", 0))]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"%PDF-1.4 fake pdf content")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = fetch_file_url("http://example.com/invoice.pdf", client=client)
    assert result == b"%PDF-1.4 fake pdf content"


def test_fetch_file_url_propagates_upstream_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda host, port: [(2, 1, 6, "", ("93.184.216.34", 0))]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, content=b"not found")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(ValueError, match="fetch failed"):
        fetch_file_url("http://example.com/missing.pdf", client=client)


def test_fetch_file_url_rejects_unresolvable_host(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_gaierror(host: str, port: object) -> object:
        raise socket.gaierror("name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", raise_gaierror)
    with pytest.raises(ValueError, match="could not resolve"):
        fetch_file_url("http://does-not-exist.invalid/file.pdf")

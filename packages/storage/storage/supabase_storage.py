"""Supabase Storage backend, implemented directly against Supabase's
documented Storage REST API (not the supabase-py SDK), so the only new
dependency is httpx, already used elsewhere in this repo. httpx is imported
only in this module -- same discipline as the google-genai SDK in
extractors/gemini.py.

Not exercised against a live Supabase project in this environment (none is
configured here). It's implemented to the documented API and covered by
tests that fake the HTTP transport (see tests/test_supabase_storage.py),
but that is not the same as having actually round-tripped a file through a
real bucket -- treat this as unverified against the live service until
someone does that with real credentials.
"""

import httpx
from core.errors import StorageError

from storage.base import Storage


class SupabaseStorage(Storage):
    def __init__(
        self,
        base_url: str,
        service_key: str,
        bucket: str,
        client: httpx.Client | None = None,
    ) -> None:
        self._api_base = base_url.rstrip("/") + "/storage/v1"
        self._bucket = bucket
        # Auth headers are attached per-request (_auth_headers), not as
        # client-level defaults -- an injected `client` (as tests do, to
        # fake the transport) would otherwise silently skip them, since
        # client-level default headers only apply to a client this
        # constructor itself builds.
        self._service_key = service_key
        self._client = client if client is not None else httpx.Client(timeout=30.0)

    def _auth_headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._service_key}", "apikey": self._service_key}
        if extra:
            headers.update(extra)
        return headers

    def upload(self, path: str, data: bytes, content_type: str) -> None:
        response = self._client.post(
            f"{self._api_base}/object/{self._bucket}/{path}",
            content=data,
            headers=self._auth_headers({"Content-Type": content_type, "x-upsert": "true"}),
        )
        if response.status_code >= 400:
            raise StorageError(
                f"upload failed for {path!r}: {response.status_code} {response.text}"
            )

    def download(self, path: str) -> bytes:
        response = self._client.get(
            f"{self._api_base}/object/{self._bucket}/{path}",
            headers=self._auth_headers(),
        )
        if response.status_code >= 400:
            raise StorageError(
                f"download failed for {path!r}: {response.status_code} {response.text}"
            )
        return response.content

    def signed_url(self, path: str, expires_in_seconds: int) -> str:
        response = self._client.post(
            f"{self._api_base}/object/sign/{self._bucket}/{path}",
            json={"expiresIn": expires_in_seconds},
            headers=self._auth_headers(),
        )
        if response.status_code >= 400:
            raise StorageError(
                f"signing failed for {path!r}: {response.status_code} {response.text}"
            )
        signed_path = response.json().get("signedURL")
        if not signed_path:
            raise StorageError(f"sign response for {path!r} had no signedURL")
        return self._api_base + str(signed_path)

"""The Storage interface every backend implements.

apps/api uploads at ingestion and signs URLs for GET /v1/invoices/{id};
apps/worker downloads during extraction. Both depend on this interface,
never on a specific backend's client directly -- CLAUDE.md principle 2
("every external dependency sits behind an interface"), the same rule
packages/extractors follows for Gemini/Tesseract.
"""

from abc import ABC, abstractmethod


class Storage(ABC):
    @abstractmethod
    def upload(self, path: str, data: bytes, content_type: str) -> None:
        """Uploads `data` to `path` in the private bucket.

        Raises StorageError (packages/core) on failure.
        """
        raise NotImplementedError

    @abstractmethod
    def download(self, path: str) -> bytes:
        """Downloads the object at `path`.

        Raises StorageError if the object is missing or the download fails.
        """
        raise NotImplementedError

    @abstractmethod
    def signed_url(self, path: str, expires_in_seconds: int) -> str:
        """Returns a short-lived signed URL for `path`.

        Raises StorageError on failure. Never returns an unsigned/public path
        -- CLAUDE.md: "private bucket, signed URLs only."
        """
        raise NotImplementedError

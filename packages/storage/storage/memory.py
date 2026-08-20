"""An in-memory Storage backend. Every test that needs storage uses this
instead of SupabaseStorage, so the suite never touches the network.
"""

from core.errors import StorageError

from storage.base import Storage


class MemoryStorage(Storage):
    def __init__(self) -> None:
        self._objects: dict[str, tuple[bytes, str]] = {}

    def upload(self, path: str, data: bytes, content_type: str) -> None:
        self._objects[path] = (data, content_type)

    def download(self, path: str) -> bytes:
        if path not in self._objects:
            raise StorageError(f"no object at {path!r}")
        return self._objects[path][0]

    def signed_url(self, path: str, expires_in_seconds: int) -> str:
        if path not in self._objects:
            raise StorageError(f"no object at {path!r}")
        return f"memory://{path}?expires_in={expires_in_seconds}"

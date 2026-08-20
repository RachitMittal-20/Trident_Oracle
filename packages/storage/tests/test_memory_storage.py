import pytest
from core.errors import StorageError
from storage.memory import MemoryStorage


def test_upload_then_download_roundtrips() -> None:
    storage = MemoryStorage()
    storage.upload("tenant/inv/file.pdf", b"file bytes", "application/pdf")

    assert storage.download("tenant/inv/file.pdf") == b"file bytes"


def test_download_missing_object_raises() -> None:
    storage = MemoryStorage()
    with pytest.raises(StorageError):
        storage.download("nope")


def test_signed_url_missing_object_raises() -> None:
    storage = MemoryStorage()
    with pytest.raises(StorageError):
        storage.signed_url("nope", 3600)


def test_signed_url_for_existing_object() -> None:
    storage = MemoryStorage()
    storage.upload("path", b"x", "image/png")

    url = storage.signed_url("path", 3600)

    assert "path" in url
    assert "3600" in url


def test_upload_overwrites_existing_path() -> None:
    storage = MemoryStorage()
    storage.upload("path", b"first", "text/plain")
    storage.upload("path", b"second", "text/plain")

    assert storage.download("path") == b"second"

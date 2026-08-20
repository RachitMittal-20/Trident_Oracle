import pytest
from core.magic_bytes import ALLOWED_MIME_TYPES, sniff_mime_type


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b"%PDF-1.7\n...rest of pdf...", "application/pdf"),
        (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR", "image/png"),
        (b"\xff\xd8\xff\xe0\x00\x10JFIF", "image/jpeg"),
        (b"\xff\xd8\xff\xe1\x00\x10Exif", "image/jpeg"),  # JPEG with EXIF variant
    ],
)
def test_sniff_recognizes_allowed_formats(data: bytes, expected: str) -> None:
    assert sniff_mime_type(data) == expected


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"not a real file",
        b"MZ\x90\x00",  # a Windows .exe (PE) header
        b"GIF89a",  # a format we deliberately don't accept
        b"PK\x03\x04",  # a zip (also docx/xlsx) header
    ],
)
def test_sniff_rejects_unrecognized_or_disallowed_formats(data: bytes) -> None:
    assert sniff_mime_type(data) is None


def test_extension_is_irrelevant_only_bytes_matter() -> None:
    # An .exe renamed to invoice.pdf must not be sniffed as a PDF just
    # because of its filename -- sniff_mime_type never sees a filename at
    # all, only bytes, which is the entire point.
    exe_bytes = b"MZ\x90\x00\x03\x00\x00\x00"
    assert sniff_mime_type(exe_bytes) is None


def test_allowed_mime_types_matches_the_three_accepted_formats() -> None:
    assert ALLOWED_MIME_TYPES == {"application/pdf", "image/png", "image/jpeg"}

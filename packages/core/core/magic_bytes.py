"""File-type detection by magic bytes, not filename/extension -- a renamed
.exe with a .pdf extension must not pass. Pure function, no I/O: covers
exactly the formats POST /v1/invoices/upload accepts (PDF, PNG, JPEG), not
a general-purpose library, since that's the entire set this system ever
needs to tell apart.
"""

# Order matters only in that longer/more-specific signatures should be
# checked as an exact prefix match; there's no overlap risk between these
# three, so a simple ordered scan is enough.
_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"%PDF-", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
)

ALLOWED_MIME_TYPES = frozenset(mime_type for _, mime_type in _SIGNATURES)


def sniff_mime_type(data: bytes) -> str | None:
    """Returns the detected mime type, or None if `data` doesn't start with
    any recognized signature (i.e. it's not one of the allowed formats)."""
    for signature, mime_type in _SIGNATURES:
        if data.startswith(signature):
            return mime_type
    return None

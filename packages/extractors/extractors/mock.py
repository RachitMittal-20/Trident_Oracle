"""A fixture-backed Extractor. Every test that needs extraction output uses
this instead of GeminiExtractor, so the suite never touches the network.
"""

import json
from pathlib import Path

from extractors.base import ExtractionResult, Extractor

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class MockExtractor(Extractor):
    """Ignores file_bytes/mime_type entirely and returns a named fixture.
    Useful both for wiring tests (does the caller handle an ExtractionResult
    correctly?) and, via fixture selection, for exercising specific scenarios
    (low confidence, missing PO, etc.) without needing a real document."""

    def __init__(self, fixture: str = "clean_invoice") -> None:
        self._fixture = fixture

    def extract(self, file_bytes: bytes, mime_type: str) -> ExtractionResult:
        path = FIXTURES_DIR / f"{self._fixture}.json"
        data = json.loads(path.read_text())
        return ExtractionResult.model_validate(data)

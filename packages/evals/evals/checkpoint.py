"""Disk checkpointing so an interrupted eval run resumes rather than
restarting (CLAUDE.md prompt). One JSON line per completed document,
appended (and flushed + fsynced) the moment that document finishes -- not
buffered until the run ends -- so a process killed mid-run has already
durably recorded every document it got through.

Only the extraction result is persisted, not the ground truth: dataset
loaders promise a stable iteration order (DatasetLoader.__iter__'s own
docstring), so runner.py re-derives ground truth by re-iterating the
loader up to the same `n` on resume rather than duplicating it on disk.
"""

import json
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evals.errors import CheckpointError


@dataclass(frozen=True, slots=True)
class CheckpointEntry:
    doc_id: str
    dataset: str
    backend: str
    extraction_result: dict[str, Any] | None  # ExtractionResult.model_dump(), or None on error
    error: str | None
    latency_ms: int | None
    estimated_cost_usd: float


def append_entry(path: Path, entry: CheckpointEntry) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(
        {
            "doc_id": entry.doc_id,
            "dataset": entry.dataset,
            "backend": entry.backend,
            "extraction_result": entry.extraction_result,
            "error": entry.error,
            "latency_ms": entry.latency_ms,
            "estimated_cost_usd": entry.estimated_cost_usd,
        }
    )
    with path.open("a") as f:
        f.write(line + "\n")
        f.flush()
        os.fsync(f.fileno())


def load_entries(path: Path, dataset: str, backend: str) -> dict[str, CheckpointEntry]:
    """Returns completed entries keyed by doc_id. Raises CheckpointError if
    the file belongs to a different dataset/backend combination -- resuming
    a gemini/docile run from a tesseract/cord checkpoint would silently mix
    incompatible results into one report."""
    if not path.is_file():
        return {}

    entries: dict[str, CheckpointEntry] = {}
    for lineno, raw_line in enumerate(path.read_text().splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            data = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise CheckpointError(f"{path}:{lineno}: not valid JSON") from exc

        if data["dataset"] != dataset or data["backend"] != backend:
            raise CheckpointError(
                f"{path} was started for dataset={data['dataset']!r} backend={data['backend']!r}, "
                f"not dataset={dataset!r} backend={backend!r} -- use a different --checkpoint path "
                "or delete the existing file if you mean to start over"
            )

        entries[data["doc_id"]] = CheckpointEntry(
            doc_id=data["doc_id"],
            dataset=data["dataset"],
            backend=data["backend"],
            extraction_result=data["extraction_result"],
            error=data["error"],
            latency_ms=data["latency_ms"],
            estimated_cost_usd=data["estimated_cost_usd"],
        )
    return entries


def iter_successful(entries: Iterator[CheckpointEntry]) -> Iterator[CheckpointEntry]:
    return (e for e in entries if e.error is None)

"""Dataset registry -- CLAUDE.md-style factory, mirroring
extractors.factory.get_extractor: callers name a dataset, this resolves it
to a concrete DatasetLoader. DocILE is primary; CORD and SROIE are the
documented fallbacks if DocILE access hasn't come through (see
evals/datasets/docile.py's module docstring)."""

from pathlib import Path

from evals.datasets.base import DatasetLoader
from evals.datasets.cord import CordLoader
from evals.datasets.docile import DocileLoader
from evals.datasets.sroie import SroieLoader
from evals.errors import EvalError

_LOADERS: dict[str, type[DatasetLoader]] = {
    "docile": DocileLoader,
    "cord": CordLoader,
    "sroie": SroieLoader,
}

# Fallback order when the requested dataset's access hasn't come through --
# DocILE first (primary), then the two public datasets that need no
# registration.
FALLBACK_ORDER = ("docile", "cord", "sroie")


def get_dataset_loader(name: str, root: Path) -> DatasetLoader:
    try:
        loader_cls = _LOADERS[name]
    except KeyError:
        raise EvalError(
            f"unknown dataset: {name!r} -- choose one of {sorted(_LOADERS)}"
        ) from None
    return loader_cls(root)


__all__ = ["DatasetLoader", "FALLBACK_ORDER", "get_dataset_loader"]

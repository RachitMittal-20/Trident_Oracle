"""The DatasetLoader interface every dataset (DocILE, CORD, SROIE) implements
-- runner.py, metrics.py, and compare.py all depend on this, never on a
concrete loader, same "every external dependency sits behind an interface"
principle CLAUDE.md applies to extractors.base.Extractor.
"""

from abc import ABC, abstractmethod
from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

from evals.errors import DatasetNotFoundError
from evals.models import DatasetExample


class DatasetLoader(ABC):
    """Iterates a local, already-downloaded copy of a dataset. `root` is the
    dataset's extracted directory -- these loaders never download anything
    themselves; where to get each dataset is documented on the concrete
    loader that needs it (DocILE in particular requires registering for
    access, which is exactly why CORD and SROIE exist as fallbacks here)."""

    name: ClassVar[str]

    def __init__(self, root: Path) -> None:
        self.root = root
        if not root.is_dir():
            raise DatasetNotFoundError(
                f"{self.name} dataset root not found: {root} -- see {type(self).__module__} "
                "for how to obtain and lay out this dataset"
            )

    @abstractmethod
    def __iter__(self) -> Iterator[DatasetExample]:
        """Yields examples in a stable, deterministic order -- runner.py and
        compare.py both rely on `itertools.islice(loader, n)` selecting the
        identical first N documents on every call, so a comparison run
        genuinely evaluates two backends on the same sample."""
        raise NotImplementedError

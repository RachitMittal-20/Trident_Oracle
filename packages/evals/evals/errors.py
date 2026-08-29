"""Exception hierarchy for the benchmark harness -- rooted at
TridentOracleError like every other error in this codebase (CLAUDE.md:
"Custom exception hierarchy rooted at TridentOracleError. Never raise bare
Exception.")
"""

from core.errors import TridentOracleError


class EvalError(TridentOracleError):
    """Root of the evals-specific hierarchy."""


class DatasetNotFoundError(EvalError):
    """A dataset's local root directory doesn't exist or isn't laid out the
    way its loader expects -- e.g. DocILE access hasn't come through yet.
    Callers (the CLI) catch this specifically to fall back to another
    dataset rather than crash."""


class DatasetFormatError(EvalError):
    """A dataset file exists but its contents don't parse the way the
    loader expects -- a real corruption/version-mismatch signal, distinct
    from DatasetNotFoundError's "never downloaded this at all"."""


class CheckpointError(EvalError):
    """A checkpoint file exists but is corrupt or from an incompatible run
    (different dataset/backend/n) -- resuming from it would silently mix
    results from two different configurations."""

"""`python -m evals run --backend gemini --dataset docile --n 500` and
`python -m evals compare --backends gemini,tesseract --dataset docile --n 200`.

Argument parsing lives here (not in __main__.py) so `main()` can be called
directly from a test with an argv list, without going through a subprocess.
"""

import argparse
import os
import sys
from pathlib import Path

import psycopg
import structlog

from evals.compare import compare
from evals.datasets import FALLBACK_ORDER, get_dataset_loader
from evals.errors import DatasetNotFoundError
from evals.metrics import compute_metrics
from evals.report import append_to_benchmarks_md, format_compare_report, format_run_report
from evals.runner import run
from evals.storage import persist_run

log = structlog.get_logger()

DEFAULT_BENCHMARKS_MD = Path("docs/BENCHMARKS.md")
DEFAULT_CHECKPOINT_DIR = Path(".evals_checkpoints")


def _default_dataset_root(dataset: str) -> Path:
    env_var = f"{dataset.upper()}_ROOT"
    return Path(os.environ.get(env_var, f"data/{dataset}"))


def _resolve_dataset_root(dataset: str, dataset_root: Path | None) -> Path:
    return dataset_root if dataset_root is not None else _default_dataset_root(dataset)


def _require_database_url() -> str | None:
    """Returns None (having already printed the error) rather than raising
    -- every other validation failure in this module signals the same way,
    so callers all follow the same `if x is None: return 1` shape."""
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print(
            "error: DATABASE_URL must be set -- eval_runs/eval_results persistence "
            "(db/migrations/0009_evals.sql) is not optional, per the harness's own design",
            file=sys.stderr,
        )
        return None
    return database_url


def _dataset_not_found_hint(dataset: str) -> str:
    remaining = [d for d in FALLBACK_ORDER if d != dataset]
    return (
        f"hint: {dataset} was not found locally. It is not auto-substituted -- rerun with "
        f"--dataset {remaining[0]} (or {' / '.join(remaining[1:])}) if you want a different "
        "dataset; a report must never be labeled with a dataset it didn't actually run against."
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m evals")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run one backend across N documents.")
    run_parser.add_argument("--backend", required=True, choices=["gemini", "tesseract", "mock"])
    run_parser.add_argument("--dataset", required=True, choices=list(FALLBACK_ORDER))
    run_parser.add_argument("--n", type=int, required=True)
    run_parser.add_argument("--dataset-root", type=Path, default=None)
    run_parser.add_argument("--concurrency", type=int, default=4)
    run_parser.add_argument("--checkpoint", type=Path, default=None)
    run_parser.add_argument("--benchmarks-md", type=Path, default=DEFAULT_BENCHMARKS_MD)

    compare_parser = subparsers.add_parser(
        "compare", help="Compare two backends on the same sample."
    )
    compare_parser.add_argument("--backends", required=True, help="e.g. gemini,tesseract")
    compare_parser.add_argument("--dataset", required=True, choices=list(FALLBACK_ORDER))
    compare_parser.add_argument("--n", type=int, required=True)
    compare_parser.add_argument("--dataset-root", type=Path, default=None)
    compare_parser.add_argument("--concurrency", type=int, default=4)
    compare_parser.add_argument("--checkpoint-dir", type=Path, default=None)
    compare_parser.add_argument("--benchmarks-md", type=Path, default=DEFAULT_BENCHMARKS_MD)

    return parser


def _cmd_run(args: argparse.Namespace) -> int:
    database_url = _require_database_url()
    if database_url is None:
        return 1
    dataset_root = _resolve_dataset_root(args.dataset, args.dataset_root)
    try:
        loader = get_dataset_loader(args.dataset, dataset_root)
    except DatasetNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print(_dataset_not_found_hint(args.dataset), file=sys.stderr)
        return 1

    checkpoint_path = args.checkpoint or (
        DEFAULT_CHECKPOINT_DIR / f"{args.dataset}_{args.backend}.jsonl"
    )
    result = run(
        args.dataset, loader, args.backend, args.n,
        concurrency=args.concurrency, checkpoint_path=checkpoint_path,
    )
    metrics = compute_metrics(args.dataset, args.backend, result.pairs)

    with psycopg.connect(database_url) as conn:
        eval_run_id = persist_run(conn, result, metrics)

    report = format_run_report(result, metrics)
    append_to_benchmarks_md(args.benchmarks_md, report)
    print(report)
    print(f"Persisted as eval_runs.id = {eval_run_id}")
    return 0


def _cmd_compare(args: argparse.Namespace) -> int:
    database_url = _require_database_url()
    if database_url is None:
        return 1
    backend_names = [b.strip() for b in args.backends.split(",")]
    if len(backend_names) != 2:
        print(
            "error: --backends must name exactly two backends, e.g. gemini,tesseract",
            file=sys.stderr,
        )
        return 1
    backend_a, backend_b = backend_names
    if backend_a == backend_b:
        # Beyond being a meaningless comparison, checkpoint files are named
        # f"{backend_name}.jsonl" (compare.py) -- two identical names would
        # silently collide onto the same file, making the second run "resume"
        # from the first's results instead of genuinely running twice.
        print(f"error: --backends names the same backend twice: {backend_a!r}", file=sys.stderr)
        return 1

    dataset_root = _resolve_dataset_root(args.dataset, args.dataset_root)
    try:
        loader = get_dataset_loader(args.dataset, dataset_root)
    except DatasetNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print(_dataset_not_found_hint(args.dataset), file=sys.stderr)
        return 1

    checkpoint_dir = args.checkpoint_dir or (DEFAULT_CHECKPOINT_DIR / f"compare_{args.dataset}")
    result = compare(
        args.dataset, loader, backend_a, backend_b, args.n,
        concurrency=args.concurrency, checkpoint_dir=checkpoint_dir,
    )

    with psycopg.connect(database_url) as conn:
        persist_run(conn, result.run_a, result.metrics_a)
        persist_run(conn, result.run_b, result.metrics_b)

    report = format_compare_report(result)
    append_to_benchmarks_md(args.benchmarks_md, report)
    print(report)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "run":
        return _cmd_run(args)
    if args.command == "compare":
        return _cmd_compare(args)
    parser.print_help()
    return 1

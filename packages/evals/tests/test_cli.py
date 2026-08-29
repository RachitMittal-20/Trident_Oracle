"""Unit tests for the argument-parsing/validation paths in evals.cli that
don't require a live database -- `_require_database_url` only checks the
env var is *set*, so these exercise every validation branch that runs
before any real connection or dataset access is attempted.

The full run -> persist -> report path (a real psycopg connection,
eval_runs/eval_results/eval_run_calibration actually populated) was
verified by hand against a disposable Docker Postgres with all migrations
applied, per this project's established pattern for DB-touching code
(never against the live Supabase project) -- not re-verified here since
that would need a real Postgres in CI, which this test file deliberately
doesn't require.
"""

from evals.cli import main


def test_missing_database_url_fails_loudly(monkeypatch, capsys) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    exit_code = main(["run", "--backend", "mock", "--dataset", "sroie", "--n", "1"])

    assert exit_code == 1
    assert "DATABASE_URL" in capsys.readouterr().err


def test_compare_rejects_identical_backend_names(monkeypatch, capsys) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake/fake")

    exit_code = main(["compare", "--backends", "mock,mock", "--dataset", "sroie", "--n", "1"])

    assert exit_code == 1
    assert "same backend twice" in capsys.readouterr().err


def test_compare_rejects_wrong_number_of_backends(monkeypatch, capsys) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake/fake")

    exit_code = main(
        ["compare", "--backends", "mock,tesseract,gemini", "--dataset", "sroie", "--n", "1"]
    )

    assert exit_code == 1
    assert "exactly two backends" in capsys.readouterr().err


def test_run_reports_missing_dataset_root_with_a_fallback_hint(
    monkeypatch, tmp_path, capsys
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake/fake")

    exit_code = main(
        [
            "run",
            "--backend",
            "mock",
            "--dataset",
            "docile",
            "--n",
            "1",
            "--dataset-root",
            str(tmp_path / "does-not-exist"),
        ]
    )

    assert exit_code == 1
    err = capsys.readouterr().err
    assert "docile dataset root not found" in err
    assert "--dataset cord" in err

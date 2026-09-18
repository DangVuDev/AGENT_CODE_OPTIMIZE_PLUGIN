"""Exercises `run_repository_command` directly: the shared executable-
resolution + OSError-handling wrapper every command-kind worker capability
(A2's `_run`/`_run_benchmark`, S04's `_run`, S05's `_run_benchmark`) calls
instead of `subprocess.run` directly."""

from __future__ import annotations

import sys
from pathlib import Path

from production_optimizer.application.a2_worker_capabilities import run_repository_command


def test_runs_a_real_resolvable_executable(tmp_path: Path) -> None:
    result = run_repository_command(
        [sys.executable, "-c", "print('ok')"], cwd=tmp_path, timeout_seconds=10
    )

    assert result.returncode == 0
    assert "ok" in result.stdout


def test_reports_an_unresolvable_executable_instead_of_raising(tmp_path: Path) -> None:
    """Regression test for a real crash: a requester-declared command whose
    executable is not on PATH (a typo, or a tool never installed in this
    environment) used to raise an unhandled `FileNotFoundError` out of
    `subprocess.run`, aborting the whole node instead of surfacing as an
    ordinary failing check -- observed running a real `npx nx build ...`
    command declared via `WorkloadContract.commands`."""

    result = run_repository_command(
        ["this-executable-does-not-exist-anywhere"], cwd=tmp_path, timeout_seconds=10
    )

    assert result.returncode == -1
    assert "this-executable-does-not-exist-anywhere" in result.stderr

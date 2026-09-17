# pyright: reportPrivateUsage=false
"""Exercises S04's pytest-invocation detection and coverage-flag decision
directly -- pure functions that decide whether `--cov` flags are safe to
append to a real unit-test command, worth testing without a full S03->S04
pipeline run."""

from __future__ import annotations

from datetime import UTC, datetime

from production_optimizer.application.s04_worker_capabilities import (
    _coverage_argv,
    _is_pytest_invocation,
    _parse_coverage_percent,
)
from production_optimizer.contracts.a2 import RepositoryCommand, RepositoryManifest
from production_optimizer.contracts.envelope import ProducerIdentity

_ZERO_DIGEST = "sha256:" + "0" * 64
_TEST_PRODUCER = ProducerIdentity(name="test", version="1.0")


def _manifest(*, tool_coverage: dict[str, float]) -> RepositoryManifest:
    return RepositoryManifest(
        artifact_id="manifest-1",
        tenant_id="TENANT-1",
        case_id="CASE-1",
        created_at=datetime.now(UTC),
        producer=_TEST_PRODUCER,
        content_digest=_ZERO_DIGEST,
        languages={"python": 1.0},
        modules=["."],
        manifest_files=[],
        test_roots=["tests"],
        commands=[],
        tool_coverage=tool_coverage,
    )


def test_is_pytest_invocation_recognizes_python_dash_m_pytest() -> None:
    assert _is_pytest_invocation(["python", "-m", "pytest", "tests"]) is True


def test_is_pytest_invocation_recognizes_bare_pytest_entrypoint() -> None:
    assert _is_pytest_invocation(["pytest", "tests"]) is True


def test_is_pytest_invocation_recognizes_wrapped_invocations() -> None:
    assert _is_pytest_invocation(["uv", "run", "pytest", "tests"]) is True
    assert _is_pytest_invocation(["tox", "-e", "pytest"]) is True


def test_is_pytest_invocation_rejects_a_non_pytest_command() -> None:
    assert _is_pytest_invocation(["npm", "test"]) is False
    assert _is_pytest_invocation(["go", "test", "./..."]) is False


def test_coverage_argv_appends_flags_for_a_real_pytest_command_when_declared() -> None:
    command = RepositoryCommand(
        command_id="unit-1", argv=["python", "-m", "pytest", "tests"],
        working_directory=".", kind="unit", source="pyproject_toml",
    )
    manifest = _manifest(tool_coverage={"pytest_cov": 1.0})

    argv, coverage_requested = _coverage_argv(command, manifest)

    assert coverage_requested is True
    assert argv == ["python", "-m", "pytest", "tests", "--cov=.", "--cov-report=term"]


def test_coverage_argv_does_not_append_flags_when_pytest_cov_not_declared() -> None:
    command = RepositoryCommand(
        command_id="unit-1", argv=["python", "-m", "pytest", "tests"],
        working_directory=".", kind="unit", source="pyproject_toml",
    )
    manifest = _manifest(tool_coverage={})

    argv, coverage_requested = _coverage_argv(command, manifest)

    assert coverage_requested is False
    assert argv == ["python", "-m", "pytest", "tests"]


def test_coverage_argv_never_corrupts_a_non_pytest_unit_command() -> None:
    """The bug this guards against: a repository declares `pytest-cov` for
    one test suite, but the requester's own unit command is something else
    entirely (e.g. `npm test`) -- appending `--cov` flags there would
    corrupt an unrelated command and cause a spurious mandatory-check
    failure that isn't a real regression."""

    command = RepositoryCommand(
        command_id="unit-1", argv=["npm", "test"],
        working_directory=".", kind="unit", source="user_declared",
    )
    manifest = _manifest(tool_coverage={"pytest_cov": 1.0})

    argv, coverage_requested = _coverage_argv(command, manifest)

    assert coverage_requested is False
    assert argv == ["npm", "test"]


def test_parse_coverage_percent_reads_the_total_line() -> None:
    stdout = (
        "Name       Stmts   Miss  Cover\n"
        "--------------------------------\n"
        "app.py         2      0   100%\n"
        "--------------------------------\n"
        "TOTAL          2      0   100%\n"
    )
    assert _parse_coverage_percent(stdout) == 100.0


def test_parse_coverage_percent_returns_none_without_a_total_line() -> None:
    assert _parse_coverage_percent("no coverage report here\n") is None

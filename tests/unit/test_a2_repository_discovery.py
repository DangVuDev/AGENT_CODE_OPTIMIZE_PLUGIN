# pyright: reportPrivateUsage=false
# Exercises A2's repository-discovery internals directly: these decide which
# interpreter and which config files a real target repo is judged by, and
# that logic deserves its own tests rather than only being reachable through
# a full A2.30/A2.31 run.
from __future__ import annotations

import sys
from pathlib import Path

from production_optimizer.application.a2_handlers.shared import (
    _declared_tools_outside_pyproject,
    _repository_interpreter,
    _requirements_declare_pytest_benchmark,
)


def _make_venv(root: Path) -> Path:
    """Create the interpreter file layout a real venv has on this platform."""

    if sys.platform == "win32":
        interpreter = root / ".venv" / "Scripts" / "python.exe"
    else:
        interpreter = root / ".venv" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("", encoding="utf-8")
    return interpreter


def test_prefers_the_repositorys_own_venv(tmp_path: Path) -> None:
    interpreter = _make_venv(tmp_path)

    assert _repository_interpreter(tmp_path) == str(interpreter)


def test_falls_back_to_an_absolute_interpreter_path(tmp_path: Path) -> None:
    """Never the bare string "python": `RepositoryCommand.argv` is sealed
    into the evidence chain, so which binary ran must be auditable."""

    chosen = _repository_interpreter(tmp_path)

    assert chosen != "python"
    assert Path(chosen).is_absolute(), chosen


def test_detects_pytest_and_mypy_declared_in_setup_cfg(tmp_path: Path) -> None:
    (tmp_path / "setup.cfg").write_text(
        "[tool:pytest]\ntestpaths = tests\n\n[mypy]\nstrict = True\n", encoding="utf-8"
    )

    declared = _declared_tools_outside_pyproject(tmp_path)

    assert "tool.pytest.ini_options" in declared
    assert "tool.mypy" in declared
    assert "tool.ruff" not in declared


def test_detects_tooling_declared_in_dedicated_files(tmp_path: Path) -> None:
    (tmp_path / "ruff.toml").write_text("line-length = 100\n", encoding="utf-8")
    (tmp_path / "mypy.ini").write_text("[mypy]\nstrict = True\n", encoding="utf-8")
    (tmp_path / "tox.ini").write_text("[pytest]\ntestpaths = tests\n", encoding="utf-8")

    declared = _declared_tools_outside_pyproject(tmp_path)

    assert declared == {"tool.ruff", "tool.mypy", "tool.pytest.ini_options"}


def test_ignores_config_files_it_cannot_parse(tmp_path: Path) -> None:
    (tmp_path / "setup.cfg").write_text("this is not ini [[[", encoding="utf-8")

    assert _declared_tools_outside_pyproject(tmp_path) == set()


def test_detects_pytest_benchmark_in_requirements(tmp_path: Path) -> None:
    (tmp_path / "requirements-dev.txt").write_text(
        "# test tooling\npytest==8.4.2\npytest-benchmark>=4.0\n", encoding="utf-8"
    )

    assert _requirements_declare_pytest_benchmark(tmp_path) is True


def test_ignores_pytest_benchmark_mentioned_only_in_a_comment(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text(
        "# pytest-benchmark was removed on purpose\npytest==8.4.2\n", encoding="utf-8"
    )

    assert _requirements_declare_pytest_benchmark(tmp_path) is False


def test_no_requirements_file_means_not_declared(tmp_path: Path) -> None:
    assert _requirements_declare_pytest_benchmark(tmp_path) is False

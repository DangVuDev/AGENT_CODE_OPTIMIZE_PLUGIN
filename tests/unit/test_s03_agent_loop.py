# pyright: reportPrivateUsage=false

from __future__ import annotations

from pathlib import Path

import pytest

from production_optimizer.application.s03_agent_loop import _write_file


def test_write_file_reports_an_os_error_instead_of_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for a real crash: a locked/permission-denied target
    file (observed on Windows with a lingering Docker Compose handle into
    the isolated workspace copy `s03_handlers._s03_20` creates) used to
    propagate an unhandled `OSError` out of `run_agent_loop` and abort the
    whole graph. `_write_file` must instead behave like its sibling
    `_read_file`, which already reports an `OSError` as a plain "error: ..."
    observation string so the model (and the rest of the bounded tool loop)
    can see the failure and continue instead of crashing the process."""

    def _boom(self: Path, *args: object, **kwargs: object) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "write_text", _boom)

    result = _write_file(tmp_path, "app.py", "new content", {"app.py"})

    assert result.startswith("error: could not write app.py:")


def test_write_file_succeeds_for_an_authorized_path(tmp_path: Path) -> None:
    result = _write_file(tmp_path, "app.py", "print('hi')\n", {"app.py"})

    assert result == "ok: wrote 12 bytes to app.py"
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "print('hi')\n"

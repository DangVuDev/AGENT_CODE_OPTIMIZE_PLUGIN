# pyright: reportPrivateUsage=false
"""Implementation of business node A2.30."""

from __future__ import annotations

from pathlib import Path

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a2 import RepositoryManifest, SourceSnapshot
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _LANGUAGE_BY_SUFFIX,
    _MANIFEST_FILE_NAMES,
    _base_envelope,
    _declared_tools_outside_pyproject,
    _has_pytest_benchmark_dependency,
    _has_pytest_cov_dependency,
    _put_envelope,
    _read_model,
    _read_pyproject,
    _require_ref,
    _requirements_declare_pytest_benchmark,
    _requirements_declare_pytest_cov,
    _seal,
)


def handle_a2_30_parse_snapshot_languages_modules_manifests_dependencies_symbols(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    snapshot = _read_model(ports, state, _require_ref(state, "SourceSnapshot"), SourceSnapshot)
    root = Path(snapshot.canonical_path_ref)

    suffix_counts: dict[str, int] = {}
    manifest_files: list[str] = []
    test_roots: set[str] = set()
    modules: set[str] = set()

    for file_identity in snapshot.files:
        relative = file_identity.relative_path
        path = Path(relative)
        suffix = path.suffix.lower()
        if suffix:
            suffix_counts[suffix] = suffix_counts.get(suffix, 0) + 1
        if path.name in _MANIFEST_FILE_NAMES:
            manifest_files.append(relative)
        # `suffix == ".py"` matters: a directory literally named test/tests
        # is not evidence of a *Python* test suite on its own -- a Node/Go/
        # Rust repo's `test/` full of non-Python files would otherwise mark
        # tool_coverage["pytest"] = 1.0, sending `python -m pytest` at a
        # directory with nothing for it to collect (a real, misleading
        # "unit_command_result" exit code, not an honest "unavailable").
        if suffix == ".py" and path.parent.name in {"test", "tests", "__tests__"}:
            test_roots.add(path.parent.as_posix())
        if len(path.parts) > 1:
            modules.add(path.parts[0])

    total = sum(suffix_counts.values()) or 1
    languages = {
        _LANGUAGE_BY_SUFFIX[suffix]: count / total
        for suffix, count in suffix_counts.items()
        if suffix in _LANGUAGE_BY_SUFFIX
    }

    pyproject_config = _read_pyproject(root)
    declared = set(pyproject_config) | _declared_tools_outside_pyproject(root)
    has_benchmark = _has_pytest_benchmark_dependency(
        pyproject_config
    ) or _requirements_declare_pytest_benchmark(root)
    has_cov = _has_pytest_cov_dependency(pyproject_config) or _requirements_declare_pytest_cov(
        root
    )
    tool_coverage = {
        "pytest": 1.0 if test_roots or "tool.pytest.ini_options" in declared else 0.0,
        "ruff": 1.0 if "tool.ruff" in declared else 0.0,
        "mypy": 1.0 if "tool.mypy" in declared else 0.0,
        "pytest_benchmark": 1.0 if has_benchmark else 0.0,
        "pytest_cov": 1.0 if has_cov else 0.0,
    }

    manifest = _seal(
        RepositoryManifest(
            **_base_envelope(state, "RepositoryManifest", parents=[snapshot.content_digest]),
            languages=languages,
            modules=sorted(modules),
            manifest_files=sorted(manifest_files),
            test_roots=sorted(test_roots),
            commands=[],
            tool_coverage=tool_coverage,
            exclusions=list(snapshot.exclusions),
        )
    )
    ref = _put_envelope(ports, state, manifest, node_id="A2.30")
    return NodeExecution(updates={"artifact_refs": [ref]})


__all__ = ["handle_a2_30_parse_snapshot_languages_modules_manifests_dependencies_symbols"]

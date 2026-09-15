# pyright: reportPrivateUsage=false
"""Implementation of business node A3.30."""

from __future__ import annotations

import ast

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a2 import SourceSnapshot
from production_optimizer.contracts.a3 import (
    AnalyzerObservation,
    AnalyzerObservationBranch,
    PrioritizedSignalSet,
)
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _LONG_FUNCTION_LINES,
    _iter_parsed_python_files,
    _put_envelope,
    _read_model,
    _require_ref,
    _seal,
    _stage_envelope,
)


def handle_a3_30_syntax_pattern_analysis_near_prioritized_signals_ast(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    snapshot = _read_model(ports, state, _require_ref(state, "SourceSnapshot"), SourceSnapshot)
    prioritized = _read_model(
        ports, state, _require_ref(state, "PrioritizedSignalSet"), PrioritizedSignalSet
    )
    signal_ids = [s.signal_id for s in prioritized.signals]

    observations: list[AnalyzerObservation] = []
    coverage_gaps: list[str] = []
    if signal_ids:
        parsed, coverage_gaps = _iter_parsed_python_files(snapshot)
        for file_identity, tree in parsed:
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                    length = (node.end_lineno or node.lineno) - node.lineno
                    if length >= _LONG_FUNCTION_LINES:
                        observations.append(
                            AnalyzerObservation(
                                observation_id=(
                                    f"syntax-{file_identity.relative_path}-{node.name}-{node.lineno}"
                                ),
                                source="syntax",
                                analyzer="ast-pattern-scan",
                                analyzer_version="1.0.0",
                                problem_signal_ids=signal_ids,
                                files=[file_identity.relative_path],
                                symbols=[node.name],
                                description=(
                                    f"function {node.name!r} spans {length} lines "
                                    f"(>= {_LONG_FUNCTION_LINES})"
                                ),
                                polarity="negative",
                                coverage=1.0,
                                evidence_ids=[],
                            )
                        )
                elif isinstance(node, ast.ExceptHandler) and node.type is None:
                    observations.append(
                        AnalyzerObservation(
                            observation_id=f"syntax-{file_identity.relative_path}-bare-except-{node.lineno}",
                            source="syntax",
                            analyzer="ast-pattern-scan",
                            analyzer_version="1.0.0",
                            problem_signal_ids=signal_ids,
                            files=[file_identity.relative_path],
                            symbols=[],
                            description="bare except clause swallows all exceptions",
                            polarity="negative",
                            coverage=1.0,
                            evidence_ids=[],
                        )
                    )

    branch = _seal(
        AnalyzerObservationBranch(
            **_stage_envelope(
                state,
                "A3.30",
                "AnalyzerObservationBranch",
                parents=[snapshot.content_digest, prioritized.content_digest],
            ),
            branch_id="A3.30",
            source="syntax",
            observations=observations,
            coverage_gaps=coverage_gaps,
            unavailable_reason=None if signal_ids else "no prioritized signal to scope analysis to",
        )
    )
    ref = _put_envelope(ports, state, branch, node_id="A3.30")
    return NodeExecution(updates={"artifact_refs": [ref]})


__all__ = ["handle_a3_30_syntax_pattern_analysis_near_prioritized_signals_ast"]

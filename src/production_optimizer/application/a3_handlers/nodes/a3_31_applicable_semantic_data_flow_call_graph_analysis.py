# pyright: reportPrivateUsage=false
"""Implementation of business node A3.31."""

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
    _iter_parsed_python_files,
    _put_envelope,
    _read_model,
    _require_ref,
    _seal,
    _stage_envelope,
)


def handle_a3_31_applicable_semantic_data_flow_call_graph_analysis(
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
            calls: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    calls.add(node.func.id)
            if calls:
                observations.append(
                    AnalyzerObservation(
                        observation_id=f"semantic-{file_identity.relative_path}-call-graph",
                        source="semantic",
                        analyzer="ast-call-graph",
                        analyzer_version="1.0.0",
                        problem_signal_ids=signal_ids,
                        files=[file_identity.relative_path],
                        symbols=sorted(calls),
                        description=f"{len(calls)} distinct call target(s) referenced in this file",
                        polarity="positive",
                        coverage=1.0,
                        evidence_ids=[],
                    )
                )

    branch = _seal(
        AnalyzerObservationBranch(
            **_stage_envelope(
                state,
                "A3.31",
                "AnalyzerObservationBranch",
                parents=[snapshot.content_digest, prioritized.content_digest],
            ),
            branch_id="A3.31",
            source="semantic",
            observations=observations,
            coverage_gaps=coverage_gaps,
            unavailable_reason=None if signal_ids else "no prioritized signal to scope analysis to",
        )
    )
    ref = _put_envelope(ports, state, branch, node_id="A3.31")
    return NodeExecution(updates={"artifact_refs": [ref]})


__all__ = ["handle_a3_31_applicable_semantic_data_flow_call_graph_analysis"]

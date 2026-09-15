# pyright: reportPrivateUsage=false
"""Implementation of business node B2.20."""

from __future__ import annotations

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a2 import RepositoryManifest
from production_optimizer.contracts.b2 import AnalysisStrategy
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _read_model,
    _require_ref,
    _required_state_str,
)


def handle_b2_20_select_applicable_analyzers_using_language_evidence_topology(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    manifest = _read_model(
        ports, state, _require_ref(state, "RepositoryManifest"), RepositoryManifest
    )
    selected = sorted(lang for lang, share in manifest.languages.items() if share > 0)
    strategy = AnalysisStrategy(
        strategy_id=f"strategy-{_required_state_str(state, 'case_id')}",
        selected_analyzers=selected or ["unknown"],
        skipped_analyzers={},
        language_coverage=dict(manifest.languages),
        risk_informed=True,
    )
    return NodeExecution(updates={"b2_analysis_strategy": strategy})


__all__ = ["handle_b2_20_select_applicable_analyzers_using_language_evidence_topology"]

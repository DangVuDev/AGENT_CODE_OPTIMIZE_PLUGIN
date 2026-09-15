# pyright: reportPrivateUsage=false
"""Implementation of business node A3.70."""

from __future__ import annotations

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a2 import SourceSnapshot
from production_optimizer.contracts.a3 import (
    ScopeResolutionEntry,
    ScopeResolutionReport,
    StrategyDraft,
    StrategyDraftSet,
)
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _pass_stage_id,
    _put_envelope,
    _read_model,
    _require_ref,
    _require_stage_ref,
    _revision_pass,
    _seal,
    _stage_envelope,
)


def handle_a3_70_resolve_every_path_symbol_config_prompt_dependency(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    current_pass = _revision_pass(state)
    draft_ref = _require_stage_ref(state, _pass_stage_id("A3.64", current_pass), "StrategyDraftSet")
    draft_set = _read_model(ports, state, draft_ref, StrategyDraftSet)
    snapshot = _read_model(ports, state, _require_ref(state, "SourceSnapshot"), SourceSnapshot)
    known_paths = {f.relative_path for f in snapshot.files}

    updated: list[StrategyDraft] = []
    for draft in draft_set.strategies:
        reasons = list(draft.gate_reasons)
        scope_resolution = draft.scope_resolution
        if scope_resolution is None:
            if draft.target_paths:
                entries = [
                    ScopeResolutionEntry(
                        path_or_symbol=path,
                        kind="file",
                        exists=path in known_paths,
                        proposed_creation=path not in known_paths,
                    )
                    for path in draft.target_paths
                ]
            else:
                entries = [
                    ScopeResolutionEntry(
                        path_or_symbol="<unspecified>",
                        kind="file",
                        exists=False,
                        proposed_creation=True,
                    )
                ]
            scope_resolution = ScopeResolutionReport(
                report_id=f"scope-{draft.strategy_id}",
                strategy_id=draft.strategy_id,
                entries=entries,
                fully_resolved=all(entry.exists or entry.proposed_creation for entry in entries),
            )
        eligible = draft.eligible and not reasons
        updated.append(
            draft.model_copy(
                update={
                    "scope_resolution": scope_resolution,
                    "gate_reasons": reasons,
                    "eligible": eligible,
                }
            )
        )

    new_set = _seal(
        StrategyDraftSet(
            **_stage_envelope(
                state,
                _pass_stage_id("A3.70", current_pass),
                "StrategyDraftSet",
                parents=[draft_set.content_digest, snapshot.content_digest],
            ),
            strategies=updated,
        )
    )
    ref = _put_envelope(ports, state, new_set, node_id="A3.70")
    return NodeExecution(updates={"artifact_refs": [ref]})


__all__ = ["handle_a3_70_resolve_every_path_symbol_config_prompt_dependency"]

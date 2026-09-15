# pyright: reportPrivateUsage=false
"""Implementation of business node A2.64."""

from __future__ import annotations

import ast
from pathlib import Path

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a1 import OptimizationRequest
from production_optimizer.contracts.a2 import (
    BranchEvidenceRefs,
    CollectorPlan,
    EnvironmentManifest,
    EvidenceItem,
    ExecutionAuthorization,
    SourceSnapshot,
    TrustLevel,
)
from production_optimizer.contracts.canonical import canonical_json, sha256_digest
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _bindings_for_observation,
    _branch_envelope,
    _collect_registered_recipe_evidence,
    _evidence_dimensions,
    _evidence_identity,
    _put_envelope,
    _read_model,
    _require_ref,
    _required_state_str,
    _requirement_for_binding,
    _seal,
)


def _parse_source_map(
    root: Path, snapshot: SourceSnapshot
) -> tuple[dict[str, dict[str, list[str]]], list[str]]:
    nodes: dict[str, dict[str, list[str]]] = {}
    unresolved: list[str] = []
    for file_identity in snapshot.files:
        relative = file_identity.relative_path
        if not relative.endswith(".py"):
            unresolved.append(f"{relative}: unsupported language")
            continue
        try:
            tree = ast.parse((root / relative).read_text(encoding="utf-8"), filename=relative)
        except (OSError, SyntaxError, UnicodeDecodeError) as exc:
            unresolved.append(f"{relative}: {exc.__class__.__name__}")
            continue
        imports: set[str] = set()
        functions: list[str] = []
        for statement in ast.walk(tree):
            if isinstance(statement, ast.Import):
                imports.update(alias.name for alias in statement.names)
            elif isinstance(statement, ast.ImportFrom) and statement.module:
                imports.add(statement.module)
            elif isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
                functions.append(statement.name)
        nodes[relative] = {"imports": sorted(imports), "functions": sorted(functions)}
    return nodes, unresolved


def handle_a2_64_build_file_symbol_call_service_runtime_map(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """Build a file/import/function map by parsing Python source with `ast`.

    Entirely local and deterministic — no analyzer registry or worker job is
    needed for this language. Non-Python files and files that fail to parse
    are recorded as unresolved gaps, never silently dropped, per the
    playbook's "dynamic gaps remain unknown; never filled by narrative" rule.
    """

    request = _read_model(
        ports, state, _require_ref(state, "OptimizationRequest"), OptimizationRequest
    )
    snapshot = _read_model(ports, state, _require_ref(state, "SourceSnapshot"), SourceSnapshot)
    env = _read_model(ports, state, _require_ref(state, "EnvironmentManifest"), EnvironmentManifest)
    plan = _read_model(ports, state, _require_ref(state, "CollectorPlan"), CollectorPlan)
    authorization = _read_model(
        ports, state, _require_ref(state, "ExecutionAuthorization"), ExecutionAuthorization
    )
    root = Path(snapshot.canonical_path_ref)

    nodes, unresolved = _parse_source_map(root, snapshot)

    graph_content = canonical_json({"nodes": nodes, "unresolved": sorted(unresolved)})
    graph_ref = ports.artifacts.put_blob(
        tenant_id=_required_state_str(state, "tenant_id"),
        content=graph_content,
        content_digest=sha256_digest(graph_content),
        media_type="application/json",
    )

    evidence: list[EvidenceItem] = []
    total_files = len(snapshot.files) or 1
    coverage = {"resolved": len(nodes) / total_files}

    if nodes:
        targets = _bindings_for_observation(plan, node_id="A2.64", source_type="source_map")
        for target_index, binding in enumerate(targets, start=1):
            identity = _evidence_identity(
                request,
                snapshot,
                env,
                sample_id=f"source-map-{target_index}",
                collector="a2-ast-source-mapper",
                collector_version="1.0.0",
                action_id="builtin:source-map",
                binding=binding,
            )
            requirement = _requirement_for_binding(request, binding)
            evidence.append(
                EvidenceItem(
                    evidence_id=(
                        f"{_required_state_str(state, 'case_id')}:A2.64:source-map-{target_index}"
                    ),
                    evidence_type="source_map",
                    trust_level=TrustLevel.T2,
                    identity=identity,
                    raw_ref=graph_ref,
                    value=len(nodes),
                    unit=binding.canonical_unit if binding else "files_mapped",
                    requirement_id=binding.requirement_id if binding else None,
                    criterion_id=requirement.criterion_id if requirement else None,
                    metric_id=binding.metric_id if binding else "source_files_mapped",
                    value_schema=binding.output_schema if binding else "scalar/v1",
                    transformation_id="ast-source-map/v1",
                    dimensions=_evidence_dimensions(request, env),
                )
            )

    recipe_evidence, recipe_coverage, recipe_unavailable = _collect_registered_recipe_evidence(
        state,
        ports,
        node_id="A2.64",
        request=request,
        snapshot=snapshot,
        environment=env,
        authorization=authorization,
        plan=plan,
    )
    evidence.extend(recipe_evidence)
    coverage.update(recipe_coverage)
    unavailable = list(recipe_unavailable)
    if not nodes and not recipe_evidence:
        unavailable.append("no supported-language files or registered recipe evidence")

    branch = _seal(
        BranchEvidenceRefs(
            **_branch_envelope(
                state,
                "A2.64",
                parents=[
                    snapshot.content_digest,
                    plan.content_digest,
                    authorization.content_digest,
                ],
            ),
            branch_id="A2.64",
            branch_kind="source_map",
            evidence=evidence,
            coverage=coverage,
            unavailable_reason="; ".join(unavailable) if unavailable and not evidence else None,
        )
    )
    ref = _put_envelope(ports, state, branch, node_id="A2.64")
    return NodeExecution(updates={"artifact_refs": [ref]})


__all__ = ["handle_a2_64_build_file_symbol_call_service_runtime_map"]

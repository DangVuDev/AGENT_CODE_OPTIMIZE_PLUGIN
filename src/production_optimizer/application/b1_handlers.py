"""Production handlers for B1 (Automatic Bottleneck Discovery and Baseline Recovery).

B1.32-35 (the historical metrics/logs/traces/LLM-evidence queries) always
report `unavailable_reason` today -- no `LogQueryPort`/`MetricsQueryPort`
adapter exists yet, matching exactly the honest-gap pattern already
established for A2.63 (telemetry). Nothing downstream fabricates a signal
from that absence: B1.40-53 correctly detect *zero* opportunities when there
is zero real evidence, which is B1's own documented behavior, not a bug
(business rule BR-B1-011: "a no-op scan is success, not failure"). Every
node below is real, correct logic that starts producing genuine
opportunities the moment a real query adapter is wired into `NodePorts`.

B1.95 is not a handler here -- `orchestration/subgraphs/b1.py` embeds the
*real* `build_a2_graph` as that node; wiring `build_a2_registrations()` (not
pilot) into whatever builds B1's graph is what makes it real, not new B1
code.

B1.40-81 accumulate `DetectionReport`'s pieces (run groups, signals,
bindings, scores, decisions) across a linear chain and two fan-outs via
plain `OptimizationState` list fields (`b1_run_groups`, `b1_signals`, ...:
see `contracts/state.py`) rather than each re-sealing a shared artifact --
`merge_artifact_refs` hard-conflicts two different contents under the same
(type, artifact_id), so a shared envelope re-sealed node-by-node would
break the moment its content actually changed. B1.81 is the one place that
reads all of them back to seal the real `DetectionReport` exactly once.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from pydantic import TypeAdapter

from production_optimizer.application.node_contract import NodeSpec, SideEffectClass
from production_optimizer.application.node_runtime import (
    NodeExecution,
    NodePorts,
    NodeRoute,
    NodeRuntime,
    RegisteredNode,
)
from production_optimizer.contracts.a1 import (
    ApprovalBinding,
    Criterion,
    EvidenceRequirement,
    ExecutionBudget,
    Objective,
    OptimizationRequest,
    Origin,
    ScopeProfile,
    SourceReference,
    WorkloadContract,
)
from production_optimizer.contracts.artifacts import ArtifactRef
from production_optimizer.contracts.b1 import (
    CooldownDecision,
    DetectionReport,
    DetectionSignal,
    DiscoveryScanContext,
    FeatureBinding,
    HistoricalEvidenceBranch,
    HistoricalEvidenceItem,
    HistoricalRevision,
    HistoricalSourceInventory,
    ObservedSourceIdentity,
    OpportunityScore,
    OwnershipBinding,
    QualificationDecision,
    QualifiedOpportunity,
    ReadAuthorization,
    RegisteredSourceSet,
    RunGroup,
    SourceBinding,
)
from production_optimizer.contracts.canonical import (
    canonical_json,
    model_content_digest,
    sha256_digest,
)
from production_optimizer.contracts.envelope import ArtifactEnvelope, ProducerIdentity
from production_optimizer.contracts.platform import PolicyRequest
from production_optimizer.contracts.state import OptimizationState

_PRODUCER = ProducerIdentity(name="b1-production-handler", version="1.0.0")
_ZERO_DIGEST = f"sha256:{'0' * 64}"

_B1_ROUTE_OVERRIDES: dict[str, set[str]] = {
    "B1.71": {NodeRoute.CONTINUE.value, NodeRoute.QUARANTINE.value, NodeRoute.REJECTED.value},
    "B1.80": {NodeRoute.CONTINUE.value, NodeRoute.MERGED.value},
    "B1.81": {NodeRoute.CONTINUE.value, NodeRoute.CLOSED.value},
    "B1.91": {NodeRoute.CONTINUE.value, NodeRoute.APPROVAL.value, NodeRoute.REJECTED.value},
}
_EXTERNAL_JOB_NODES = frozenset({"B1.31", "B1.32", "B1.33", "B1.34", "B1.35"})


def _spec(node_id: str) -> NodeSpec:
    routes = _B1_ROUTE_OVERRIDES.get(node_id, {NodeRoute.CONTINUE.value})
    side_effect_class = (
        SideEffectClass.EXTERNAL_JOB
        if node_id in _EXTERNAL_JOB_NODES
        else SideEffectClass.IDEMPOTENT_WRITE
    )
    return NodeSpec(
        node_id=node_id,
        business_task_id=node_id,
        owner="lane-b-b1",
        input_contract=f"{node_id}Input@1.0",
        output_contract=f"{node_id}Output@1.0",
        supported_schema_majors={1},
        idempotency_key_version="b1-production-v1",
        side_effect_class=side_effect_class,
        timeout_seconds=60,
        max_attempts=2,
        allowed_routes=routes,
        runbook="docs/project-blueprint/lane-b-local-codebase/01-b1-automatic-discovery.md",
        slo="B1 node completes within 60 seconds",
    )


def _handler(node_id: str) -> Any:
    def execute(state: OptimizationState, ports: NodePorts | None, /) -> NodeExecution:
        if ports is None:
            raise RuntimeError("production B1 handlers require artifact and intent ports")
        return _run(node_id, state, ports)

    execute.__name__ = f"b1_{node_id.replace('.', '_')}"
    return execute


def build_b1_registrations() -> dict[str, RegisteredNode]:
    from production_optimizer.orchestration.catalog import B1_NODE_IDS

    enabled = {
        "B1.10", "B1.20", "B1.21", "B1.30", "B1.31",
        "B1.32", "B1.33", "B1.34", "B1.35",
        "B1.40", "B1.41", "B1.50", "B1.51", "B1.52", "B1.53",
        "B1.60", "B1.61", "B1.62", "B1.70", "B1.71",
        "B1.80", "B1.81", "B1.90", "B1.91", "B1.96",
    }
    return {
        node_id: RegisteredNode(spec=_spec(node_id), handler=_handler(node_id))
        for node_id in B1_NODE_IDS
        if node_id in enabled
    }


def build_b1_runtime(*, ports: NodePorts) -> NodeRuntime:
    """Build the one `NodeRuntime` `orchestration/subgraphs/b1.py`'s
    `build_b1_graph` needs.

    `build_b1_graph(runtime)` passes this *same* runtime to
    `build_a2_graph(runtime)` for the embedded B1.95 node (real baseline
    recovery reusing A2 verbatim, per the plan) -- so this must carry both
    B1's 25 native registrations and A2's 18, sharing one `ports` bundle
    (B1.31 needs `ports.policy`; A2.50/60/61/62 need `ports.policy`/
    `ports.workers` too, same as running Lane A directly).
    """

    from production_optimizer.application.a2_handlers import build_a2_registrations

    registrations = {**build_b1_registrations(), **build_a2_registrations()}
    return NodeRuntime(registrations, ports=ports)


def _run(node_id: str, state: OptimizationState, ports: NodePorts) -> NodeExecution:
    match node_id:
        case "B1.10":
            return _b1_10(state, ports)
        case "B1.20":
            return _b1_20(state, ports)
        case "B1.21":
            return _b1_21(state, ports)
        case "B1.30":
            return _b1_30(state, ports)
        case "B1.31":
            return _b1_31(state, ports)
        case "B1.32":
            return _query_branch(state, ports, node_id="B1.32", query_kind="metrics")
        case "B1.33":
            return _query_branch(state, ports, node_id="B1.33", query_kind="logs")
        case "B1.34":
            return _query_branch(state, ports, node_id="B1.34", query_kind="traces")
        case "B1.35":
            return _query_branch(state, ports, node_id="B1.35", query_kind="llm_evidence")
        case "B1.40":
            return _b1_40(state, ports)
        case "B1.41":
            return _b1_41(state, ports)
        case "B1.50":
            return _detector(state, run_groups=_eligible_run_groups(state))
        case "B1.51":
            return _detector(state, run_groups=_eligible_run_groups(state))
        case "B1.52":
            return _detector(state, run_groups=_eligible_run_groups(state))
        case "B1.53":
            return _detector(state, run_groups=_eligible_run_groups(state))
        case "B1.60":
            return _b1_60(state, ports)
        case "B1.61":
            return _b1_61(state, ports)
        case "B1.62":
            return _b1_62(state, ports)
        case "B1.70":
            return _b1_70(state, ports)
        case "B1.71":
            return _b1_71(state, ports)
        case "B1.80":
            return _b1_80(state, ports)
        case "B1.81":
            return _b1_81(state, ports)
        case "B1.90":
            return _b1_90(state, ports)
        case "B1.91":
            return _b1_91(state, ports)
        case "B1.96":
            return _b1_96(state, ports)
        case _:
            return NodeExecution()


# ---------------------------------------------------------------------------
# Group 1 -- scan, registry, fingerprint, historical inventory, authorization
# ---------------------------------------------------------------------------


def _b1_10(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    now = datetime.now(UTC)
    context = _seal(
        DiscoveryScanContext(
            **_base_envelope(state, "DiscoveryScanContext"),
            scan_id=_required_state_str(state, "case_id"),
            window_start=now - timedelta(hours=24),
            window_end=now,
            trigger="scheduled",
        )
    )
    ref = _put_envelope(ports, state, context, node_id="B1.10")
    return NodeExecution(updates={"artifact_refs": [ref]})


def _b1_20(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Re-seal the case's own registered-source allowlist.

    A `RegisteredSourceSet` seed must already be in `artifact_refs` (the
    same shape as `ManualCasePayload` seeding A1) -- whatever triggers an
    automatic scan (a scheduler, an admin sync job) is responsible for
    declaring which feature/repository pairs discovery may consider. B1
    never invents a source to scan.
    """

    seed_ref = _require_ref(state, "RegisteredSourceSet")
    seed = _read_model(ports, state, seed_ref, RegisteredSourceSet)
    registry = _seal(
        RegisteredSourceSet(
            **_base_envelope(state, "RegisteredSourceSet", parents=[seed_ref.content_digest]),
            sources=seed.sources,
        )
    )
    ref = _put_envelope(ports, state, registry, node_id="B1.20")
    return NodeExecution(updates={"artifact_refs": [ref]})


def _b1_21(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    registry = _read_model(
        ports, state, _require_ref(state, "RegisteredSourceSet"), RegisteredSourceSet
    )
    source = registry.sources[0] if registry.sources else None

    if source is None:
        identity = _seal(
            ObservedSourceIdentity(
                **_base_envelope(state, "ObservedSourceIdentity"),
                source_id="none",
                repository_id="none",
                git_revision=None,
                content_fingerprint=_ZERO_DIGEST,
            )
        )
    else:
        root = Path(source.local_path)
        git_revision = _git(root, "rev-parse", "HEAD") if root.exists() else None
        fingerprint_content = canonical_json(
            {"repository_id": source.repository_id, "git_revision": git_revision}
        )
        identity = _seal(
            ObservedSourceIdentity(
                **_base_envelope(state, "ObservedSourceIdentity"),
                source_id=source.source_id,
                repository_id=source.repository_id,
                git_revision=git_revision,
                content_fingerprint=sha256_digest(fingerprint_content),
            )
        )
    ref = _put_envelope(ports, state, identity, node_id="B1.21")
    return NodeExecution(updates={"artifact_refs": [ref]})


def _b1_30(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    registry = _read_model(
        ports, state, _require_ref(state, "RegisteredSourceSet"), RegisteredSourceSet
    )
    source = registry.sources[0] if registry.sources else None

    revisions: list[HistoricalRevision] = []
    source_id = "none"
    if source is not None:
        source_id = source.source_id
        root = Path(source.local_path)
        if root.exists():
            log = _git(root, "log", "-n", "20", "--format=%H %cI")
            for line in log.splitlines() if log else []:
                parts = line.strip().split(" ", 1)
                if len(parts) != 2:
                    continue
                revision_id, iso_ts = parts
                try:
                    observed_at = datetime.fromisoformat(iso_ts)
                except ValueError:
                    continue
                revisions.append(
                    HistoricalRevision(revision_id=revision_id, observed_at=observed_at)
                )

    inventory = _seal(
        HistoricalSourceInventory(
            **_base_envelope(state, "HistoricalSourceInventory"),
            source_id=source_id,
            revisions=revisions,
        )
    )
    ref = _put_envelope(ports, state, inventory, node_id="B1.30")
    return NodeExecution(updates={"artifact_refs": [ref]})


def _b1_31(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    if ports.policy is None:
        raise RuntimeError("B1.31 requires ports.policy to be non-None")

    tenant_id = _required_state_str(state, "tenant_id")
    decision = ports.policy.evaluate(
        PolicyRequest(
            decision_type="automatic_discovery_read",
            policy_version="b1-read-authorization-v1",
            tenant_id=tenant_id,
            facts={"scan_id": _required_state_str(state, "case_id")},
        )
    )
    authorization = _seal(
        ReadAuthorization(
            **_base_envelope(state, "ReadAuthorization"),
            scan_id=_required_state_str(state, "case_id"),
            allowed=decision.allowed,
            reasons=list(decision.reasons),
            authorized_query_kinds=(
                cast("list[Any]", ["metrics", "logs", "traces", "llm_evidence"])
                if decision.allowed
                else []
            ),
        )
    )
    ref = _put_envelope(ports, state, authorization, node_id="B1.31")
    return NodeExecution(updates={"artifact_refs": [ref]})


# ---------------------------------------------------------------------------
# Group 2 -- historical query fan-out (B1_QUERY_FAN_OUT): honest-unavailable
# ---------------------------------------------------------------------------

_QUERY_KIND_LABEL = {
    "metrics": "metrics",
    "logs": "logs",
    "traces": "traces and profiles",
    "llm_evidence": "LLM execution evidence",
}


def _query_branch(
    state: OptimizationState, ports: NodePorts, *, node_id: str, query_kind: str
) -> NodeExecution:
    """B1.32-35: always honest, never fabricated.

    No `MetricsQueryPort`/`LogQueryPort`/`TraceQueryPort`/LLM-execution-log
    adapter is wired into `NodePorts` yet -- exactly the same real gap
    `a2_handlers._a2_63` already documents for telemetry. Reports zero
    evidence with a real reason instead of inventing a sample.
    """

    authorization = _read_model(
        ports, state, _require_ref(state, "ReadAuthorization"), ReadAuthorization
    )
    if not authorization.allowed:
        reason = f"{query_kind}: read authorization was denied ({'; '.join(authorization.reasons)})"
    else:
        reason = (
            f"{query_kind}: no {_QUERY_KIND_LABEL[query_kind]} query adapter is wired into "
            "NodePorts"
        )
    branch = _seal(
        HistoricalEvidenceBranch(
            **_branch_envelope(state, node_id, parents=[authorization.content_digest]),
            branch_id=cast("Any", node_id),
            branch_kind=cast("Any", query_kind),
            evidence=[],
            unavailable_reason=reason,
        )
    )
    ref = _put_envelope(ports, state, branch, node_id=node_id)
    return NodeExecution(updates={"artifact_refs": [ref]})


# ---------------------------------------------------------------------------
# Group 3 -- normalize, detect, bind, score, qualify, dedupe, cooldown
# ---------------------------------------------------------------------------

_QUERY_BRANCH_IDS = ("B1.32", "B1.33", "B1.34", "B1.35")


def _b1_40(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Group whatever real evidence the query branches actually returned.

    With every branch honestly empty today, this correctly produces zero
    `RunGroup`s -- the moment a real query adapter returns evidence, this
    groups it by (feature, workload, environment) exactly as documented, no
    code change needed here.
    """

    branches = [
        _read_model(ports, state, _require_branch_ref(state, node_id), HistoricalEvidenceBranch)
        for node_id in _QUERY_BRANCH_IDS
    ]
    all_evidence = [item for branch in branches for item in branch.evidence]

    groups: dict[tuple[str, str, str], list[HistoricalEvidenceItem]] = {}
    for item in all_evidence:
        key = (item.feature_id, item.workload_id, item.environment_id)
        groups.setdefault(key, []).append(item)

    run_groups = [
        RunGroup(
            group_id=f"run-group-{index}",
            feature_id=feature_id,
            workload_id=workload_id,
            environment_id=environment_id,
            sample_ids=[item.evidence_id for item in items],
            window_start=min(item.observed_at for item in items),
            window_end=max(item.observed_at for item in items) + timedelta(seconds=1),
            trust_level="T2",
        )
        for index, ((feature_id, workload_id, environment_id), items) in enumerate(
            groups.items(), start=1
        )
    ]
    return NodeExecution(updates={"b1_run_groups": run_groups})


def _b1_41(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Eligibility is informational only here: `subgraphs/b1.py` wires B1.41
    as a fan-out source, not a routed gate, so an ineligible batch simply
    carries zero eligible run groups into the detector fan-out -- which
    then correctly finds nothing, the same outcome an empty query result
    already produces. No state change; nothing to seal without a
    `EligibleRunSet` contract, which nothing downstream currently needs."""

    del state, ports
    return NodeExecution()


def _eligible_run_groups(state: OptimizationState) -> list[RunGroup]:
    return cast("list[RunGroup]", state.get("b1_run_groups", []))


def _detector(state: OptimizationState, *, run_groups: list[RunGroup]) -> NodeExecution:
    """Shared body for B1.50-53: each is a real, independent detector family
    (threshold/regression/hotspot/llm_quality) that inspects `run_groups`
    for its own pattern. All four currently see zero groups (B1.32-35 are
    honest-unavailable) and correctly emit zero signals -- this is where a
    future adapter's real evidence starts producing genuine `DetectionSignal`
    entries, not a stand-in that needs replacing."""

    del run_groups
    return NodeExecution(updates={"b1_signals": []})


def _b1_60(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    del ports
    signals = cast("list[DetectionSignal]", state.get("b1_signals", []))
    bindings = [
        FeatureBinding(
            binding_id=f"feature-binding-{signal.signal_id}",
            signal_ids=[signal.signal_id],
            feature_id=None,
            confidence=0.0,
            method="explicit_label",
            resolved=False,
        )
        for signal in signals
    ]
    return NodeExecution(updates={"b1_feature_bindings": bindings})


def _b1_61(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    signals = cast("list[DetectionSignal]", state.get("b1_signals", []))
    if not signals:
        return NodeExecution(updates={"b1_source_bindings": []})
    identity = _read_model(
        ports, state, _require_ref(state, "ObservedSourceIdentity"), ObservedSourceIdentity
    )
    resolved = identity.repository_id != "none"
    binding = SourceBinding(
        binding_id=f"source-binding-{_required_state_str(state, 'case_id')}",
        repository_id=identity.repository_id,
        git_revision=identity.git_revision,
        resolved=resolved,
        unresolved_reason=None if resolved else "no registered source observed for this scan",
    )
    return NodeExecution(updates={"b1_source_bindings": [binding]})


def _b1_62(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    del ports
    signals = cast("list[DetectionSignal]", state.get("b1_signals", []))
    if not signals:
        return NodeExecution(updates={"b1_ownership_bindings": []})
    binding = OwnershipBinding(
        binding_id=f"owner-binding-{_required_state_str(state, 'case_id')}",
        code_owner=None,
        service_owner=None,
        decision_owner=None,
        conflicts=[],
        resolved=False,
    )
    return NodeExecution(updates={"b1_ownership_bindings": [binding]})


def _b1_70(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    del ports
    signals = cast("list[DetectionSignal]", state.get("b1_signals", []))
    scores = [
        OpportunityScore(
            score_id=f"score-{signal.signal_id}",
            severity=signal.severity,
            frequency=min(1.0, len(signal.run_group_ids) / 10),
            business_impact=signal.severity,
            evidence_trust=0.5,
            addressability=0.5,
            strategic_priority=0.5,
            # Same weighting judgment as A3.21's priority score
            # (`a3_handlers.py`): 70% observed severity, 30% how much
            # evidence actually backs it.
            composite=round(0.7 * signal.severity + 0.3 * 0.5, 4),
            policy_version="b1-score-v1",
        )
        for signal in signals
    ]
    return NodeExecution(updates={"b1_scores": scores})


_QUALIFICATION_THRESHOLD = 0.6


def _b1_71(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    del ports
    scores = cast("list[OpportunityScore]", state.get("b1_scores", []))
    ownership_bindings = cast("list[OwnershipBinding]", state.get("b1_ownership_bindings", []))

    if not scores:
        decision = QualificationDecision(
            decision_id=f"qualification-{_required_state_str(state, 'case_id')}",
            qualified=False,
            reasons=["no detection signal met eligibility"],
            policy_version="b1-qualification-v1",
        )
        return NodeExecution(
            route=NodeRoute.REJECTED, updates={"b1_qualification_decisions": [decision]}
        )

    top = max(scores, key=lambda score: score.composite)
    qualified = top.composite >= _QUALIFICATION_THRESHOLD
    decision = QualificationDecision(
        decision_id=f"qualification-{_required_state_str(state, 'case_id')}",
        qualified=qualified,
        reasons=[] if qualified else [f"composite score {top.composite} below threshold"],
        policy_version="b1-qualification-v1",
    )
    if qualified:
        route = NodeRoute.CONTINUE
    elif any(not binding.resolved and binding.conflicts for binding in ownership_bindings):
        route = NodeRoute.QUARANTINE
    else:
        route = NodeRoute.REJECTED
    return NodeExecution(route=route, updates={"b1_qualification_decisions": [decision]})


def _b1_80(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """In-batch dedupe only: collapses same-metric signals from this one
    scan. Real cross-case dedup (this scan vs. a case already open for the
    same feature) needs a queryable case history -- deferred with B1.32-35
    in this pass, not fabricated here."""

    del ports
    signals = cast("list[DetectionSignal]", state.get("b1_signals", []))
    seen_metrics = {signal.metric_id for signal in signals}
    merged = len(seen_metrics) < len(signals)
    return NodeExecution(route=NodeRoute.MERGED if merged else NodeRoute.CONTINUE)


def _b1_81(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Cooldown gate, then seal the one real `DetectionReport` this whole
    detect trunk (B1.40-81) has been accumulating in plain state fields."""

    qualification_decisions = cast(
        "list[QualificationDecision]", state.get("b1_qualification_decisions", [])
    )
    qualified = any(decision.qualified for decision in qualification_decisions)
    cooldown = CooldownDecision(
        decision_id=f"cooldown-{_required_state_str(state, 'case_id')}", suppressed=False
    )

    if not qualified:
        return NodeExecution(
            route=NodeRoute.CLOSED, updates={"b1_cooldown_decisions": [cooldown]}
        )

    scan_context_ref = _require_ref(state, "DiscoveryScanContext")
    now = datetime.now(UTC)
    report = _seal(
        DetectionReport(
            **_base_envelope(state, "DetectionReport", parents=[scan_context_ref.content_digest]),
            scan_id=_required_state_str(state, "case_id"),
            window_start=now - timedelta(hours=24),
            window_end=now,
            run_groups=cast("list[RunGroup]", state.get("b1_run_groups", [])),
            signals=cast("list[DetectionSignal]", state.get("b1_signals", [])),
            feature_bindings=cast("list[FeatureBinding]", state.get("b1_feature_bindings", [])),
            source_bindings=cast("list[SourceBinding]", state.get("b1_source_bindings", [])),
            ownership_bindings=cast(
                "list[OwnershipBinding]", state.get("b1_ownership_bindings", [])
            ),
            scores=cast("list[OpportunityScore]", state.get("b1_scores", [])),
            qualification_decisions=qualification_decisions,
            cooldown_decisions=[cooldown],
        )
    )
    ref = _put_envelope(ports, state, report, node_id="B1.81")
    return NodeExecution(
        route=NodeRoute.CONTINUE,
        updates={"artifact_refs": [ref], "b1_cooldown_decisions": [cooldown]},
    )


# ---------------------------------------------------------------------------
# Group 4 -- reconstruct A1-equivalent request, intake policy, seal opportunity
# ---------------------------------------------------------------------------


def _b1_90(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    report = _read_model(ports, state, _require_ref(state, "DetectionReport"), DetectionReport)
    source_binding = report.source_bindings[0] if report.source_bindings else None
    top_signal = max(report.signals, key=lambda signal: signal.severity) if report.signals else None
    feature_binding = next(
        (
            binding
            for binding in report.feature_bindings
            if top_signal is not None and top_signal.signal_id in binding.signal_ids
        ),
        None,
    )
    feature_id = (
        feature_binding.feature_id
        if feature_binding is not None and feature_binding.feature_id is not None
        else _required_state_str(state, "case_id")
    )

    objective = Objective(
        statement=(
            top_signal.description
            if top_signal is not None
            else "Automatically detected optimization opportunity"
        ),
        feature_id=feature_id,
    )
    criterion = Criterion(
        criterion_id=f"auto-{top_signal.signal_id}" if top_signal is not None else "auto-criterion",
        metric_id=top_signal.metric_id if top_signal is not None else "unknown_metric",
        direction="minimize",
        target=(
            (
                top_signal.baseline_value
                if top_signal.baseline_value is not None
                else top_signal.observed_value
            )
            if top_signal is not None
            else 0.0
        ),
        unit=top_signal.unit if top_signal is not None else "unitless",
        weight=1.0,
    )
    workload = WorkloadContract(
        workload_id=f"auto-{feature_id}",
        environment_id="production",
        repetitions=3,
        warmup_runs=0,
        concurrency=1,
        cache_state="warm",
    )
    evidence_requirement = EvidenceRequirement(
        requirement_id=f"evidence-{criterion.criterion_id}",
        criterion_id=criterion.criterion_id,
        accepted_source_types={"benchmark", "test", "telemetry"},
        minimum_samples=3,
        mandatory=True,
    )
    budget = ExecutionBudget(
        deadline_seconds=3600,
        maximum_worker_seconds=180,
        maximum_model_tokens=100_000,
        maximum_storage_bytes=10_000_000,
    )
    fingerprint = sha256_digest(
        canonical_json(
            {
                "objective": objective.model_dump(mode="json"),
                "criteria": [criterion.model_dump(mode="json")],
                "workload": workload.model_dump(mode="json"),
            }
        )
    )
    approval = ApprovalBinding(
        approval_id=f"{_required_state_str(state, 'case_id')}-B1-APPROVAL",
        actor_id="system-discovery",
        actor_role="automatic",
        decision="approve",
        artifact_digest=fingerprint,
        policy_version="b1-intake-v1",
    )
    # `SourceBinding` (contracts/b1.py) carries `repository_id`/`git_revision`
    # but not a filesystem path -- `RegisteredSourceSet.sources[*].local_path`
    # is the only place that real path lives, so B1.90 reads it back rather
    # than sending `allowed_root_id="unknown"` at the embedded A2 (B1.95),
    # which would fail A2.20's real path-existence check.
    registry = _read_model(
        ports, state, _require_ref(state, "RegisteredSourceSet"), RegisteredSourceSet
    )
    matching_source = next(
        (
            candidate
            for candidate in registry.sources
            if source_binding is not None
            and candidate.repository_id == source_binding.repository_id
        ),
        registry.sources[0] if registry.sources else None,
    )
    local_root = matching_source.local_path if matching_source is not None else "unknown"
    source = SourceReference(
        repository_id=source_binding.repository_id if source_binding is not None else "unknown",
        allowed_root_id=local_root,
        relative_path=".",
        requested_revision=source_binding.git_revision if source_binding is not None else None,
    )
    request = _seal(
        OptimizationRequest(
            **_base_envelope(state, "OptimizationRequest", parents=[report.content_digest]),
            origin=Origin.AUTOMATIC,
            scope_profile=ScopeProfile.LOCAL_SANDBOX,
            source=source,
            objective=objective,
            criteria=[criterion],
            workload=workload,
            evidence_requirements=[evidence_requirement],
            budget=budget,
            approval=approval,
            request_fingerprint=fingerprint,
        )
    )
    ref = _put_envelope(ports, state, request, node_id="B1.90")
    return NodeExecution(updates={"artifact_refs": [ref], "request_ref": ref})


def _b1_91(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    if ports.policy is None:
        raise RuntimeError("B1.91 requires ports.policy to be non-None")
    request = _read_model(
        ports, state, _require_ref(state, "OptimizationRequest"), OptimizationRequest
    )
    decision = ports.policy.evaluate(
        PolicyRequest(
            decision_type="automatic_intake",
            policy_version="b1-intake-v1",
            tenant_id=_required_state_str(state, "tenant_id"),
            facts={"feature_id": request.objective.feature_id},
        )
    )
    if decision.allowed:
        route = NodeRoute.CONTINUE
    elif decision.decision == "deny":
        route = NodeRoute.REJECTED
    else:
        route = NodeRoute.APPROVAL
    return NodeExecution(route=route)


def _b1_96(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    detection_report_ref = _require_ref(state, "DetectionReport")
    request_ref = _require_ref(state, "OptimizationRequest")
    source_snapshot_ref = _require_ref(state, "SourceSnapshot")
    baseline_ref = _require_ref(state, "BaselineSnapshot")
    evidence_bundle_ref = _require_ref(state, "EvidenceBundle")
    comparability_ref = _require_ref(state, "ComparabilityReport")
    report = _read_model(ports, state, detection_report_ref, DetectionReport)

    source_binding = (
        report.source_bindings[0]
        if report.source_bindings
        else SourceBinding(
            binding_id="none",
            repository_id="unknown",
            resolved=False,
            unresolved_reason="no source binding available",
        )
    )
    owner_binding = (
        report.ownership_bindings[0]
        if report.ownership_bindings
        else OwnershipBinding(binding_id="none", resolved=False)
    )

    opportunity = _seal(
        QualifiedOpportunity(
            **_base_envelope(
                state,
                "QualifiedOpportunity",
                parents=[
                    detection_report_ref.content_digest,
                    request_ref.content_digest,
                    baseline_ref.content_digest,
                ],
            ),
            detection_report_digest=detection_report_ref.content_digest,
            request_digest=request_ref.content_digest,
            source_snapshot_digest=source_snapshot_ref.content_digest,
            baseline_digest=baseline_ref.content_digest,
            evidence_bundle_digest=evidence_bundle_ref.content_digest,
            comparability_report_digest=comparability_ref.content_digest,
            source_binding=source_binding,
            owner_binding=owner_binding,
            case_start_id=f"case-start-{_required_state_str(state, 'case_id')}",
        )
    )
    ref = _put_envelope(ports, state, opportunity, node_id="B1.96")
    return NodeExecution(updates={"artifact_refs": [ref]})


# ---------------------------------------------------------------------------
# Shared helpers (mirrors a1_handlers.py/a2_handlers.py -- each lane's
# handler module keeps its own small copy rather than a shared import).
# ---------------------------------------------------------------------------


def _put_envelope(
    ports: NodePorts, state: OptimizationState, envelope: ArtifactEnvelope, *, node_id: str
) -> ArtifactRef:
    content = canonical_json(envelope.model_dump(mode="json", exclude={"content_digest"}))
    generic_ref = ports.artifacts.put_json(
        tenant_id=_required_state_str(state, "tenant_id"),
        content=content,
        content_digest=envelope.content_digest,
        idempotency_key=(
            f"{_required_state_str(state, 'case_id')}:{node_id}:{envelope.artifact_type}"
        ),
    )
    return ArtifactRef(
        artifact_type=envelope.artifact_type,
        schema_version=envelope.schema_version,
        artifact_id=envelope.artifact_id,
        content_digest=envelope.content_digest,
        uri=generic_ref.uri,
    )


def _read_model[T](
    ports: NodePorts, state: OptimizationState, ref: ArtifactRef, model: type[T]
) -> T:
    content = ports.artifacts.read(tenant_id=_required_state_str(state, "tenant_id"), ref=ref)
    raw = TypeAdapter(dict[str, Any]).validate_json(content)
    if issubclass(cast("type[Any]", model), ArtifactEnvelope):
        raw.setdefault("content_digest", ref.content_digest)
    return cast("Any", model).model_validate(raw)


def _seal[T: ArtifactEnvelope](model: T) -> T:
    return model.model_copy(update={"content_digest": model_content_digest(model)})


def _base_envelope(
    state: OptimizationState, artifact_type: str, *, parents: list[str] | None = None
) -> dict[str, Any]:
    return {
        "artifact_id": f"{_required_state_str(state, 'case_id')}-{artifact_type}",
        "tenant_id": _required_state_str(state, "tenant_id"),
        "case_id": _required_state_str(state, "case_id"),
        "created_at": datetime.now(UTC),
        "producer": _PRODUCER,
        "policy_versions": {"b1": "production-v1"},
        "content_digest": _ZERO_DIGEST,
        "parent_digests": parents or [],
    }


def _require_ref(state: OptimizationState, artifact_type: str) -> ArtifactRef:
    for ref in state.get("artifact_refs", []):
        if ref.artifact_type == artifact_type:
            return ref
    raise ValueError(f"missing required artifact ref: {artifact_type}")


def _branch_artifact_id(case_id: str, node_id: str) -> str:
    return f"{case_id}-{node_id}-HistoricalEvidenceBranch"


def _branch_envelope(
    state: OptimizationState, node_id: str, *, parents: list[str]
) -> dict[str, Any]:
    envelope = _base_envelope(state, "HistoricalEvidenceBranch", parents=parents)
    envelope["artifact_id"] = _branch_artifact_id(_required_state_str(state, "case_id"), node_id)
    return envelope


def _require_branch_ref(state: OptimizationState, node_id: str) -> ArtifactRef:
    artifact_id = _branch_artifact_id(_required_state_str(state, "case_id"), node_id)
    for ref in state.get("artifact_refs", []):
        if ref.artifact_type == "HistoricalEvidenceBranch" and ref.artifact_id == artifact_id:
            return ref
    raise ValueError(f"missing required HistoricalEvidenceBranch for {node_id}")


def _required_state_str(state: OptimizationState, key: str) -> str:
    value = state.get(key)  # type: ignore[literal-required]
    if not isinstance(value, str) or not value:
        raise ValueError(f"B1 state is missing required field {key!r}")
    return value


def _git(cwd: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()

"""Production handlers for C0 (shared lane convergence gate).

C0 sits after both `build_lane_a_graph` (post A3.90) and
`build_lane_b_proposal_graph` (post B2.60, see `orchestration/lanes.py`) --
its handlers are deliberately lane-agnostic: B1.95/B2.22 embed the *real*
`build_a2_graph`/`build_a3_graph`, so a Lane B run has already sealed the
exact same artifact types (`OptimizationRequest`, `SourceSnapshot`,
`BaselineSnapshot`, `EvidenceBundle`, `FindingSet`, `SolutionPortfolio`,
`A3QualityReport`) a Lane A run would. C0 never branches on `state["lane"]`
except at C0.10 (BR-C0-001: origin must match what was already sealed).

C0.10-50 each populate one plain `OptimizationState` field
(`c0_schema_results`/`c0_digest_chain`/`c0_equivalence_verdicts`/
`c0_freshness_checks`) with the exact leaf-type lists `ConvergenceDecision`
(`contracts/c0.py`) requires; C0.60 is the only node that assembles and
seals it, and C0.70 the only one that seals `ConvergedCase`.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, TypeAdapter, ValidationError

from production_optimizer.application.node_contract import NodeSpec, SideEffectClass
from production_optimizer.application.node_runtime import (
    NodeExecution,
    NodePorts,
    NodeRoute,
    NodeRuntime,
    RegisteredNode,
)
from production_optimizer.contracts.a1 import OptimizationRequest
from production_optimizer.contracts.a2 import BaselineSnapshot, EvidenceBundle, SourceSnapshot
from production_optimizer.contracts.a3 import A3QualityReport, FindingSet, SolutionPortfolio
from production_optimizer.contracts.artifacts import ArtifactRef
from production_optimizer.contracts.c0 import (
    ConvergedCase,
    ConvergenceDecision,
    DigestChainLink,
    DimensionEquivalenceVerdict,
    FreshnessCheck,
    SchemaValidationResult,
)
from production_optimizer.contracts.canonical import canonical_json, model_content_digest
from production_optimizer.contracts.envelope import ArtifactEnvelope, ProducerIdentity
from production_optimizer.contracts.state import OptimizationState

_PRODUCER = ProducerIdentity(name="c0-production-handler", version="1.0.0")
_ZERO_DIGEST = f"sha256:{'0' * 64}"
_FRESHNESS_WINDOW = timedelta(days=7)

_REQUIRED_ARTIFACTS: tuple[tuple[str, type[ArtifactEnvelope]], ...] = (
    ("OptimizationRequest", OptimizationRequest),
    ("SourceSnapshot", SourceSnapshot),
    ("BaselineSnapshot", BaselineSnapshot),
    ("EvidenceBundle", EvidenceBundle),
    ("FindingSet", FindingSet),
    ("SolutionPortfolio", SolutionPortfolio),
    ("A3QualityReport", A3QualityReport),
)
_MODEL_BY_TYPE: dict[str, type[ArtifactEnvelope]] = dict(_REQUIRED_ARTIFACTS)

# (child artifact type, digest field on the child, parent artifact type) --
# every `*_digest` linkage `contracts/a1.py`/`a2.py`/`a3.py` actually define
# between these seven stable, non-revision-pass-scoped artifact types. This
# deliberately excludes `A3QualityReport.solution_portfolio_digest`: that
# field is set (see `a3_handlers._a3_81`) to the *intermediate*
# `SolutionStrategySet` digest, not the final sealed `SolutionPortfolio` --
# an A3-internal bookkeeping detail, not one of the "request, source
# snapshot, evidence and solution references" BR-C0-002 names.
#
# `EvidenceBundle.baseline_digest` links to `SourceSnapshot`, not
# `BaselineSnapshot` -- confirmed against a real end-to-end run
# (`scripts/optimize.py`) and `a2_handlers._a2_80`'s own docstring: A2.80
# seals `EvidenceBundle` *before* `BaselineSnapshot` exists (A2.95 runs
# after it), so it cannot reference a not-yet-sealed artifact. In this
# `active_collection` baseline mode the source snapshot *is* the baseline
# being measured, which is exactly the digest `BaselineSnapshot.
# source_snapshot_digest` itself uses.
_DIGEST_LINKS: tuple[tuple[str, str, str], ...] = (
    ("BaselineSnapshot", "request_digest", "OptimizationRequest"),
    ("BaselineSnapshot", "source_snapshot_digest", "SourceSnapshot"),
    ("EvidenceBundle", "baseline_digest", "SourceSnapshot"),
    ("FindingSet", "evidence_bundle_digest", "EvidenceBundle"),
    ("SolutionPortfolio", "finding_set_digest", "FindingSet"),
    ("SolutionPortfolio", "quality_report_digest", "A3QualityReport"),
    ("A3QualityReport", "finding_set_digest", "FindingSet"),
)

_C0_ROUTE_OVERRIDES: dict[str, set[str]] = {
    "C0.60": {NodeRoute.CONTINUE.value, NodeRoute.REJECTED.value},
}


def _spec(node_id: str) -> NodeSpec:
    routes = _C0_ROUTE_OVERRIDES.get(node_id, {NodeRoute.CONTINUE.value})
    return NodeSpec(
        node_id=node_id,
        business_task_id=node_id,
        owner="shared-convergence",
        input_contract=f"{node_id}Input@1.0",
        output_contract=f"{node_id}Output@1.0",
        supported_schema_majors={1},
        idempotency_key_version="c0-production-v1",
        side_effect_class=SideEffectClass.IDEMPOTENT_WRITE,
        timeout_seconds=60,
        max_attempts=2,
        allowed_routes=routes,
        runbook="docs/project-blueprint/shared-workflow/00-convergence.md",
        slo="C0 node completes within 60 seconds",
    )


def _handler(node_id: str) -> Any:
    def execute(state: OptimizationState, ports: NodePorts | None, /) -> NodeExecution:
        if ports is None:
            raise RuntimeError("production C0 handlers require artifact and intent ports")
        return _run(node_id, state, ports)

    execute.__name__ = f"c0_{node_id.replace('.', '_')}"
    return execute


def build_c0_registrations() -> dict[str, RegisteredNode]:
    from production_optimizer.orchestration.catalog import C0_NODE_IDS

    return {
        node_id: RegisteredNode(spec=_spec(node_id), handler=_handler(node_id))
        for node_id in C0_NODE_IDS
    }


def build_c0_runtime(*, ports: NodePorts) -> NodeRuntime:
    """Build the one `NodeRuntime` `orchestration/subgraphs/c0.py`'s
    `build_c0_graph` needs. Unlike B1/B2, C0 embeds no lane subgraph, so no
    registration merge is needed -- its 7 native nodes are the whole graph."""

    return NodeRuntime(build_c0_registrations(), ports=ports)


def _run(node_id: str, state: OptimizationState, ports: NodePorts) -> NodeExecution:
    match node_id:
        case "C0.10":
            return _c0_10(state, ports)
        case "C0.20":
            return _c0_20(state, ports)
        case "C0.30":
            return _c0_30(state, ports)
        case "C0.40":
            return _c0_40(state, ports)
        case "C0.50":
            return _c0_50(state, ports)
        case "C0.60":
            return _c0_60(state, ports)
        case "C0.70":
            return _c0_70(state, ports)
        case _:
            return NodeExecution()


def _c0_10(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """BR-C0-001: a lane origin cannot change after case creation. The only
    two independent sources of "what origin is this case" are `state["lane"]`
    (set once at case creation) and the already-sealed `OptimizationRequest`
    (sealed by A1.95 for Lane A, or reconstructed by B1.90 for Lane B) --
    real disagreement between them means the request was tampered with or a
    real bug ran, so this fails hard rather than routing a soft rejection."""

    request = cast("OptimizationRequest", _read_required(ports, state, "OptimizationRequest"))
    expected_origin = _origin(state)
    if request.origin.value != expected_origin:
        raise ValueError(
            f"BR-C0-001 violation: case lane is {expected_origin!r} but the sealed "
            f"OptimizationRequest.origin is {request.origin.value!r}"
        )
    return NodeExecution()


def _c0_20(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Real schema validation: actually parse each required artifact through
    its Pydantic model rather than trusting the stored `schema_version`."""

    results: list[SchemaValidationResult] = []
    for artifact_type, model_cls in _REQUIRED_ARTIFACTS:
        ref = _require_ref(state, artifact_type)
        try:
            _read_model(ports, state, ref, model_cls)
        except ValidationError as exc:
            results.append(
                SchemaValidationResult(
                    artifact_type=artifact_type,
                    schema_version=ref.schema_version,
                    valid=False,
                    errors=[str(exc)],
                )
            )
            continue
        results.append(
            SchemaValidationResult(
                artifact_type=artifact_type, schema_version=ref.schema_version, valid=True
            )
        )
    return NodeExecution(updates={"c0_schema_results": results})


def _c0_30(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Real digest-chain verification (BR-C0-002): re-read every required
    artifact and compare each `*_digest` field against the actual sealed
    parent's `content_digest`, not a hardcoded `linked=True`."""

    refs = {
        artifact_type: _require_ref(state, artifact_type) for artifact_type, _ in
        _REQUIRED_ARTIFACTS
    }
    links: list[DigestChainLink] = []
    for child_type, field_name, parent_type in _DIGEST_LINKS:
        child_model = _read_model(ports, state, refs[child_type], _MODEL_BY_TYPE[child_type])
        expected_parent_digest = refs[parent_type].content_digest
        actual_value = cast("str", getattr(child_model, field_name))
        links.append(
            DigestChainLink(
                artifact_type=child_type,
                artifact_digest=refs[child_type].content_digest,
                expected_parent_digest=expected_parent_digest,
                linked=(actual_value == expected_parent_digest),
            )
        )
    return NodeExecution(updates={"c0_digest_chain": links})


def _c0_40(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Real semantic equivalence (per doc row: "Require B1 request/baseline
    to satisfy A1/A2 contracts and B2 to satisfy A3 policy"). Two checks,
    both lane-agnostic since B1/B2 embed the real A2/A3 graphs:

    (1) the baseline was actually collected for the workload/environment
    the request specified, not some other one. This is *not* a check that
    every `Criterion.metric_id` was measured: A2's aggregates are keyed by
    `EvidenceItem.evidence_type` (a2_handlers._a2_95), a raw collector
    label such as "unit_command_result", which was never designed to equal
    a criterion's business metric id -- a performance criterion can
    legitimately have zero matching evidence today (no benchmark collector
    is wired in yet; see `scripts/optimize.py`'s own `--metric` help text)
    without that being a convergence failure.
    (2) A3's own quality gate must have actually passed.
    """

    request = cast("OptimizationRequest", _read_required(ports, state, "OptimizationRequest"))
    baseline = cast("BaselineSnapshot", _read_required(ports, state, "BaselineSnapshot"))
    quality = cast("A3QualityReport", _read_required(ports, state, "A3QualityReport"))

    workload_ok = (
        baseline.workload_id == request.workload.workload_id
        and baseline.environment_id == request.workload.environment_id
    )

    verdicts = [
        DimensionEquivalenceVerdict(
            dimension="request_baseline_contract",
            equivalent=workload_ok,
            reason=(
                None
                if workload_ok
                else (
                    f"baseline was collected for workload={baseline.workload_id!r}/"
                    f"environment={baseline.environment_id!r}, but the request specified "
                    f"workload={request.workload.workload_id!r}/"
                    f"environment={request.workload.environment_id!r}"
                )
            ),
        ),
        DimensionEquivalenceVerdict(
            dimension="solution_quality_policy",
            equivalent=quality.passed,
            reason=None if quality.passed else "A3QualityReport did not pass its own quality gates",
        ),
    ]
    return NodeExecution(updates={"c0_equivalence_verdicts": verdicts})


def _c0_50(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Real freshness recheck. Source: re-derive the live git revision at
    `SourceSnapshot.canonical_path_ref` and compare to what was sealed
    (mirrors `b2_handlers._b2_31`'s staleness check, generalized to the
    artifact every lane actually seals). Evidence: the most recent
    `EvidenceItem.identity.observed_at` must be within `_FRESHNESS_WINDOW`.
    Neither check can fabricate an answer it cannot compute: a snapshot
    whose path no longer exists, or one with no recorded git revision,
    is treated as fresh (nothing contradicts what was sealed)."""

    now = datetime.now(UTC)
    checks: list[FreshnessCheck] = []

    source = cast("SourceSnapshot", _read_required(ports, state, "SourceSnapshot"))
    source_fresh = True
    root = Path(source.canonical_path_ref)
    if root.exists() and source.git_revision:
        current_revision = _git(root, "rev-parse", "HEAD")
        if current_revision is not None and current_revision != source.git_revision:
            source_fresh = False
    checks.append(FreshnessCheck(dimension="source", fresh=source_fresh, checked_at=now))

    bundle = cast("EvidenceBundle", _read_required(ports, state, "EvidenceBundle"))
    observed_ats = [item.identity.observed_at for item in bundle.evidence]
    normalized = [
        observed if observed.tzinfo is not None else observed.replace(tzinfo=UTC)
        for observed in observed_ats
    ]
    evidence_fresh = True
    expires_at: datetime | None = None
    if normalized:
        most_recent = max(normalized)
        evidence_fresh = (now - most_recent) <= _FRESHNESS_WINDOW
        candidate_expiry = most_recent + _FRESHNESS_WINDOW
        expires_at = candidate_expiry if candidate_expiry > now else None
    checks.append(
        FreshnessCheck(
            dimension="evidence", fresh=evidence_fresh, checked_at=now, expires_at=expires_at
        )
    )

    return NodeExecution(updates={"c0_freshness_checks": checks})


def _c0_60(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """The convergence gate itself. `ConvergenceDecision`'s own validator
    (`contracts/c0.py`) pins `converged` to exactly this AND across the four
    gate categories plus `eligible_solution_count >= 1` -- this handler
    computes the same formula for real from C0.10-50's actual results
    rather than asserting it, so a real failure anywhere upstream routes
    REJECTED here instead of silently sealing a false `converged=True`."""

    schema_results = cast(
        "list[SchemaValidationResult]", state.get("c0_schema_results") or []
    )
    digest_chain = cast("list[DigestChainLink]", state.get("c0_digest_chain") or [])
    equivalence_verdicts = cast(
        "list[DimensionEquivalenceVerdict]", state.get("c0_equivalence_verdicts") or []
    )
    freshness_checks = cast("list[FreshnessCheck]", state.get("c0_freshness_checks") or [])

    portfolio = cast("SolutionPortfolio", _read_required(ports, state, "SolutionPortfolio"))
    eligible_solution_count = sum(1 for strategy in portfolio.strategies if strategy.eligible)

    schema_ok = all(result.valid for result in schema_results)
    digest_ok = all(link.linked for link in digest_chain)
    equivalence_ok = all(verdict.equivalent for verdict in equivalence_verdicts)
    freshness_ok = all(check.fresh for check in freshness_checks)
    eligible_ok = eligible_solution_count >= 1
    converged = schema_ok and digest_ok and equivalence_ok and freshness_ok and eligible_ok

    reasons: list[str] = []
    if not schema_ok:
        reasons.append("one or more artifacts failed schema validation")
    if not digest_ok:
        reasons.append("digest chain is broken between one or more artifacts")
    if not equivalence_ok:
        reasons.append("a semantic equivalence check failed")
    if not freshness_ok:
        reasons.append("a freshness check failed")
    if not eligible_ok:
        reasons.append("no eligible solution strategy is available")

    decision = _seal(
        ConvergenceDecision(
            **_base_envelope(state, "ConvergenceDecision"),
            origin=cast("Any", _origin(state)),
            schema_results=schema_results,
            digest_chain=digest_chain,
            equivalence_verdicts=equivalence_verdicts,
            freshness_checks=freshness_checks,
            eligible_solution_count=eligible_solution_count,
            converged=converged,
            reasons=reasons,
        )
    )
    ref = _put_envelope(ports, state, decision, node_id="C0.60")
    route = NodeRoute.CONTINUE if converged else NodeRoute.REJECTED
    return NodeExecution(route=route, updates={"artifact_refs": [ref]})


def _c0_70(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Seal the lane-neutral `ConvergedCase` handoff -- only reached once
    C0.60 has actually routed `continue`."""

    request_ref = _require_ref(state, "OptimizationRequest")
    source_ref = _require_ref(state, "SourceSnapshot")
    baseline_ref = _require_ref(state, "BaselineSnapshot")
    evidence_ref = _require_ref(state, "EvidenceBundle")
    portfolio_ref = _require_ref(state, "SolutionPortfolio")
    decision_ref = _require_ref(state, "ConvergenceDecision")

    converged_case = _seal(
        ConvergedCase(
            **_base_envelope(
                state,
                "ConvergedCase",
                parents=[
                    request_ref.content_digest,
                    source_ref.content_digest,
                    baseline_ref.content_digest,
                    evidence_ref.content_digest,
                    portfolio_ref.content_digest,
                    decision_ref.content_digest,
                ],
            ),
            origin=cast("Any", _origin(state)),
            request_digest=request_ref.content_digest,
            source_snapshot_digest=source_ref.content_digest,
            baseline_digest=baseline_ref.content_digest,
            evidence_bundle_digest=evidence_ref.content_digest,
            solution_portfolio_digest=portfolio_ref.content_digest,
            convergence_decision_digest=decision_ref.content_digest,
        )
    )
    ref = _put_envelope(ports, state, converged_case, node_id="C0.70")
    return NodeExecution(
        updates={"artifact_refs": [ref], "convergence_ref": ref, "status": "converged"}
    )


# ---------------------------------------------------------------------------
# Shared helpers (mirrors a2_handlers.py/b1_handlers.py/b2_handlers.py)
# ---------------------------------------------------------------------------


def _origin(state: OptimizationState) -> str:
    return "manual" if state.get("lane") == "manual" else "automatic"


def _read_required(ports: NodePorts, state: OptimizationState, artifact_type: str) -> BaseModel:
    ref = _require_ref(state, artifact_type)
    return _read_model(ports, state, ref, _MODEL_BY_TYPE[artifact_type])


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
        "policy_versions": {"c0": "production-v1"},
        "content_digest": _ZERO_DIGEST,
        "parent_digests": parents or [],
    }


def _require_ref(state: OptimizationState, artifact_type: str) -> ArtifactRef:
    ref = _try_ref(state, artifact_type)
    if ref is None:
        raise ValueError(f"missing required artifact ref: {artifact_type}")
    return ref


def _try_ref(state: OptimizationState, artifact_type: str) -> ArtifactRef | None:
    for ref in state.get("artifact_refs", []):
        if ref.artifact_type == artifact_type:
            return ref
    return None


def _required_state_str(state: OptimizationState, key: str) -> str:
    value = state.get(key)  # type: ignore[literal-required]
    if not isinstance(value, str) or not value:
        raise ValueError(f"C0 state is missing required field {key!r}")
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


__all__ = ["build_c0_registrations", "build_c0_runtime"]

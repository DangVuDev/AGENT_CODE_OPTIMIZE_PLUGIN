"""Production handlers for S06 (Policy Decision) -- converts S04's real
verification and S05's real measurement into one deterministic operational
decision: KEEP, FIX_ONE_PART or REVERT (see
docs/project-blueprint/shared-workflow/06-policy-decision.md). An LLM may
explain the outcome; nothing here calls one, since the doc is explicit that
an LLM "may explain the outcome but may not decide it".

The outer graph (`orchestration/shared_workflow.py`) never routes on this
subgraph's own `NodeExecution.route` -- like `_verification_outcome` already
does for S04, it reads `state["s06_decision"]["outcome"]` directly, since a
plain `NodeRoute` value has no member for "loop back to S01 after a verified
revert" and this codebase's established pattern for that shape is a custom,
state-data-driven predicate rather than growing the shared enum.

S06.60/70's REVERT handling is real but architecturally light: S03 always
isolates the treatment in a real git worktree or plain directory copy and
never writes back into `SourceSnapshot.canonical_path_ref` (see
`s03_handlers._s03_20`'s docstring), so a REVERT decision never has a real
change applied to the original repository to undo. "Rollback" here is a
real, honest verification that the original is still untouched (a real
`git status --porcelain` check when a git revision exists), not a
fabricated revert action -- and S06.70 escalates (never silently claims
REVERT) if that verification ever fails, matching the doc's ROLLBACK_FAILED
rule: "Escalate as critical incident; do not mark reverted".
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
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
from production_optimizer.contracts.a1 import Guardrail, OptimizationRequest
from production_optimizer.contracts.a2 import SourceSnapshot
from production_optimizer.contracts.artifacts import ArtifactRef
from production_optimizer.contracts.canonical import canonical_json, model_content_digest
from production_optimizer.contracts.envelope import ArtifactEnvelope, ProducerIdentity
from production_optimizer.contracts.interrupts import InterruptEnvelope
from production_optimizer.contracts.platform import PolicyRequest
from production_optimizer.contracts.s01 import SelectedSolution
from production_optimizer.contracts.s03 import PatchArtifact
from production_optimizer.contracts.s04 import VerificationReport
from production_optimizer.contracts.s05 import EffectResult, StatisticalReport
from production_optimizer.contracts.s06 import (
    Decision,
    GuardrailEvaluation,
    PhaseAttribution,
    RepositoryApplyResult,
    RollbackReport,
    TargetEvaluation,
)
from production_optimizer.contracts.state import OptimizationState

_PRODUCER = ProducerIdentity(name="s06-production-handler", version="1.0.0")
_ZERO_DIGEST = f"sha256:{'0' * 64}"
_POLICY_VERSION = "s06-decision-v1"
_TARGET_EPSILON = 1e-9

_S06_ROUTE_OVERRIDES: dict[str, set[str]] = {
    "S06.10": {NodeRoute.CONTINUE.value, NodeRoute.REJECTED.value},
    "S06.90": {NodeRoute.CONTINUE.value, NodeRoute.APPROVAL.value, NodeRoute.REJECTED.value},
}


def _spec(node_id: str) -> NodeSpec:
    routes = _S06_ROUTE_OVERRIDES.get(node_id, {NodeRoute.CONTINUE.value})
    return NodeSpec(
        node_id=node_id,
        business_task_id=node_id,
        owner="shared-workflow-s06",
        input_contract=f"{node_id}Input@1.0",
        output_contract=f"{node_id}Output@1.0",
        supported_schema_majors={1},
        idempotency_key_version="s06-production-v1",
        side_effect_class=SideEffectClass.IDEMPOTENT_WRITE,
        timeout_seconds=60,
        max_attempts=2,
        allowed_routes=routes,
        runbook="docs/project-blueprint/shared-workflow/06-policy-decision.md",
        slo="S06 completes in p95 < 2s plus rollback SLO",
    )


def _handler(node_id: str) -> Any:
    def execute(state: OptimizationState, ports: NodePorts | None, /) -> NodeExecution:
        if ports is None:
            raise RuntimeError("production S06 handlers require artifact and intent ports")
        return _run(node_id, state, ports)

    execute.__name__ = f"s06_{node_id.replace('.', '_')}"
    return execute


def build_s06_registrations() -> dict[str, RegisteredNode]:
    from production_optimizer.orchestration.catalog import S06_NODE_IDS

    return {
        node_id: RegisteredNode(spec=_spec(node_id), handler=_handler(node_id))
        for node_id in S06_NODE_IDS
    }


def build_s06_runtime(*, ports: NodePorts) -> NodeRuntime:
    return NodeRuntime(build_s06_registrations(), ports=ports)


def _run(node_id: str, state: OptimizationState, ports: NodePorts) -> NodeExecution:
    match node_id:
        case "S06.10":
            return _s06_10(state, ports)
        case "S06.20":
            return _s06_20(state, ports)
        case "S06.30":
            return _s06_30(state, ports)
        case "S06.40":
            return _s06_40(state, ports)
        case "S06.50":
            return _s06_50(state, ports)
        case "S06.60":
            return _s06_60(state, ports)
        case "S06.70":
            return _s06_70(state, ports)
        case "S06.80":
            return _s06_80(state, ports)
        case "S06.90":
            return _s06_90(state, ports)
        case _:
            return NodeExecution()


def _active_phase_id(state: OptimizationState) -> str:
    value = state.get("s03_active_phase_id")
    if not isinstance(value, str) or not value:
        raise ValueError("S06 state is missing s03_active_phase_id (S03/S04/S05 must run first)")
    return value


def _pass_number(state: OptimizationState) -> int:
    return state.get("s03_revision_attempts", 0) or 0


def _s04_report_stage(state: OptimizationState) -> str:
    return f"S04.90-{_active_phase_id(state)}-pass{_pass_number(state)}"


def _s05_measurement_stage(state: OptimizationState) -> str:
    return f"S05.90-{_active_phase_id(state)}-pass{_pass_number(state)}"


def _s05_statistical_stage(state: OptimizationState) -> str:
    return f"S05.80-{_active_phase_id(state)}-pass{_pass_number(state)}"


def _s06_stage(node_id: str, state: OptimizationState) -> str:
    return f"{node_id}-{_active_phase_id(state)}-pass{_pass_number(state)}"


def _s06_10(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Real defense-in-depth check, not the load-bearing gate: the outer
    graph already only reaches S06 with a passed `VerificationReport`
    (S04.80) and a comparable `Measurement` (S05.70), so this mostly
    confirms both are genuinely resolvable by their exact pass-scoped stage
    ids -- and that S05.80 measured every A1 criterion (BR-05-005) -- before
    the rest of S06 reads them."""

    request = cast("OptimizationRequest", _read_required(ports, state, "OptimizationRequest"))
    verification_ref = _require_stage_ref(state, _s04_report_stage(state), "VerificationReport")
    verification = _read_model(ports, state, verification_ref, VerificationReport)
    measurement_ref = _require_stage_ref(state, _s05_measurement_stage(state), "Measurement")
    statistical_ref = _require_stage_ref(state, _s05_statistical_stage(state), "StatisticalReport")
    statistical = _read_model(ports, state, statistical_ref, StatisticalReport)

    measured_ids = {effect.criterion_id for effect in statistical.effects}
    required_ids = {criterion.criterion_id for criterion in request.criteria}
    complete = verification.passed and required_ids <= measured_ids
    route = NodeRoute.CONTINUE if complete else NodeRoute.REJECTED
    intake = {
        "verification_digest": verification_ref.content_digest,
        "measurement_digest": measurement_ref.content_digest,
        "complete": complete,
    }
    return NodeExecution(route=route, updates={"s06_intake": intake})


def _s06_20(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Real, deterministic application of each A1 criterion's own
    `direction`/`target` against S05's real, measured effect -- never an LLM
    judgment."""

    request = cast("OptimizationRequest", _read_required(ports, state, "OptimizationRequest"))
    statistical_ref = _require_stage_ref(state, _s05_statistical_stage(state), "StatisticalReport")
    statistical = _read_model(ports, state, statistical_ref, StatisticalReport)
    effects_by_criterion = {effect.criterion_id: effect for effect in statistical.effects}

    evaluations = [
        _evaluate_target(criterion, effects_by_criterion.get(criterion.criterion_id))
        for criterion in request.criteria
    ]
    return NodeExecution(
        updates={"s06_target_evaluations": [e.model_dump(mode="json") for e in evaluations]}
    )


def _evaluate_target(criterion: Any, effect: EffectResult | None) -> TargetEvaluation:
    treatment_value = effect.treatment_mean if effect is not None else float("nan")
    met = False
    if effect is not None:
        if criterion.direction == "minimize":
            met = treatment_value <= criterion.target
        elif criterion.direction == "maximize":
            met = treatment_value >= criterion.target
        else:
            met = abs(treatment_value - criterion.target) <= _TARGET_EPSILON
    return TargetEvaluation(
        criterion_id=criterion.criterion_id,
        metric_id=criterion.metric_id,
        direction=criterion.direction,
        target=criterion.target,
        treatment_value=treatment_value,
        met=met,
    )


def _s06_30(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Real guardrail evaluation grounded in whichever real evidence exists
    -- S04's `VerificationReport.check_results` (exit-code guardrails) or
    S05's `StatisticalReport.effects` (benchmarked guardrails). A guardrail
    metric neither source can resolve fails closed (BR "fail closed on
    required correctness/security/... regression"), never fabricated as a
    pass for lack of evidence."""

    request = cast("OptimizationRequest", _read_required(ports, state, "OptimizationRequest"))
    verification_ref = _require_stage_ref(state, _s04_report_stage(state), "VerificationReport")
    verification = _read_model(ports, state, verification_ref, VerificationReport)
    statistical_ref = _require_stage_ref(state, _s05_statistical_stage(state), "StatisticalReport")
    statistical = _read_model(ports, state, statistical_ref, StatisticalReport)

    effects_by_metric = {effect.metric_id: effect for effect in statistical.effects}
    checks_by_metric = {
        f"{result.kind}_command_result": result for result in verification.check_results
    }

    evaluations = [
        _evaluate_guardrail(guardrail, effects_by_metric, checks_by_metric)
        for guardrail in request.guardrails
    ]
    return NodeExecution(
        updates={"s06_guardrail_evaluations": [e.model_dump(mode="json") for e in evaluations]}
    )


def _evaluate_guardrail(
    guardrail: Guardrail, effects_by_metric: dict[str, Any], checks_by_metric: dict[str, Any]
) -> GuardrailEvaluation:
    effect = effects_by_metric.get(guardrail.metric_id)
    if effect is not None:
        value = cast("float", effect.treatment_mean)
        passed = _apply_operator(value, guardrail.operator, guardrail.threshold)
        return GuardrailEvaluation(
            guardrail_id=guardrail.guardrail_id,
            metric_id=guardrail.metric_id,
            evidence_source="statistical_effect",
            observed_value=value,
            passed=passed,
            detail=f"{guardrail.metric_id}={value} {guardrail.operator} {guardrail.threshold}",
        )

    check = checks_by_metric.get(guardrail.metric_id)
    if check is not None:
        value = float(cast("int", check.exit_code))
        passed = _apply_operator(value, guardrail.operator, guardrail.threshold)
        return GuardrailEvaluation(
            guardrail_id=guardrail.guardrail_id,
            metric_id=guardrail.metric_id,
            evidence_source="verification_check",
            observed_value=value,
            passed=passed,
            detail=f"{guardrail.metric_id}={value} {guardrail.operator} {guardrail.threshold}",
        )

    return GuardrailEvaluation(
        guardrail_id=guardrail.guardrail_id,
        metric_id=guardrail.metric_id,
        evidence_source="unavailable",
        observed_value=None,
        passed=False,
        detail=f"no real evidence resolved for {guardrail.metric_id!r}",
    )


def _apply_operator(value: float, op: str, threshold: float) -> bool:
    if op == "lt":
        return value < threshold
    if op == "lte":
        return value <= threshold
    if op == "eq":
        return value == threshold
    if op == "gte":
        return value >= threshold
    if op == "gt":
        return value > threshold
    raise ValueError(f"unknown guardrail operator {op!r}")


def _s06_40(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Attribute the phase result: this milestone's S03 implements exactly
    one phase per case (BR-03-001), so a partial pass is trivially
    attributable to that one active phase -- a real multi-phase attribution
    is future work, not fabricated here."""

    del ports
    evaluations_raw = cast("list[dict[str, Any]]", state.get("s06_target_evaluations") or [])
    total = len(evaluations_raw)
    met = sum(1 for e in evaluations_raw if e.get("met"))

    if total == 0:
        classification: str = "no_evidence"
        rationale = "no primary criteria were evaluated"
    elif met == total:
        classification = "all_targets_met"
        rationale = f"all {total} primary criteria met their target"
    elif met == 0:
        classification = "wrong_direction"
        rationale = f"none of {total} primary criteria improved toward target"
    else:
        classification = "repairable"
        rationale = f"{met} of {total} primary criteria met target; the active phase is repairable"

    attribution = PhaseAttribution(
        phase_id=_active_phase_id(state),
        classification=cast("Any", classification),
        rationale=rationale,
    )
    return NodeExecution(updates={"s06_attribution": attribution.model_dump(mode="json")})


def _s06_50(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """The decision table itself -- a closed, deterministic formula, never
    an LLM judgment (the doc: "An LLM may explain the outcome but may not
    decide it")."""

    del ports
    guardrail_evaluations = cast(
        "list[dict[str, Any]]", state.get("s06_guardrail_evaluations") or []
    )
    attribution = cast("dict[str, Any]", state.get("s06_attribution") or {})
    guardrails_passed = all(g.get("passed") for g in guardrail_evaluations)
    classification = attribution.get("classification")

    if not guardrails_passed:
        outcome, rationale = "REVERT", "guardrail failure forces revert (BR-04-005/BR-05-005)"
    elif classification == "all_targets_met":
        outcome, rationale = "KEEP", "all primary targets met and every guardrail passed"
    elif classification == "repairable":
        outcome, rationale = "FIX_ONE_PART", cast("str", attribution.get("rationale", ""))
    else:
        outcome, rationale = (
            "REVERT",
            cast("str", attribution.get("rationale", "no evidence of improvement")),
        )

    return NodeExecution(updates={"s06_decision": {"outcome": outcome, "rationale": rationale}})


def _s06_60(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Execute required rollback -- real only for REVERT. S03 never mutates
    the real `SourceSnapshot.canonical_path_ref` (isolated workspace only),
    so there is nothing to git-revert; the real work is verifying that
    invariant actually held, not fabricating a restore action."""

    decision = cast("dict[str, Any]", state.get("s06_decision") or {})
    if decision.get("outcome") != "REVERT":
        return NodeExecution(updates={"s06_rollback": {"attempted": False, "verified": True}})

    snapshot = cast("SourceSnapshot", _read_required(ports, state, "SourceSnapshot"))
    patch_ref = _require_stage_ref(state, _s03_patch_stage(state), "PatchArtifact")
    root = Path(snapshot.canonical_path_ref)

    if snapshot.git_revision:
        status = _git(root, "status", "--porcelain")
        original_untouched = status is not None and status == ""
        detail = (
            "git status is clean on the original repository"
            if original_untouched
            else "git status reported real, unexpected changes on the original repository"
        )
    else:
        original_untouched = True
        detail = "directory-copy isolation never writes back into the original tree"

    report = _seal(
        RollbackReport(
            **_stage_envelope(state, _s06_stage("S06.60", state), "RollbackReport"),
            phase_id=_active_phase_id(state),
            patch_digest=patch_ref.content_digest,
            original_untouched=original_untouched,
            detail=detail,
        )
    )
    ref = _put_envelope(ports, state, report, node_id="S06.60")
    return NodeExecution(
        updates={
            "artifact_refs": [ref],
            "s06_rollback": {"attempted": True, "verified": original_untouched},
        }
    )


_APPLY_POLICY_DECISION_TYPE = "apply_to_real_repo"


def _s06_90(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """No-op pass-through unless this pass's `Decision` was KEEP -- mirrors
    `_s06_60`'s rollback-is-a-noop-unless-REVERT pattern exactly. On KEEP,
    this is the first genuine git-WRITE against the user's real repository
    anywhere in this codebase: every other `_git()` call in this file (and
    every A1/A2/B1/B2/C0 call, and S03's own isolated-worktree creation) is
    either read-only or confined to a temporary, disposable workspace that
    is never the real `SourceSnapshot.canonical_path_ref`.

    Uses the same policy-decides-if-a-human-is-needed shape as A1.90
    (`a1_handlers`'s `handle_a1_90_...`), not S01.80/S02.90's unconditional
    interrupt: `ports.policy.evaluate()` returns `allow` (auto-proceed),
    `require_approval` (real, resumable `InterruptEnvelope`, exact S01.80/
    S02.90 field shape) or a deny (fail closed, no interrupt at all). This
    is a third, deliberate approval distinct from S01.80 (approved the
    *strategy*) and S02.90 (approved the *plan*): neither of those has seen
    a concrete, S04-verified, S05-measured diff about to become a permanent
    commit in the user's real repository.
    """

    decision = cast("dict[str, Any]", state.get("s06_decision") or {})
    if decision.get("outcome") != "KEEP":
        return NodeExecution(updates={"s06_apply": {"applied": False, "attempted": False}})

    if ports.policy is None:
        raise RuntimeError("S06.90 requires ports.policy to be non-None")

    snapshot = cast("SourceSnapshot", _read_required(ports, state, "SourceSnapshot"))
    patch_ref = _require_stage_ref(state, _s03_patch_stage(state), "PatchArtifact")
    patch = _read_model(ports, state, patch_ref, PatchArtifact)
    active_phase_id = _active_phase_id(state)

    resume_command = state.get("resume_command")
    if resume_command is None:
        policy_decision = ports.policy.evaluate(
            PolicyRequest(
                decision_type=_APPLY_POLICY_DECISION_TYPE,
                policy_version=_POLICY_VERSION,
                tenant_id=_required_state_str(state, "tenant_id"),
                facts={
                    "phase_id": active_phase_id,
                    "case_id": _required_state_str(state, "case_id"),
                    "base_revision": patch.base_revision,
                    "changed_files": patch.changed_files,
                },
            )
        )
        if not policy_decision.allowed:
            return _seal_apply_result(
                state,
                ports,
                patch_ref,
                applied=False,
                base_revision=patch.base_revision,
                phase_id=active_phase_id,
                failure_reason=f"policy denied: {'; '.join(policy_decision.reasons)}",
                approval_decision="denied",
            )
        if policy_decision.decision == "allow":
            return _apply_patch_to_real_repo(
                state,
                ports,
                snapshot=snapshot,
                patch=patch,
                patch_ref=patch_ref,
                approval_decision="auto_allowed",
                approval_actor_id=None,
            )
        # decision == "require_approval" -> real, resumable halt (A1.90 shape)
        interrupt = InterruptEnvelope(
            interrupt_id=f"{_required_state_str(state, 'case_id')}-S06-APPLY",
            case_id=_required_state_str(state, "case_id"),
            thread_id=_required_state_str(state, "thread_id"),
            stage="S06.90",
            artifact_digest=patch_ref.content_digest,
            allowed_decisions=["approve", "reject"],
            required_actor_role="owner",
            policy_version=_POLICY_VERSION,
            issued_at=_now(),
            expires_at=_now() + timedelta(hours=24),
        )
        return NodeExecution(
            route=NodeRoute.APPROVAL,
            updates={
                "pending_interrupt": interrupt,
                "s06_apply": {"applied": False, "attempted": False},
            },
        )

    if resume_command.decision != "approve":
        return _seal_apply_result(
            state,
            ports,
            patch_ref,
            applied=False,
            base_revision=patch.base_revision,
            phase_id=active_phase_id,
            failure_reason=f"resumed with decision {resume_command.decision!r}",
            approval_decision="rejected",
            approval_actor_id=resume_command.actor_id,
        )

    return _apply_patch_to_real_repo(
        state,
        ports,
        snapshot=snapshot,
        patch=patch,
        patch_ref=patch_ref,
        approval_decision="approved",
        approval_actor_id=resume_command.actor_id,
    )


def _apply_patch_to_real_repo(
    state: OptimizationState,
    ports: NodePorts,
    *,
    snapshot: SourceSnapshot,
    patch: PatchArtifact,
    patch_ref: ArtifactRef,
    approval_decision: str,
    approval_actor_id: str | None,
) -> NodeExecution:
    real_root = Path(snapshot.canonical_path_ref)
    phase_id = patch.phase_id

    # Fail closed on drift -- BEFORE any git write, regardless of whether
    # `git apply` itself would tolerate it via fuzzy context matching (it
    # often will: empirically verified that plain `git apply --check`
    # tolerates unrelated drift outside the patch's own 3-line context
    # window, and `git apply -3` buys nothing for this diff format --
    # `difflib.unified_diff` output has no `diff --git`/`index` header for
    # git to locate a merge-base blob with). The only trustworthy drift
    # guard for a permanent, real commit is this explicit equality check.
    current_head = _git(real_root, "rev-parse", "HEAD")
    if current_head is None or current_head != patch.base_revision:
        return _seal_apply_result(
            state,
            ports,
            patch_ref,
            applied=False,
            base_revision=patch.base_revision,
            phase_id=phase_id,
            failure_reason=(
                f"real repository HEAD ({current_head!r}) no longer matches "
                f"base_revision ({patch.base_revision!r}); refusing to apply a stale patch"
            ),
            approval_decision=approval_decision,
            approval_actor_id=approval_actor_id,
        )

    branch_name = f"optimizer/{_required_state_str(state, 'case_id')}-{phase_id}"
    tmp_root = Path(tempfile.mkdtemp(prefix="s06-apply-"))
    worktree_dir = tmp_root / "worktree"
    succeeded = False
    try:
        if (
            _git(
                real_root,
                "worktree",
                "add",
                "-b",
                branch_name,
                str(worktree_dir),
                patch.base_revision,
            )
            is None
        ):
            return _seal_apply_result(
                state,
                ports,
                patch_ref,
                applied=False,
                base_revision=patch.base_revision,
                phase_id=phase_id,
                failure_reason="git worktree add -b failed",
                approval_decision=approval_decision,
                approval_actor_id=approval_actor_id,
            )

        patch_file = tmp_root / "change.patch"
        # Two real, empirically-found robustness gaps, both fixed here
        # rather than in S03's diff generation (out of scope for this
        # change, and this fix is complete without touching it):
        #
        # 1. `difflib.unified_diff` does not guarantee a trailing newline
        #    on the last hunk line; `git apply` treats a patch file missing
        #    one as truncated ("corrupt patch").
        # 2. `_build_unified_diff` reads files via `Path.read_text()`,
        #    which applies Python's universal-newline translation
        #    regardless of what is actually on disk, so the diff it
        #    produces is always LF-only -- but `git apply` matches raw
        #    bytes. On a real file with CRLF line endings (common on
        #    Windows, and Git for Windows' own installer defaults to
        #    `core.autocrlf=true`, which many repos' real checkouts
        #    inherit), an LF-only diff then fails to match CRLF context
        #    lines even though the change itself is correct. `-c
        #    core.autocrlf=true` makes `git apply` itself translate the
        #    patch to match the working tree's real line endings -- a
        #    per-invocation override (`-c`, not persistent `git config`),
        #    so it never touches this repository's actual configuration,
        #    on this worktree or the real one.
        diff_text = patch.diff if patch.diff.endswith("\n") else f"{patch.diff}\n"
        patch_file.write_text(diff_text, encoding="utf-8")
        autocrlf_override = ("-c", "core.autocrlf=true")

        if _git(worktree_dir, *autocrlf_override, "apply", "--check", str(patch_file)) is None:
            return _seal_apply_result(
                state,
                ports,
                patch_ref,
                applied=False,
                base_revision=patch.base_revision,
                phase_id=phase_id,
                failure_reason="git apply --check failed; diff no longer applies cleanly",
                approval_decision=approval_decision,
                approval_actor_id=approval_actor_id,
            )
        if _git(worktree_dir, *autocrlf_override, "apply", str(patch_file)) is None:
            return _seal_apply_result(
                state,
                ports,
                patch_ref,
                applied=False,
                base_revision=patch.base_revision,
                phase_id=phase_id,
                failure_reason="git apply failed after a successful --check",
                approval_decision=approval_decision,
                approval_actor_id=approval_actor_id,
            )
        if _git(worktree_dir, "add", *patch.changed_files) is None:
            return _seal_apply_result(
                state,
                ports,
                patch_ref,
                applied=False,
                base_revision=patch.base_revision,
                phase_id=phase_id,
                failure_reason="git add of changed_files failed",
                approval_decision=approval_decision,
                approval_actor_id=approval_actor_id,
            )
        message = (
            f"optimizer: apply {phase_id} ({_required_state_str(state, 'case_id')})\n\n"
            f"task_ids: {', '.join(patch.task_ids)}\n"
            f"base_revision: {patch.base_revision}\n"
            f"patch_digest: {patch_ref.content_digest}"
        )
        if _git(worktree_dir, "commit", "-m", message) is None:
            return _seal_apply_result(
                state,
                ports,
                patch_ref,
                applied=False,
                base_revision=patch.base_revision,
                phase_id=phase_id,
                failure_reason="git commit failed",
                approval_decision=approval_decision,
                approval_actor_id=approval_actor_id,
            )
        commit_sha = _git(worktree_dir, "rev-parse", "HEAD")
        if commit_sha is None:
            return _seal_apply_result(
                state,
                ports,
                patch_ref,
                applied=False,
                base_revision=patch.base_revision,
                phase_id=phase_id,
                failure_reason="commit succeeded but rev-parse HEAD failed",
                approval_decision=approval_decision,
                approval_actor_id=approval_actor_id,
            )
        succeeded = True
        return _seal_apply_result(
            state,
            ports,
            patch_ref,
            applied=True,
            base_revision=patch.base_revision,
            phase_id=phase_id,
            branch_name=branch_name,
            commit_sha=commit_sha,
            approval_decision=approval_decision,
            approval_actor_id=approval_actor_id,
        )
    finally:
        # Deregister the worktree from the REAL repo's own .git/worktrees/
        # metadata even on failure -- mirrors s03_handlers._cleanup_workspace
        # / s05_handlers's duplicated final-cleanup-owner pattern exactly.
        # On success, the branch and its real commit are real refs in the
        # real repo and survive this removal; only the temporary checkout
        # directory goes away. On any failure *after* `worktree add -b`
        # created the branch (apply/commit failing partway through), the
        # branch itself would otherwise be left behind pointing at nothing
        # but `base_revision` -- a real, if harmless, cleanup gap for a
        # step whose entire point is "only leave something behind when it
        # genuinely succeeded" -- so delete it too. Both calls are
        # best-effort (`_git` returning `None` here is not itself a case
        # failure): the branch may never have been created at all (drift
        # check or `worktree add` itself failed first).
        _git(real_root, "worktree", "remove", "--force", str(worktree_dir))
        if not succeeded:
            _git(real_root, "branch", "-D", branch_name)
        shutil.rmtree(tmp_root, ignore_errors=True)


def _seal_apply_result(
    state: OptimizationState,
    ports: NodePorts,
    patch_ref: ArtifactRef,
    *,
    applied: bool,
    base_revision: str,
    phase_id: str,
    approval_decision: str,
    approval_actor_id: str | None = None,
    branch_name: str | None = None,
    commit_sha: str | None = None,
    failure_reason: str | None = None,
) -> NodeExecution:
    record = _seal(
        RepositoryApplyResult(
            **_stage_envelope(state, _s06_stage("S06.90", state), "RepositoryApplyResult"),
            phase_id=phase_id,
            patch_digest=patch_ref.content_digest,
            applied=applied,
            branch_name=branch_name,
            commit_sha=commit_sha,
            base_revision=base_revision,
            failure_reason=failure_reason,
            approval_decision=approval_decision,
            approval_actor_id=approval_actor_id,
            policy_version=_POLICY_VERSION,
        )
    )
    ref = _put_envelope(ports, state, record, node_id="S06.90")
    return NodeExecution(
        updates={"artifact_refs": [ref], "s06_apply": {"applied": applied, "attempted": True}}
    )


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


def _s06_70(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """Route graph -- the real gate on REVERT: escalate rather than ever
    silently mark a case REVERT'ed when S06.60's real rollback verification
    failed (doc: ROLLBACK_FAILED "Escalate as critical incident; do not mark
    reverted"). KEEP/FIX_ONE_PART pass through unchanged."""

    del ports
    decision = dict(cast("dict[str, Any]", state.get("s06_decision") or {}))
    rollback = cast("dict[str, Any]", state.get("s06_rollback") or {})
    if decision.get("outcome") == "REVERT" and not rollback.get("verified", False):
        decision = {
            "outcome": "ESCALATE",
            "rationale": (
                "REVERT was decided but rollback verification failed -- escalating "
                "instead of marking the case reverted"
            ),
        }
    return NodeExecution(updates={"s06_decision": decision})


def _s06_80(state: OptimizationState, ports: NodePorts) -> NodeExecution:
    """The only node that seals `Decision` -- phase-and-pass scoped like
    S03.80/S04.90/S05.90. On a real FIX_ONE_PART, bumps
    `s03_revision_attempts` (the one other real writer besides S04.90 --
    see `contracts/state.py`'s docstring) so the outer `fix_one_part` route
    back to S03 really re-executes instead of replaying a cached pass. On a
    real REVERT, extends `s01_excluded_strategy_ids` (BR-01-005) so the
    outer `revert` route back to S01 cannot re-select the same strategy."""

    decision = cast("dict[str, Any]", state.get("s06_decision") or {})
    outcome = cast("str", decision.get("outcome", "ESCALATE"))
    active_phase_id = _active_phase_id(state)

    verification_ref = _require_stage_ref(state, _s04_report_stage(state), "VerificationReport")
    measurement_ref = _require_stage_ref(state, _s05_measurement_stage(state), "Measurement")
    target_evaluations = [
        TargetEvaluation.model_validate(raw)
        for raw in cast("list[dict[str, Any]]", state.get("s06_target_evaluations") or [])
    ]
    guardrail_evaluations = [
        GuardrailEvaluation.model_validate(raw)
        for raw in cast("list[dict[str, Any]]", state.get("s06_guardrail_evaluations") or [])
    ]
    attribution = PhaseAttribution.model_validate(
        cast("dict[str, Any]", state.get("s06_attribution") or {})
    )

    rollback_ref: ArtifactRef | None = None
    if outcome == "REVERT":
        rollback_ref = _require_stage_ref(state, _s06_stage("S06.60", state), "RollbackReport")

    met = sum(1 for t in target_evaluations if t.met)
    narrative = (
        f"Decision: {outcome}. {met}/{len(target_evaluations)} primary criteria met. "
        f"{decision.get('rationale', '')}"
    )

    record = _seal(
        Decision(
            **_stage_envelope(state, _s06_stage("S06.80", state), "Decision"),
            phase_id=active_phase_id,
            measurement_digest=measurement_ref.content_digest,
            verification_digest=verification_ref.content_digest,
            target_evaluations=target_evaluations,
            guardrail_evaluations=guardrail_evaluations,
            attribution=attribution,
            outcome=cast("Any", outcome),
            rationale=narrative,
            rollback_report_digest=rollback_ref.content_digest if rollback_ref else None,
            policy_version=_POLICY_VERSION,
        )
    )
    ref = _put_envelope(ports, state, record, node_id="S06.80")
    updates: dict[str, Any] = {
        "artifact_refs": [ref],
        "s06_decision": {**decision, "outcome": outcome},
    }

    if outcome == "FIX_ONE_PART":
        updates["s03_revision_attempts"] = (state.get("s03_revision_attempts", 0) or 0) + 1
    elif outcome == "REVERT":
        # `SelectedSolution` is pass-scoped in `s01_handlers` (BR-01-005) --
        # this REVERT is deciding on the selection that led to *this*
        # attempt, i.e. the current `s01_excluded_strategy_ids` length (that
        # list is only ever extended below, after this read).
        pass_number = len(cast("list[str]", state.get("s01_excluded_strategy_ids") or []))
        selected_ref = _require_stage_ref(state, f"S01.90-pass{pass_number}", "SelectedSolution")
        selected = _read_model(ports, state, selected_ref, SelectedSolution)
        excluded = set(cast("list[str]", state.get("s01_excluded_strategy_ids") or []))
        excluded.add(selected.strategy_id)
        updates["s01_excluded_strategy_ids"] = sorted(excluded)

    return NodeExecution(updates=updates)


# ---------------------------------------------------------------------------
# Shared helpers (mirrors s04_handlers.py/s05_handlers.py)
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


_MODEL_BY_TYPE: dict[str, type[ArtifactEnvelope]] = {
    "OptimizationRequest": cast("type[ArtifactEnvelope]", OptimizationRequest),
    "SourceSnapshot": cast("type[ArtifactEnvelope]", SourceSnapshot),
    "SelectedSolution": cast("type[ArtifactEnvelope]", SelectedSolution),
}


def _read_required(ports: NodePorts, state: OptimizationState, artifact_type: str) -> Any:
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
            f"{_required_state_str(state, 'case_id')}:{node_id}:"
            f"{envelope.artifact_type}:{envelope.artifact_id}"
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
        "created_at": _now(),
        "producer": _PRODUCER,
        "policy_versions": {"s06": "production-v1"},
        "content_digest": _ZERO_DIGEST,
        "parent_digests": parents or [],
    }


def _stage_artifact_id(case_id: str, node_id: str, artifact_type: str) -> str:
    return f"{case_id}-{node_id}-{artifact_type}"


def _stage_envelope(state: OptimizationState, node_id: str, artifact_type: str) -> dict[str, Any]:
    envelope = _base_envelope(state, artifact_type)
    envelope["artifact_id"] = _stage_artifact_id(
        _required_state_str(state, "case_id"), node_id, artifact_type
    )
    return envelope


def _s03_patch_stage(state: OptimizationState) -> str:
    return f"S03.80-{_active_phase_id(state)}-pass{_pass_number(state)}"


def _require_stage_ref(state: OptimizationState, node_id: str, artifact_type: str) -> ArtifactRef:
    artifact_id = _stage_artifact_id(_required_state_str(state, "case_id"), node_id, artifact_type)
    for ref in state.get("artifact_refs", []):
        if ref.artifact_type == artifact_type and ref.artifact_id == artifact_id:
            return ref
    raise ValueError(f"missing required {artifact_type} produced by {node_id}")


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
        raise ValueError(f"S06 state is missing required field {key!r}")
    return value


__all__ = ["build_s06_registrations", "build_s06_runtime"]

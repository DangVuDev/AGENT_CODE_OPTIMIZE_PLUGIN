from __future__ import annotations

from typing import Any, cast

from production_optimizer.contracts.platform import PolicyDecision, PolicyRequest

from .python_policy import DeterministicPythonPolicy

# The command kinds A2.31 is allowed to detect and A2.50 to authorize.
_ALLOWED_COMMAND_KINDS = frozenset(
    {"build", "unit", "lint", "type", "benchmark", "ci", "compose_evaluation"}
)

# `source_type` vocabulary shared with `a2_handlers._EVIDENCE_TYPE_SOURCE_TYPE`.
_ALLOWED_SOURCE_TYPES = frozenset({"test", "static", "benchmark", "telemetry", "source_map"})

# Shell metacharacters have no meaning in an `argv` list (nothing runs these
# through a shell), so their presence means the argv was not built by the
# detector that is supposed to build it.
_SHELL_METACHARACTERS = (";", "&&", "||", "|", ">", "<", "`", "$(")

_MAX_COMMAND_TIMEOUT_SECONDS = 3600


def build_bootstrap_policy(*, policy_version: str) -> DeterministicPythonPolicy:
    """A real fail-closed `PolicyPort` for the decisions Lane A/B actually make.

    Replaces the allow-everything stand-in the runnable entrypoints used.
    The point is not that each individual check is elaborate -- several of
    these fact sets are small and the producing node already validated them
    -- but that this is a *chokepoint*:

    * unknown `decision_type`s are denied, so a decision added later cannot
      quietly execute under an implicit allow;
    * command execution has one place that can refuse, independent of
      whichever node proposed the command;
    * every decision records `reasons` into the sealed evidence chain under
      a pinned `policy_version`, which an allow-everything stub never did.

    Where a fact set genuinely does not support a stronger rule, the
    decision allows and says so in its reasons rather than inventing a
    check that only looks strict.
    """

    policy = DeterministicPythonPolicy(policy_version=policy_version)
    policy.register("a1.request_approval", _a1_request_approval(policy_version))
    policy.register("a2.execution_authorization", _execution_authorization(policy_version))
    policy.register("a2.collector_authorization", _collector_authorization(policy_version))
    policy.register("s03_phase_authorization", _phase_authorization(policy_version))
    policy.register("apply_to_real_repo", _apply_to_real_repo(policy_version))
    policy.register("automatic_intake", _automatic_intake(policy_version))
    policy.register("automatic_discovery_read", _discovery_read(policy_version))
    return policy


def _deny(policy_version: str, reasons: list[str]) -> PolicyDecision:
    return PolicyDecision(
        allowed=False, decision="deny", policy_version=policy_version, reasons=reasons
    )


def _allow(policy_version: str, reasons: list[str]) -> PolicyDecision:
    return PolicyDecision(
        allowed=True, decision="allow", policy_version=policy_version, reasons=reasons
    )


def _execution_authorization(policy_version: str):
    def decide(request: PolicyRequest) -> PolicyDecision:
        facts = request.facts
        kind = facts.get("kind")
        argv = facts.get("argv")
        working_directory = facts.get("working_directory")
        timeout = facts.get("timeout_seconds")
        reasons: list[str] = []

        if kind not in _ALLOWED_COMMAND_KINDS:
            reasons.append(f"command kind {kind!r} is not an authorized kind")
        if not isinstance(argv, list) or not argv:
            reasons.append("argv must be a non-empty list")
        else:
            argv_items = [str(item) for item in cast("list[Any]", argv)]
            if any(
                metacharacter in item
                for item in argv_items
                for metacharacter in _SHELL_METACHARACTERS
            ):
                reasons.append("argv contains shell metacharacters; it is executed without a shell")
        if not isinstance(working_directory, str) or not working_directory.strip():
            reasons.append("working_directory must be a non-empty path")
        if not isinstance(timeout, int | float) or not 0 < float(timeout) <= (
            _MAX_COMMAND_TIMEOUT_SECONDS
        ):
            reasons.append(f"timeout_seconds must be within (0, {_MAX_COMMAND_TIMEOUT_SECONDS}]")

        if reasons:
            return _deny(policy_version, reasons)
        return _allow(policy_version, [f"{kind} command authorized for local execution"])

    return decide


def _a1_request_approval(policy_version: str):
    def decide(request: PolicyRequest) -> PolicyDecision:
        actor_role = request.facts.get("actor_role")
        if actor_role in {"owner", "approver", "platform_owner"}:
            return _allow(policy_version, [f"role {actor_role!r} may approve this request"])
        if actor_role == "requester":
            return PolicyDecision(
                allowed=True,
                decision="require_approval",
                policy_version=policy_version,
                reasons=["requester role requires an owner approval"],
            )
        return _deny(policy_version, [f"role {actor_role!r} cannot submit an A1 request"])

    return decide


def _collector_authorization(policy_version: str):
    def decide(request: PolicyRequest) -> PolicyDecision:
        source_type = request.facts.get("source_type")
        collector_id = request.facts.get("collector_id")
        recipe_id = request.facts.get("recipe_id")
        registry_version = request.facts.get("registry_version")
        reasons: list[str] = []

        registry_backed = (
            isinstance(recipe_id, str)
            and bool(recipe_id.strip())
            and isinstance(registry_version, int)
            and not isinstance(registry_version, bool)
            and registry_version >= 1
        )
        if source_type not in _ALLOWED_SOURCE_TYPES and not registry_backed:
            reasons.append(f"source_type {source_type!r} is outside the evidence vocabulary")
        if not isinstance(collector_id, str) or not collector_id.strip():
            reasons.append("collector_id must be a non-empty identifier")

        if reasons:
            return _deny(policy_version, reasons)
        return _allow(policy_version, [f"collector {collector_id!r} authorized"])

    return decide


def _phase_authorization(policy_version: str):
    def decide(request: PolicyRequest) -> PolicyDecision:
        """Authorize one implementation phase to start editing code.

        The facts carry only `phase_id`/`case_id`, so this cannot re-derive
        risk here. What actually constrains the edit is enforced where the
        edit happens -- `s03_agent_loop` refuses writes outside the phase's
        `ScopeResolutionReport` paths and runs only the one pre-authorized
        command -- and this decision records that authorization in the audit
        chain under a pinned policy version.
        """

        phase_id = request.facts.get("phase_id")
        case_id = request.facts.get("case_id")
        reasons: list[str] = []

        if not isinstance(phase_id, str) or not phase_id.strip():
            reasons.append("phase_id must identify the phase being authorized")
        if not isinstance(case_id, str) or not case_id.strip():
            reasons.append("case_id must identify the owning case")

        if reasons:
            return _deny(policy_version, reasons)
        return _allow(
            policy_version,
            [f"phase {phase_id!r} authorized; writes remain bound to its resolved scope"],
        )

    return decide


def _apply_to_real_repo(policy_version: str):
    def decide(request: PolicyRequest) -> PolicyDecision:
        """Decide whether S06.90 may land an accepted KEEP as a real commit
        in the user's real repository.

        Always `require_approval` for now: there is no risk-tier signal yet
        that would justify ever returning a bare `allow` for a real,
        permanent git write (unlike `_phase_authorization`, which authorizes
        work confined to a disposable, isolated workspace). A future,
        more permissive tenant policy can key off the `phase_id`/
        `base_revision`/`changed_files` facts this decision already
        receives without any change to `s06_handlers._s06_90` itself.
        """

        phase_id = request.facts.get("phase_id")
        case_id = request.facts.get("case_id")
        base_revision = request.facts.get("base_revision")
        reasons: list[str] = []

        if not isinstance(phase_id, str) or not phase_id.strip():
            reasons.append("phase_id must identify the phase being applied")
        if not isinstance(case_id, str) or not case_id.strip():
            reasons.append("case_id must identify the owning case")
        if not isinstance(base_revision, str) or not base_revision.strip():
            reasons.append("base_revision must identify the commit the patch was built against")

        if reasons:
            return _deny(policy_version, reasons)
        return PolicyDecision(
            allowed=True,
            decision="require_approval",
            policy_version=policy_version,
            reasons=[
                f"phase {phase_id!r} requires an owner approval before a real repository write"
            ],
        )

    return decide


def _automatic_intake(policy_version: str):
    def decide(request: PolicyRequest) -> PolicyDecision:
        return _require_non_empty_facts(
            request, policy_version, required=("opportunity_id",), subject="automatic intake"
        )

    return decide


def _discovery_read(policy_version: str):
    def decide(request: PolicyRequest) -> PolicyDecision:
        return _require_non_empty_facts(
            request, policy_version, required=("query_kind",), subject="historical discovery read"
        )

    return decide


def _require_non_empty_facts(
    request: PolicyRequest,
    policy_version: str,
    *,
    required: tuple[str, ...],
    subject: str,
) -> PolicyDecision:
    missing = [key for key in required if not _present(request.facts.get(key))]
    if missing:
        return _deny(policy_version, [f"{subject} is missing required facts: {missing}"])
    return _allow(policy_version, [f"{subject} authorized"])


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True

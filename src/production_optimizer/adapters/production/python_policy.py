from __future__ import annotations

from collections.abc import Callable

from production_optimizer.contracts.platform import PolicyDecision, PolicyRequest

DecisionFn = Callable[[PolicyRequest], PolicyDecision]

_BOOTSTRAP_REASONS = ["business policies are not implemented"]


class DeterministicPythonPolicy:
    """Primary production ``PolicyPort``: a versioned registry of pure Python decisions.

    Per project decision, deterministic Python decision functions are the
    primary production policy mechanism; an OPA HTTP adapter implementing
    the same ``PolicyPort`` is deferred to later work.

    A single instance is bound to exactly one ``policy_version`` -- matching
    "approval is bound to ... policy version" -- so standing up a new policy
    version means constructing a new instance rather than mutating this one.
    The registry maps ``decision_type`` to a pure ``PolicyRequest ->
    PolicyDecision`` callable. Any ``decision_type`` without a registered
    handler resolves to a fail-closed bootstrap default mirroring
    ``policies/bootstrap.rego``'s ``default allow := false``: ``allowed=False``,
    ``decision="deny"``, ``reasons=["business policies are not implemented"]``.
    This preserves the project's fail-closed posture while giving real
    callers a real decision path once they register one via ``register()``.
    """

    def __init__(self, *, policy_version: str) -> None:
        if not policy_version.strip():
            raise ValueError("policy_version must not be empty")
        self._policy_version = policy_version
        self._registry: dict[str, DecisionFn] = {}

    def register(self, decision_type: str, fn: DecisionFn) -> None:
        """Register a decision function for ``decision_type``, overriding any prior one."""

        if not decision_type.strip():
            raise ValueError("decision_type must not be empty")
        self._registry[decision_type] = fn

    def evaluate(self, request: PolicyRequest) -> PolicyDecision:
        fn = self._registry.get(request.decision_type)
        if fn is None:
            return self._bootstrap_decision()
        decision = fn(request)
        if decision.policy_version != self._policy_version:
            raise ValueError(
                f"registered decision function for {request.decision_type!r} returned "
                f"policy_version {decision.policy_version!r}, expected "
                f"{self._policy_version!r}"
            )
        return decision

    def healthcheck(self) -> bool:
        # Deterministic Python decisions have no external dependency (no
        # network call, database, or filesystem access), so -- unlike the
        # other adapters in this module -- there is nothing that can make
        # this unhealthy; it always returns True.
        return True

    def _bootstrap_decision(self) -> PolicyDecision:
        return PolicyDecision(
            allowed=False,
            decision="deny",
            policy_version=self._policy_version,
            reasons=list(_BOOTSTRAP_REASONS),
        )

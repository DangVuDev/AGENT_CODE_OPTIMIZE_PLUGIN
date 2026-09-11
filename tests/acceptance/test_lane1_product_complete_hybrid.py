from __future__ import annotations

from pathlib import Path

from production_optimizer.application import (
    NodeRuntime,
    build_a1_registrations,
    build_pilot_registrations,
)
from production_optimizer.contracts.a1 import ManualCasePayload
from production_optimizer.orchestration import build_root_graph
from tests.contract.test_a1_production_handlers import (
    _MemoryArtifactStore,
    _payload_ref,
    _ports,
    _state,
)


def test_lane1_product_complete_hybrid_acceptance_flow(tmp_path: Path) -> None:
    """Product-shape acceptance test.

    A1 uses production handlers. A2/A3/C0 intentionally use deterministic pilot
    registrations until those production slices exist. This gives a realistic
    finished-product input/output contract without pretending all business
    handlers are complete.
    """

    (tmp_path / "pyproject.toml").write_text("[project]\nname='checkout-demo'\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "checkout.py").write_text("def checkout():\n    return 'ok'\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_checkout.py").write_text("def test_checkout():\n    assert True\n")

    payload = ManualCasePayload(
        structured_request={
            "objective": {
                "statement": "Reduce checkout p95 latency while preserving correctness",
                "feature_id": "checkout",
            },
            "criteria": [
                {
                    "metric_id": "p95_latency_ms",
                    "direction": "minimize",
                    "target": 180.0,
                    "unit": "ms",
                }
            ],
            "workload": {
                "workload_id": "checkout-load",
                "dataset_id": "checkout-fixture-small",
                "environment_id": "local-dev",
                "command_id": "pytest",
            },
        },
        local_path=str(tmp_path),
        allowed_root=str(tmp_path),
        actor_id="owner-1",
        actor_role="owner",
        policy_version="intake-policy-v1",
    )
    store = _MemoryArtifactStore()
    registrations = build_pilot_registrations()
    registrations.update(build_a1_registrations())
    runtime = NodeRuntime(registrations, ports=_ports(store))
    graph = build_root_graph(runtime=runtime)

    result = graph.invoke(_state(_payload_ref(store, payload)))

    completed = set(result["completed_nodes"])
    artifact_types = {ref.artifact_type for ref in result["artifact_refs"]}

    assert result["status"] == "converged"
    assert result["current_node"] == "C0.70"
    assert {"A1.95", "A2.95", "A3.90", "C0.70"} <= completed
    assert result["request_ref"].artifact_type == "OptimizationRequest"
    assert result["baseline_ref"].artifact_type == "BaselineSnapshot"
    assert result["solution_portfolio_ref"].artifact_type == "SolutionPortfolio"
    assert result["convergence_ref"].artifact_type == "ConvergedCase"
    assert {
        "OptimizationRequest",
        "SourceSnapshot",
        "BaselineSnapshot",
        "EvidenceBundle",
        "FindingSet",
        "SolutionPortfolio",
        "ConvergenceDecision",
        "ConvergedCase",
    } <= artifact_types

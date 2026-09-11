from __future__ import annotations

from production_optimizer.application import build_pilot_runtime
from production_optimizer.orchestration import build_root_graph
from production_optimizer.orchestration.catalog import A1_NODE_IDS, B1_NODE_IDS, B2_NODE_IDS


def test_pilot_runtime_runs_manual_lane_to_convergence() -> None:
    result = build_root_graph(runtime=build_pilot_runtime()).invoke(
        {
            "case_id": "OPT-PILOT-1",
            "thread_id": "THREAD-PILOT-1",
            "tenant_id": "TENANT-PILOT",
            "entrypoint": "manual",
            "lane": "manual",
        }
    )

    completed = set(result["completed_nodes"])
    artifact_types = {ref.artifact_type for ref in result["artifact_refs"]}

    assert result["status"] == "converged"
    assert result["baseline_mode"] == "active_collection"
    assert {"A1.95", "A2.95", "A3.90", "C0.70"} <= completed
    assert not completed & set(B1_NODE_IDS)
    assert not completed & set(B2_NODE_IDS)
    assert {
        "OptimizationRequest",
        "BaselineSnapshot",
        "EvidenceBundle",
        "FindingSet",
        "SolutionPortfolio",
        "ConvergedCase",
    } <= artifact_types


def test_pilot_runtime_runs_discovery_scan_through_shared_a2() -> None:
    result = build_root_graph(runtime=build_pilot_runtime()).invoke(
        {
            "case_id": "SCAN-PILOT-1",
            "thread_id": "SCAN-THREAD-PILOT-1",
            "tenant_id": "TENANT-PILOT",
            "entrypoint": "discovery",
            "lane": "automatic",
        }
    )

    completed = set(result["completed_nodes"])
    artifact_types = {ref.artifact_type for ref in result["artifact_refs"]}

    assert result["baseline_mode"] == "historical_recovery"
    assert {"B1.96", "A2.95"} <= completed
    assert not completed & set(A1_NODE_IDS)
    assert {"DetectionReport", "OptimizationRequest", "QualifiedOpportunity"} <= artifact_types


def test_pilot_runtime_runs_qualified_case_through_shared_a3_and_c0() -> None:
    result = build_root_graph(runtime=build_pilot_runtime()).invoke(
        {
            "case_id": "OPT-PILOT-2",
            "thread_id": "CASE-THREAD-PILOT-2",
            "tenant_id": "TENANT-PILOT",
            "entrypoint": "qualified",
            "lane": "automatic",
        }
    )

    completed = set(result["completed_nodes"])
    artifact_types = {ref.artifact_type for ref in result["artifact_refs"]}

    assert result["status"] == "converged"
    assert result["baseline_mode"] == "historical_recovery"
    assert {"B2.60", "A3.90", "C0.70"} <= completed
    assert not completed & set(A1_NODE_IDS)
    assert {
        "FindingSet",
        "SolutionPortfolio",
        "ProposalEnvelope",
        "ConvergedCase",
    } <= artifact_types

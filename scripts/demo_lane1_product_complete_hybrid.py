from __future__ import annotations

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

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


def main() -> None:
    with TemporaryDirectory(dir=".local-data") as workspace:
        source_root = Path(workspace)
        build_demo_repository(source_root)
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
            local_path=str(source_root),
            allowed_root=str(source_root),
            actor_id="owner-1",
            actor_role="owner",
            policy_version="intake-policy-v1",
        )
        run_product_shape_demo(payload)


def build_demo_repository(source_root: Path) -> None:
    (source_root / "pyproject.toml").write_text(
        "[project]\nname = \"checkout-demo\"\n",
        encoding="utf-8",
    )
    (source_root / "src").mkdir()
    (source_root / "src" / "checkout.py").write_text(
        "def checkout():\n    return 'ok'\n",
        encoding="utf-8",
    )
    (source_root / "tests").mkdir()
    (source_root / "tests" / "test_checkout.py").write_text(
        "def test_checkout():\n    assert True\n",
        encoding="utf-8",
    )


def run_product_shape_demo(payload: ManualCasePayload) -> None:
    store = _MemoryArtifactStore()
    registrations = build_pilot_registrations()
    registrations.update(build_a1_registrations())
    runtime = NodeRuntime(registrations, ports=_ports(store))
    graph = build_root_graph(runtime=runtime)
    result = graph.invoke(_state(_payload_ref(store, payload)))

    print("INPUT")
    print(f"  objective: {payload.structured_request['objective']['statement']}")
    print(f"  feature: {payload.structured_request['objective']['feature_id']}")
    print(f"  metric: {payload.structured_request['criteria'][0]['metric_id']}")
    print(f"  target: {payload.structured_request['criteria'][0]['target']} ms")
    print(f"  workload: {payload.structured_request['workload']['workload_id']}")
    print()
    print("OUTPUT")
    print(f"  status: {result['status']}")
    print(f"  current_node: {result['current_node']}")
    print(f"  request: {result['request_ref'].artifact_type}")
    print(f"  baseline: {result['baseline_ref'].artifact_type}")
    print(f"  solution_portfolio: {result['solution_portfolio_ref'].artifact_type}")
    print(f"  convergence: {result['convergence_ref'].artifact_type}")
    print(f"  completed_nodes: {len(result['completed_nodes'])}")
    print()
    print("NOTE")
    print("  A1 is production handler logic.")
    print("  A2/A3/C0 are deterministic pilot handlers until their production slices exist.")


if __name__ == "__main__":
    main()

"""Run the real A1 -> A2 Compose-evaluator flow against the Go fixture."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from _lane1_common import build_control_plane, read_model, ref_by_type

from production_optimizer.adapters.production.local_worker_broker import LocalWorkerBroker
from production_optimizer.application import NodePorts, build_a1_runtime, build_a2_runtime
from production_optimizer.application.a2_worker_capabilities import (
    build_local_command_capabilities,
)
from production_optimizer.contracts.a1 import ManualCasePayload, OptimizationRequest
from production_optimizer.contracts.a2 import BranchEvidenceRefs, EvidenceQualityReport
from production_optimizer.contracts.artifacts import ArtifactRef
from production_optimizer.contracts.canonical import canonical_json, sha256_digest
from production_optimizer.contracts.evaluation import ContainerCommandSpec, EvaluationSpec
from production_optimizer.contracts.platform import ActorContext
from production_optimizer.orchestration.subgraphs import build_a1_graph, build_a2_graph

ROOT = Path(__file__).resolve().parents[1]
TENANT_ID = "TENANT-GO-FIXTURE"

PROFILES = {
    "local": {
        "source": ROOT / "fixtures" / "go-checkout",
        "feature_id": "checkout",
        "metric_id": "p95_latency_ms",
        "target": 50.0,
        "workload_id": "go-checkout-http",
        "compose_file": "compose.yaml",
        "service": "app",
        "argv": ["sh", "/app/scripts/evaluate-checkout.sh"],
        "evaluation_id": "checkout-http",
    },
    "realworld": {
        "source": ROOT / "fixtures" / "external" / "realworld-go",
        "feature_id": "user-domain",
        "metric_id": "suite_runtime_ms",
        "target": 30_000.0,
        "workload_id": "realworld-go-unit-suite",
        "compose_file": "optimizer.compose.yaml",
        "service": "testbed",
        "argv": ["sh", "scripts/optimizer-evaluate-users.sh"],
        "evaluation_id": "realworld-user-suite",
    },
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--a1-only", action="store_true", help="validate and freeze the request without Docker"
    )
    parser.add_argument("--fixture", choices=sorted(PROFILES), default="local")
    args = parser.parse_args()
    profile = PROFILES[args.fixture]
    source = Path(profile["source"])
    case_id = f"OPT-GO-{uuid4().hex[:10]}"
    control = build_control_plane(service_name="go-compose-a1-a2-test")
    payload = ManualCasePayload(
        local_path=str(source),
        allowed_root=str(source),
        feature_id=str(profile["feature_id"]),
        metric_id=str(profile["metric_id"]),
        direction="minimize",
        target=float(profile["target"]),
        unit="ms",
        guardrail_metric_id="correctness",
        workload_id=str(profile["workload_id"]),
        environment_id="docker-compose-local",
        actor_id="go-fixture-owner",
        actor_role="owner",
        execution_profile="docker_compose",
        compose_file=str(profile["compose_file"]),
        application_services=[str(profile["service"])],
        evaluations=[
            EvaluationSpec(
                evaluation_id=str(profile["evaluation_id"]),
                command=ContainerCommandSpec(
                    service=str(profile["service"]),
                    argv=list(profile["argv"]),
                    timeout_seconds=60,
                ),
                repetitions=5,
                warmup_runs=1,
                expected_metric_ids={str(profile["metric_id"]), "correctness"},
            )
        ],
        repetitions=5,
        warmup_runs=1,
        maximum_worker_seconds=300,
        deadline_seconds=600,
    )
    payload_bytes = canonical_json(payload)
    payload_digest = sha256_digest(payload_bytes)
    stored = control.artifacts.put_json(
        tenant_id=TENANT_ID,
        content=payload_bytes,
        content_digest=payload_digest,
        idempotency_key=f"{case_id}:ManualCasePayload",
    )
    payload_ref = ArtifactRef(
        artifact_type="ManualCasePayload",
        schema_version="1.0",
        artifact_id=f"{case_id}-payload",
        content_digest=payload_digest,
        uri=stored.uri,
    )
    state = {
        "case_id": case_id,
        "thread_id": f"THREAD-{case_id}",
        "tenant_id": TENANT_ID,
        "entrypoint": "manual",
        "lane": "manual",
        "baseline_mode": "active_collection",
        "actor_context": ActorContext(
            actor_id="go-fixture-owner",
            tenant_id=TENANT_ID,
            roles={"owner"},
            authenticated_at=datetime.now(UTC),
        ),
        "artifact_refs": [payload_ref],
    }

    ports = NodePorts(
        artifacts=control.artifacts,
        intents=control.intents,
        telemetry=control.telemetry,
        policy=control.policy,
    )
    state = build_a1_graph(build_a1_runtime(ports=ports)).invoke(state)  # type: ignore[arg-type]
    if state.get("request_ref") is None:
        raise SystemExit(f"A1 failed: {state.get('node_routes')}")
    request = read_model(
        control.artifacts, TENANT_ID, state["request_ref"], OptimizationRequest
    )
    print("A1 OptimizationRequest:")
    print(
        json.dumps(
            {
                "feature_id": request.objective.feature_id,
                "workload": request.workload.workload_id,
                "compose_file": request.execution.compose_file if request.execution else None,
                "evaluations": [
                    item.evaluation_id for item in request.execution.evaluations
                ]
                if request.execution
                else [],
            },
            indent=2,
        )
    )
    if args.a1_only:
        return

    broker = LocalWorkerBroker(
        capabilities=build_local_command_capabilities(control.artifacts, tenant_id=TENANT_ID)
    )
    try:
        a2_ports = NodePorts(
            artifacts=control.artifacts,
            intents=control.intents,
            telemetry=control.telemetry,
            policy=control.policy,
            workers=broker,
        )
        state = build_a2_graph(build_a2_runtime(ports=a2_ports)).invoke(state)
    finally:
        broker.close()

    branch_ref = next(
        (
            ref
            for ref in state.get("artifact_refs", [])
            if ref.artifact_type == "BranchEvidenceRefs" and "A2.62" in ref.artifact_id
        ),
        None,
    )
    if branch_ref is not None:
        branch = read_model(control.artifacts, TENANT_ID, branch_ref, BranchEvidenceRefs)
        print("A2.62 evidence:")
        print(
            json.dumps(
                [
                    {
                        "metric_id": item.metric_id,
                        "value": item.value,
                        "unit": item.unit,
                        "sample_id": item.identity.sample_id,
                    }
                    for item in branch.evidence
                ],
                indent=2,
            )
        )
        if branch.unavailable_reason:
            print(f"A2.62 unavailable_reason: {branch.unavailable_reason}")
    quality_ref = ref_by_type(state, "EvidenceQualityReport")
    if quality_ref is None:
        raise SystemExit("A2 did not produce EvidenceQualityReport")
    quality = read_model(control.artifacts, TENANT_ID, quality_ref, EvidenceQualityReport)
    print(f"A2 EvidenceQualityReport.passed = {quality.passed}")
    if quality.sample_failures:
        print("A2 sample failures:")
        for failure in quality.sample_failures:
            print(f"  - {failure}")
    if not quality.passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

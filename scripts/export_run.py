"""CLI: run Lane A (A1 -> A2 -> A3 -> C0) and export the whole run as one JSON
file the pipeline inspector UI can load.

    python scripts/export_run.py <path-to-repo> --feature-id ... --metric ... \
        --direction ... --target ... --unit ... --command-id ... \
        --out run.json

Takes `scripts/optimize.py`'s CLI verbatim (including --execution-profile
docker_compose and every --eval-* flag) and adds `--out`. Where `optimize.py`
prints a human summary and throws the state away, this captures it: per-stage
node routes, every sealed `ArtifactRef`, and each artifact's real decoded
content, so the inspector can show what actually went in and out of each node
without re-running anything.

A stage that legitimately stops early (A1 halting for approval, A3 rejecting
with no finding whose citations resolve) is exported as such -- `status` on
that stage says why, and the stages after it are exported as "not_reached"
rather than omitted, since "this never ran" is exactly what the inspector
needs to show.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from _lane1_common import build_control_plane, select_model_provider
from optimize import build_payload, parse_args, seed_payload

from production_optimizer.adapters.production.local_worker_broker import LocalWorkerBroker
from production_optimizer.application import (
    NodePorts,
    build_a1_runtime,
    build_a2_runtime,
    build_a3_runtime,
    build_c0_runtime,
)
from production_optimizer.application.a2_worker_capabilities import (
    build_local_command_capabilities,
)
from production_optimizer.contracts.platform import ActorContext
from production_optimizer.orchestration.catalog import (
    A1_NODE_IDS,
    A2_NODE_IDS,
    A3_NODE_IDS,
    C0_NODE_IDS,
)
from production_optimizer.orchestration.node_manifest import NODE_MANIFEST
from production_optimizer.orchestration.subgraphs import (
    build_a1_graph,
    build_a2_graph,
    build_a3_graph,
    build_c0_graph,
)

_TENANT_ID = "TENANT-CLI"
CONTROL_PLANE = build_control_plane(service_name="production-optimizer-export")

_STAGES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("A1", "Requirement Intake", A1_NODE_IDS),
    ("A2", "Real Baseline", A2_NODE_IDS),
    ("A3", "Grounded Solutions", A3_NODE_IDS),
    ("C0", "Convergence", C0_NODE_IDS),
)


def _artifact_payload(store: Any, ref: Any) -> dict[str, Any]:
    """Decode one sealed artifact so the inspector can show real content.

    A ref whose bytes cannot be read or parsed is still exported (with the
    failure named): an inspector that silently dropped it would be lying
    about what the run produced.
    """

    entry: dict[str, Any] = {
        "artifact_type": ref.artifact_type,
        "artifact_id": ref.artifact_id,
        "schema_version": ref.schema_version,
        "content_digest": ref.content_digest,
        "uri": ref.uri,
    }
    try:
        raw = store.read(tenant_id=_TENANT_ID, ref=ref)
        entry["content"] = json.loads(raw)
    except (OSError, KeyError, ValueError, TypeError) as exc:
        entry["read_error"] = f"{type(exc).__name__}: {exc}"
    return entry


def _stage_snapshot(
    stage: str,
    title: str,
    node_ids: tuple[str, ...],
    state: dict[str, Any],
    store: Any,
    *,
    known_artifact_ids: set[str],
) -> dict[str, Any]:
    completed = set(state.get("completed_nodes", []))
    routes = {k: v for k, v in state.get("node_routes", {}).items() if k.split(".")[0] == stage}

    nodes: list[dict[str, Any]] = []
    for node_id in node_ids:
        manifest_entry = NODE_MANIFEST.get(node_id)
        nodes.append(
            {
                "node_id": node_id,
                "description": manifest_entry.description if manifest_entry else "",
                "produces": list(manifest_entry.produces) if manifest_entry else [],
                "completed": node_id in completed,
                "route": routes.get(node_id),
            }
        )

    # Only the artifacts this stage newly added -- `artifact_refs` accumulates
    # across the whole case, so without this every stage would claim every
    # earlier stage's outputs as its own.
    new_artifacts = [
        _artifact_payload(store, ref)
        for ref in state.get("artifact_refs", [])
        if ref.artifact_id not in known_artifact_ids
    ]
    known_artifact_ids.update(
        entry["artifact_id"] for entry in new_artifacts if "artifact_id" in entry
    )

    halted = state.get("pending_interrupt")
    halted_here = halted is not None and halted.stage.split(".")[0] == stage
    if halted_here:
        status = "halted"
    elif any(route == "rejected" for route in routes.values()):
        status = "rejected"
    elif node_ids[-1] in completed:
        status = "passed"
    elif completed & set(node_ids):
        status = "incomplete"
    else:
        status = "not_reached"

    snapshot: dict[str, Any] = {
        "stage": stage,
        "title": title,
        "status": status,
        "nodes": nodes,
        "artifacts": new_artifacts,
        "completed_count": len(completed & set(node_ids)),
        "node_count": len(node_ids),
    }
    if halted_here and halted is not None:
        snapshot["interrupt"] = {
            "stage": halted.stage,
            "interrupt_id": halted.interrupt_id,
            "allowed_decisions": list(halted.allowed_decisions),
            "required_actor_role": halted.required_actor_role,
            "expires_at": halted.expires_at.isoformat(),
        }
    return snapshot


def _empty_stage(stage: str, title: str, node_ids: tuple[str, ...]) -> dict[str, Any]:
    return {
        "stage": stage,
        "title": title,
        "status": "not_reached",
        "nodes": [
            {
                "node_id": node_id,
                "description": (
                    NODE_MANIFEST[node_id].description if node_id in NODE_MANIFEST else ""
                ),
                "produces": (
                    list(NODE_MANIFEST[node_id].produces) if node_id in NODE_MANIFEST else []
                ),
                "completed": False,
                "route": None,
            }
            for node_id in node_ids
        ],
        "artifacts": [],
        "completed_count": 0,
        "node_count": len(node_ids),
    }


def main() -> None:
    argv = sys.argv[1:]
    out_path = Path("run.json")
    if "--out" in argv:
        index = argv.index("--out")
        out_path = Path(argv[index + 1])
        argv = argv[:index] + argv[index + 2 :]

    args = parse_args(argv)
    repo_path = args.repo_path.resolve()
    if not repo_path.exists():
        raise SystemExit(f"repo path does not exist: {repo_path}")
    args.repo_path = repo_path

    store = CONTROL_PLANE.artifacts
    payload = build_payload(args)
    payload_ref = seed_payload(store, args.case_id, payload)
    known_artifact_ids: set[str] = {payload_ref.artifact_id}

    export: dict[str, Any] = {
        "case_id": args.case_id,
        "exported_at": datetime.now(UTC).isoformat(),
        "repository": str(repo_path),
        "request": {
            "feature_id": args.feature_id,
            "metric_id": args.metric,
            "direction": args.direction,
            "target": args.target,
            "unit": args.unit,
            "guardrail_metric_id": args.guardrail_metric_id,
            "execution_profile": args.execution_profile,
            "command_id": args.command_id,
        },
        "input_payload": json.loads(store.read(tenant_id=_TENANT_ID, ref=payload_ref)),
        "stages": [],
    }

    state: dict[str, Any] = {
        "case_id": args.case_id,
        "thread_id": f"THREAD-{args.case_id}",
        "tenant_id": _TENANT_ID,
        "entrypoint": "manual",
        "lane": "manual",
        "baseline_mode": "active_collection",
        "actor_context": ActorContext(
            actor_id=args.actor_id,
            tenant_id=_TENANT_ID,
            roles={"owner"},
            authenticated_at=datetime.now(UTC),
        ),
        "artifact_refs": [payload_ref],
    }

    base_ports = {
        "artifacts": store,
        "intents": CONTROL_PLANE.intents,
        "policy": CONTROL_PLANE.policy,
        "telemetry": CONTROL_PLANE.telemetry,
    }

    print("=== A1 ===")
    state = build_a1_graph(build_a1_runtime(ports=NodePorts(**base_ports))).invoke(state)
    export["stages"].append(
        _stage_snapshot(*_STAGES[0], state, store, known_artifact_ids=known_artifact_ids)
    )
    print(f"  status={export['stages'][-1]['status']}")

    if state.get("request_ref") is not None:
        print("=== A2 ===")
        broker = LocalWorkerBroker(
            capabilities=build_local_command_capabilities(store, tenant_id=_TENANT_ID)
        )
        try:
            state = build_a2_graph(
                build_a2_runtime(ports=NodePorts(**base_ports, workers=broker))
            ).invoke(state)
        finally:
            broker.close()
        export["stages"].append(
            _stage_snapshot(*_STAGES[1], state, store, known_artifact_ids=known_artifact_ids)
        )
        print(f"  status={export['stages'][-1]['status']}")
    else:
        export["stages"].append(_empty_stage(*_STAGES[1]))

    if any(ref.artifact_type == "BaselineSnapshot" for ref in state.get("artifact_refs", [])):
        print("=== A3 ===")
        model_provider, model_id = select_model_provider(
            tenant_id=_TENANT_ID,
            thread_id=f"THREAD-{args.case_id}",
            deferrals=CONTROL_PLANE.model_deferrals,
        )
        export["model"] = {"provider": type(model_provider).__name__, "model_id": model_id}
        try:
            state = build_a3_graph(
                build_a3_runtime(
                    ports=NodePorts(**base_ports, model=model_provider, model_id=model_id)
                ),
                checkpointer=CONTROL_PLANE.checkpointer,
            ).invoke(state, config={"configurable": {"thread_id": f"THREAD-{args.case_id}"}})
            export["stages"].append(
                _stage_snapshot(*_STAGES[2], state, store, known_artifact_ids=known_artifact_ids)
            )
        except Exception as exc:
            stage = _empty_stage(*_STAGES[2])
            stage["status"] = "error"
            stage["error"] = f"{type(exc).__name__}: {exc}"
            export["stages"].append(stage)
        print(f"  status={export['stages'][-1]['status']}")
    else:
        export["stages"].append(_empty_stage(*_STAGES[2]))

    if any(ref.artifact_type == "SolutionPortfolio" for ref in state.get("artifact_refs", [])):
        print("=== C0 ===")
        state = build_c0_graph(build_c0_runtime(ports=NodePorts(**base_ports))).invoke(state)
        export["stages"].append(
            _stage_snapshot(*_STAGES[3], state, store, known_artifact_ids=known_artifact_ids)
        )
        print(f"  status={export['stages'][-1]['status']}")
    else:
        export["stages"].append(_empty_stage(*_STAGES[3]))

    out_path.write_text(json.dumps(export, indent=2, ensure_ascii=False), encoding="utf-8")
    total_artifacts = sum(len(stage["artifacts"]) for stage in export["stages"])
    print(f"\nWrote {out_path} ({total_artifacts} artifacts across {len(export['stages'])} stages)")


if __name__ == "__main__":
    main()

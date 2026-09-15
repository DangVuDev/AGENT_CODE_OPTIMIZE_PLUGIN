"""Local web UI for driving and watching a real optimizer run.

    python -m uv sync --group ui        # once, if fastapi/uvicorn are missing
    python scripts/ui_server.py         # then open http://127.0.0.1:8765

Serves `ui_static/index.html` plus a small JSON API over the same real
graphs `scripts/optimize.py` drives -- this is a view onto the production
handlers, never a reimplementation of them:

    GET  /api/catalog            every node in the catalogue, grouped by stage,
                                 with what it produces and whether it is
                                 runnable from here today
    POST /api/runs               start a run (returns a run_id immediately)
    GET  /api/runs/{id}/events   Server-Sent Events: one message per node as
                                 it starts/finishes, plus stage and run edges
    GET  /api/runs/{id}          full snapshot (reconnect/refresh path)
    GET  /api/runs/{id}/artifacts/{artifact_id}   one artifact's real content

Per-node events come from wrapping each `RegisteredNode.handler` in an
emitting decorator at registration time (`_instrumented`) -- the handlers
themselves, and everything they seal, are untouched production code.

Honest scope, mirroring `docs/project-blueprint/10-current-gap-analysis.md`:
Lane A (A1-A3, C0) and the shared workflow's S01/S02 really run from here.
S03-S07 really edit the target repository, so they run only when the request
opts in explicitly. Lane B (B1/B2) has no trigger surface and no query
adapters yet, so the UI renders its topology but will not pretend to run it.
"""

from __future__ import annotations

import argparse
import json
import queue
import sys
import threading
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _lane1_common import build_control_plane, select_model_provider
from optimize import build_payload, seed_payload

from production_optimizer.adapters.production import DeferredModelCallError
from production_optimizer.adapters.production.local_worker_broker import (
    LocalWorkerBroker,
)
from production_optimizer.application import (
    NodePorts,
    NodeRuntime,
    build_a1_registrations,
    build_a2_registrations,
    build_a3_registrations,
    build_c0_registrations,
    build_s01_registrations,
    build_s02_registrations,
)
from production_optimizer.application.a2_worker_capabilities import (
    build_local_command_capabilities,
)
from production_optimizer.application.node_runtime import RegisteredNode
from production_optimizer.contracts.a1 import CriterionInput
from production_optimizer.contracts.platform import ActorContext
from production_optimizer.orchestration.catalog import (
    A1_NODE_IDS,
    A2_NODE_IDS,
    A3_NODE_IDS,
    B1_NODE_IDS,
    B2_NODE_IDS,
    C0_NODE_IDS,
    S01_NODE_IDS,
    S02_NODE_IDS,
    S03_NODE_IDS,
    S04_NODE_IDS,
    S05_NODE_IDS,
    S06_NODE_IDS,
    S07_NODE_IDS,
)
from production_optimizer.orchestration.node_manifest import NODE_MANIFEST
from production_optimizer.orchestration.subgraphs import (
    build_a1_graph,
    build_a2_graph,
    build_a3_graph,
    build_c0_graph,
    build_s01_graph,
    build_s02_graph,
)

_TENANT_ID = "TENANT-UI"
_STATIC = Path(__file__).resolve().parent / "ui_static"

# (stage, title, node ids, lane, runnable-from-here, why-not)
_STAGES: tuple[tuple[str, str, tuple[str, ...], str, bool, str], ...] = (
    ("A1", "Requirement Intake", A1_NODE_IDS, "A", True, ""),
    ("A2", "Real Baseline", A2_NODE_IDS, "A", True, ""),
    ("A3", "Grounded Solutions", A3_NODE_IDS, "A", True, ""),
    ("C0", "Convergence", C0_NODE_IDS, "shared", True, ""),
    ("S01", "Rank & Select", S01_NODE_IDS, "shared", True, ""),
    ("S02", "Plan & Task List", S02_NODE_IDS, "shared", True, ""),
    ("S03", "Implement", S03_NODE_IDS, "shared", False, "edits the target repository"),
    ("S04", "Verify", S04_NODE_IDS, "shared", False, "runs after a real S03 edit"),
    ("S05", "Remeasure", S05_NODE_IDS, "shared", False, "runs after a real S03 edit"),
    ("S06", "Policy Decision", S06_NODE_IDS, "shared", False, "runs after a real S03 edit"),
    ("S07", "Report", S07_NODE_IDS, "shared", False, "runs after a real KEEP decision"),
    ("B1", "Automatic Discovery", B1_NODE_IDS, "B", False, "no trigger surface or query adapters"),
    ("B2", "Automatic Proposal", B2_NODE_IDS, "B", False, "no trigger surface yet"),
)


@dataclass
class Run:
    run_id: str
    request: dict[str, Any]
    started_at: str
    status: str = "running"
    error: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    artifacts: dict[str, dict[str, Any]] = field(default_factory=dict)
    listeners: list[queue.Queue[dict[str, Any]]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def emit(self, event: dict[str, Any]) -> None:
        event = {**event, "at": datetime.now(UTC).isoformat()}
        with self.lock:
            self.events.append(event)
            listeners = list(self.listeners)
        for listener in listeners:
            listener.put(event)


_RUNS: dict[str, Run] = {}


def _instrumented(
    registrations: dict[str, RegisteredNode], run: Run
) -> dict[str, RegisteredNode]:
    """Wrap every handler so the UI sees each node start and finish.

    The wrapper only observes: it forwards the exact `NodeExecution` the real
    handler returned (or re-raises its exception unchanged), so a run driven
    from the UI is byte-for-byte the run `scripts/optimize.py` would drive.
    """

    def wrap(node_id: str, registered: RegisteredNode) -> RegisteredNode:
        def handler(state: Any, ports: Any, /) -> Any:
            run.emit({"type": "node_start", "node_id": node_id})
            try:
                execution = registered.handler(state, ports)
            except Exception as exc:
                run.emit(
                    {
                        "type": "node_error",
                        "node_id": node_id,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                raise
            run.emit(
                {
                    "type": "node_done",
                    "node_id": node_id,
                    "route": getattr(execution.route, "value", str(execution.route)),
                }
            )
            return execution

        return RegisteredNode(spec=registered.spec, handler=handler)

    return {node_id: wrap(node_id, reg) for node_id, reg in registrations.items()}


def _capture_artifacts(run: Run, state: dict[str, Any], store: Any) -> None:
    """Record every newly sealed artifact's real content, once each."""

    for ref in state.get("artifact_refs", []):
        if ref.artifact_id in run.artifacts:
            continue
        entry: dict[str, Any] = {
            "artifact_type": ref.artifact_type,
            "artifact_id": ref.artifact_id,
            "schema_version": ref.schema_version,
            "content_digest": ref.content_digest,
        }
        try:
            entry["content"] = json.loads(store.read(tenant_id=_TENANT_ID, ref=ref))
        except (OSError, KeyError, ValueError, TypeError) as exc:
            entry["read_error"] = f"{type(exc).__name__}: {exc}"
        run.artifacts[ref.artifact_id] = entry
        run.emit(
            {
                "type": "artifact",
                "artifact_id": ref.artifact_id,
                "artifact_type": ref.artifact_type,
                "content_digest": ref.content_digest,
            }
        )


def _execute(run: Run, body: dict[str, Any]) -> None:
    """Drive the real graphs, stage by stage, emitting as it goes."""

    control = build_control_plane(service_name="production-optimizer-ui")
    store = control.artifacts
    args = argparse.Namespace(
        repo_path=Path(body["repo_path"]),
        feature_id=body["feature_id"],
        metric=body["metric"],
        direction=body.get("direction", "minimize"),
        target=float(body["target"]),
        unit=body["unit"],
        command_id=body.get("command_id") or None,
        workload_id=body.get("workload_id") or None,
        environment_id=body.get("environment_id") or "local-dev",
        case_id=body.get("case_id") or f"OPT-UI-{run.run_id[:8]}",
        actor_id=body.get("actor_id") or "ui-user",
        guardrail_metric_id=body.get("guardrail_metric_id") or None,
        maximum_worker_seconds=body.get("maximum_worker_seconds") or None,
        deadline_seconds=None,
        execution_profile=body.get("execution_profile") or "legacy_discovery",
        compose_file=body.get("compose_file") or None,
        eval_id=None,
        eval_service=body.get("eval_service") or None,
        eval_command=(body.get("eval_command") or "").split() or None,
        eval_working_dir=None,
        eval_metrics=body.get("eval_metrics") or None,
        eval_repetitions=None,
        eval_warmup=None,
        eval_timeout=300,
        application_services=body.get("application_services") or None,
    )

    payload = build_payload(args)
    declared = body.get("criteria") or []
    if declared:
        # More than one weighted criterion is the general case (BR-A1-002,
        # and S01.40 ranks by each one's weight) -- `build_payload` only
        # knows the CLI's single-criterion shorthand, so attach the full list
        # here and let A1.61 seal every one of them.
        payload = payload.model_copy(
            update={"criteria": [CriterionInput.model_validate(c) for c in declared]}
        )
    payload_ref = seed_payload(store, args.case_id, payload)
    run.emit({"type": "run_start", "case_id": args.case_id})

    base = {
        "artifacts": store,
        "intents": control.intents,
        "policy": control.policy,
        "telemetry": control.telemetry,
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
    _capture_artifacts(run, state, store)

    def stage_done(stage: str, ok: bool, detail: str = "") -> None:
        run.emit({"type": "stage_done", "stage": stage, "ok": ok, "detail": detail})

    # --- A1 -----------------------------------------------------------
    runtime = NodeRuntime(_instrumented(build_a1_registrations(), run), ports=NodePorts(**base))
    state = build_a1_graph(runtime).invoke(state)
    _capture_artifacts(run, state, store)
    pending = state.get("pending_interrupt")
    if pending is not None:
        stage_done("A1", False, f"halted at {pending.stage} for {pending.allowed_decisions}")
        run.status = "halted"
        run.emit({"type": "run_done", "status": "halted"})
        return
    if state.get("request_ref") is None:
        stage_done("A1", False, "no OptimizationRequest sealed")
        run.status = "stopped"
        run.emit({"type": "run_done", "status": "stopped"})
        return
    stage_done("A1", True)

    # --- A2 -----------------------------------------------------------
    broker = LocalWorkerBroker(
        capabilities=build_local_command_capabilities(store, tenant_id=_TENANT_ID)
    )
    try:
        runtime = NodeRuntime(
            _instrumented(build_a2_registrations(), run),
            ports=NodePorts(**base, workers=broker),
        )
        state = build_a2_graph(runtime).invoke(state)
    finally:
        broker.close()
    _capture_artifacts(run, state, store)
    if not any(r.artifact_type == "BaselineSnapshot" for r in state.get("artifact_refs", [])):
        stage_done("A2", False, "no BaselineSnapshot sealed")
        run.status = "stopped"
        run.emit({"type": "run_done", "status": "stopped"})
        return
    stage_done("A2", True)

    # --- A3 -----------------------------------------------------------
    provider, model_id = select_model_provider(
        tenant_id=_TENANT_ID,
        thread_id=f"THREAD-{args.case_id}",
        deferrals=control.model_deferrals,
    )
    run.emit({"type": "model", "provider": type(provider).__name__, "model_id": model_id})
    runtime = NodeRuntime(
        _instrumented(build_a3_registrations(), run),
        ports=NodePorts(**base, model=provider, model_id=model_id),
    )
    state = build_a3_graph(runtime, checkpointer=control.checkpointer).invoke(
        state, config={"configurable": {"thread_id": f"THREAD-{args.case_id}"}}
    )
    _capture_artifacts(run, state, store)
    if not any(r.artifact_type == "SolutionPortfolio" for r in state.get("artifact_refs", [])):
        stage_done("A3", False, "no SolutionPortfolio sealed")
        run.status = "stopped"
        run.emit({"type": "run_done", "status": "stopped"})
        return
    stage_done("A3", True)

    # --- C0 -----------------------------------------------------------
    runtime = NodeRuntime(_instrumented(build_c0_registrations(), run), ports=NodePorts(**base))
    state = build_c0_graph(runtime).invoke(state)
    _capture_artifacts(run, state, store)
    if not any(r.artifact_type == "ConvergedCase" for r in state.get("artifact_refs", [])):
        stage_done("C0", False, "case did not converge")
        run.status = "stopped"
        run.emit({"type": "run_done", "status": "stopped"})
        return
    stage_done("C0", True)

    if not body.get("run_shared_workflow"):
        run.status = "done"
        run.emit({"type": "run_done", "status": "done"})
        return

    # --- S01 / S02 ----------------------------------------------------
    shared_ports = NodePorts(**base, model=provider, model_id=model_id)
    runtime = NodeRuntime(_instrumented(build_s01_registrations(), run), ports=shared_ports)
    state = build_s01_graph(runtime).invoke(state)
    _capture_artifacts(run, state, store)
    if not any(r.artifact_type == "SelectedSolution" for r in state.get("artifact_refs", [])):
        stage_done("S01", False, "no SelectedSolution sealed")
        run.status = "stopped"
        run.emit({"type": "run_done", "status": "stopped"})
        return
    stage_done("S01", True)

    runtime = NodeRuntime(_instrumented(build_s02_registrations(), run), ports=shared_ports)
    state = build_s02_graph(runtime, checkpointer=control.checkpointer).invoke(
        state, config={"configurable": {"thread_id": f"THREAD-{args.case_id}"}}
    )
    _capture_artifacts(run, state, store)
    sealed_plan = any(r.artifact_type == "ExecutionPlan" for r in state.get("artifact_refs", []))
    stage_done("S02", sealed_plan, "" if sealed_plan else "plan quality gate rejected the draft")
    run.status = "done"
    run.emit({"type": "run_done", "status": "done"})


def _run_thread(run: Run, body: dict[str, Any]) -> None:
    try:
        _execute(run, body)
    except DeferredModelCallError as exc:
        run.status = "deferred"
        run.error = str(exc)
        run.emit(
            {
                "type": "run_deferred",
                "error": run.error,
                "deferral_id": exc.deferral_id,
                "retry_after_seconds": exc.retry_after_seconds,
            }
        )
    except Exception as exc:
        run.status = "error"
        run.error = f"{type(exc).__name__}: {exc}"
        run.emit({"type": "run_error", "error": run.error, "trace": traceback.format_exc()})
    finally:
        run.emit({"type": "eof"})


def build_app() -> Any:
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import FileResponse, JSONResponse
    from sse_starlette.sse import EventSourceResponse

    app = FastAPI(title="Optimizer Pipeline UI")

    @app.get("/")
    def index() -> Any:
        return FileResponse(_STATIC / "index.html")

    @app.get("/api/browse")
    def browse(path: str = "") -> Any:
        """List sub-directories so the UI can pick a repository by clicking.

        A browser file input cannot hand back a real filesystem path, and
        this server is the thing that will actually open the repository, so
        it is the right side to enumerate from. Each entry is flagged
        `is_repo` when it looks like a project root, which is what the picker
        highlights.
        """

        target = Path(path).expanduser() if path else Path.cwd()
        try:
            target = target.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise HTTPException(400, f"cannot resolve path: {exc}") from exc
        if not target.is_dir():
            raise HTTPException(400, f"not a directory: {target}")

        markers = ("pyproject.toml", "go.mod", "package.json", "Cargo.toml", "pom.xml", ".git")
        entries: list[dict[str, Any]] = []
        try:
            for child in sorted(target.iterdir(), key=lambda p: p.name.lower()):
                if not child.is_dir() or (child.name.startswith(".") and child.name != ".git"):
                    continue
                entries.append(
                    {
                        "name": child.name,
                        "path": str(child),
                        "is_repo": any((child / marker).exists() for marker in markers),
                    }
                )
        except PermissionError as exc:
            raise HTTPException(403, f"cannot read directory: {exc}") from exc

        return {
            "path": str(target),
            "parent": str(target.parent) if target.parent != target else None,
            "is_repo": any((target / marker).exists() for marker in markers),
            "entries": entries,
        }

    @app.get("/api/catalog")
    def catalog() -> Any:
        stages = []
        for stage, title, node_ids, lane, runnable, reason in _STAGES:
            stages.append(
                {
                    "stage": stage,
                    "title": title,
                    "lane": lane,
                    "runnable": runnable,
                    "not_runnable_reason": reason,
                    "nodes": [
                        {
                            "node_id": node_id,
                            "description": (
                                NODE_MANIFEST[node_id].description
                                if node_id in NODE_MANIFEST
                                else ""
                            ),
                            "produces": (
                                list(NODE_MANIFEST[node_id].produces)
                                if node_id in NODE_MANIFEST
                                else []
                            ),
                        }
                        for node_id in node_ids
                    ],
                }
            )
        total = sum(len(s["nodes"]) for s in stages)
        return {"stages": stages, "node_count": total}

    @app.post("/api/runs")
    def start_run(body: dict[str, Any]) -> Any:
        missing = [
            key
            for key in ("repo_path", "feature_id", "metric", "target", "unit")
            if not body.get(key) and body.get(key) != 0
        ]
        if missing:
            raise HTTPException(400, f"missing required fields: {', '.join(missing)}")
        if not Path(body["repo_path"]).is_dir():
            raise HTTPException(400, f"repo_path is not a directory: {body['repo_path']}")

        run = Run(
            run_id=uuid.uuid4().hex,
            request=body,
            started_at=datetime.now(UTC).isoformat(),
        )
        _RUNS[run.run_id] = run
        threading.Thread(target=_run_thread, args=(run, body), daemon=True).start()
        return {"run_id": run.run_id}

    @app.get("/api/runs/{run_id}")
    def snapshot(run_id: str) -> Any:
        run = _RUNS.get(run_id)
        if run is None:
            raise HTTPException(404, "unknown run")
        return {
            "run_id": run.run_id,
            "status": run.status,
            "error": run.error,
            "started_at": run.started_at,
            "request": run.request,
            "events": run.events,
            "artifacts": [
                {k: v for k, v in entry.items() if k != "content"}
                for entry in run.artifacts.values()
            ],
        }

    @app.get("/api/runs/{run_id}/artifacts/{artifact_id}")
    def artifact(run_id: str, artifact_id: str) -> Any:
        run = _RUNS.get(run_id)
        if run is None:
            raise HTTPException(404, "unknown run")
        entry = run.artifacts.get(artifact_id)
        if entry is None:
            raise HTTPException(404, "unknown artifact")
        return JSONResponse(entry)

    @app.get("/api/runs/{run_id}/events")
    async def events(run_id: str) -> Any:
        run = _RUNS.get(run_id)
        if run is None:
            raise HTTPException(404, "unknown run")

        listener: queue.Queue[dict[str, Any]] = queue.Queue()
        with run.lock:
            backlog = list(run.events)
            run.listeners.append(listener)

        async def stream() -> Any:
            import asyncio

            try:
                for event in backlog:
                    yield {"data": json.dumps(event)}
                while True:
                    try:
                        event = listener.get_nowait()
                    except queue.Empty:
                        await asyncio.sleep(0.12)
                        continue
                    yield {"data": json.dumps(event)}
                    if event.get("type") == "eof":
                        return
            finally:
                with run.lock:
                    if listener in run.listeners:
                        run.listeners.remove(listener)

        return EventSourceResponse(stream())

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    import uvicorn

    print(f"Pipeline UI on http://{args.host}:{args.port}")
    uvicorn.run(build_app(), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()

"""Run the durable model-call deferral scheduler.

This worker resumes checkpointed A3/S02 graph invocations whose model call was
deferred because every approved provider was temporarily unavailable.

Example:

    python scripts/run_model_deferral_scheduler.py --tenant-id TENANT-CLI --once
"""

from __future__ import annotations

import argparse
import socket
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _lane1_common import build_control_plane, select_model_provider

from production_optimizer.application import (
    ModelDeferralScheduler,
    NodePorts,
    build_a3_runtime,
    build_s02_runtime,
)
from production_optimizer.contracts.platform import DeferredModelCallRecord
from production_optimizer.orchestration.subgraphs import build_a3_graph, build_s02_graph


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Resume deferred model calls from Postgres.")
    parser.add_argument("--tenant-id", default="TENANT-CLI")
    parser.add_argument("--lease-owner", default=f"model-deferral-scheduler:{socket.gethostname()}")
    parser.add_argument("--lease-seconds", type=int, default=300)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--once", action="store_true", help="Run one claim/resume cycle and exit.")
    parser.add_argument("--interval-seconds", type=float, default=15.0)
    return parser.parse_args(argv)


def _print_control_plane(control: Any) -> None:
    state = "durable" if control.durable else "NOT durable"
    print(f"[control-plane] {state}")
    for note in control.notes:
        print(f"[control-plane]   {note}")


def main() -> None:
    args = parse_args(sys.argv[1:])
    control = build_control_plane(service_name="production-optimizer-model-deferral-scheduler")
    _print_control_plane(control)
    if control.checkpointer is None:
        raise SystemExit("model deferral scheduler requires a durable LangGraph checkpointer")

    def _ports(record: DeferredModelCallRecord) -> NodePorts:
        provider, model_id = select_model_provider(
            tenant_id=args.tenant_id,
            thread_id=record.thread_id,
            deferrals=control.model_deferrals,
        )
        return NodePorts(
            artifacts=control.artifacts,
            intents=control.intents,
            policy=control.policy,
            telemetry=control.telemetry,
            model=provider,
            model_id=model_id,
        )

    def a3_graph(record: DeferredModelCallRecord) -> Any:
        ports = _ports(record)
        return build_a3_graph(build_a3_runtime(ports=ports), checkpointer=control.checkpointer)

    def s02_graph(record: DeferredModelCallRecord) -> Any:
        ports = _ports(record)
        return build_s02_graph(build_s02_runtime(ports=ports), checkpointer=control.checkpointer)

    scheduler = ModelDeferralScheduler(
        deferrals=control.model_deferrals,
        graph_factories={"A3": a3_graph, "S02": s02_graph},
        tenant_id=args.tenant_id,
        lease_owner=args.lease_owner,
        lease_seconds=args.lease_seconds,
    )

    while True:
        results = scheduler.run_once(limit=args.limit)
        for result in results:
            print(
                f"[model-deferral] {result.status}: {result.deferral_id} "
                f"node={result.node_id}" + (f" detail={result.detail}" if result.detail else "")
            )
        if args.once:
            break
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    main()

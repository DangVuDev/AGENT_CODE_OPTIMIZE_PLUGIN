from __future__ import annotations

from production_optimizer.application.a2_handlers import A2_BLOCKED_NODES
from production_optimizer.orchestration.catalog import ALL_BUSINESS_NODE_IDS
from production_optimizer.orchestration.node_manifest import (
    NODE_MANIFEST,
    manifest_for,
    status_counts,
)


def test_manifest_covers_every_catalog_node_exactly_once() -> None:
    assert frozenset(NODE_MANIFEST) == ALL_BUSINESS_NODE_IDS
    assert len(NODE_MANIFEST) == 99


def test_manifest_for_unknown_node_raises() -> None:
    try:
        manifest_for("Z9.99")
    except KeyError as exc:
        assert "Z9.99" in str(exc)
    else:
        raise AssertionError("expected KeyError for unknown node id")


def test_status_counts_sum_to_total_node_count() -> None:
    counts = status_counts()
    assert sum(counts.values()) == len(NODE_MANIFEST)


def test_blocked_nodes_match_a2_handler_module() -> None:
    blocked = {
        node_id for node_id, entry in NODE_MANIFEST.items() if entry.status == "blocked"
    }
    assert blocked == frozenset(A2_BLOCKED_NODES)
    for node_id in blocked:
        assert manifest_for(node_id).blocked_reason == A2_BLOCKED_NODES[node_id]


def test_implemented_nodes_declare_at_least_one_produced_artifact() -> None:
    for node_id, entry in NODE_MANIFEST.items():
        if entry.status == "implemented":
            assert entry.produces, f"{node_id} is implemented but declares no produced artifact"


def test_blocked_nodes_declare_no_produced_artifact() -> None:
    for node_id, entry in NODE_MANIFEST.items():
        if entry.status == "blocked":
            assert entry.produces == (), f"{node_id} is blocked but claims a produced artifact"

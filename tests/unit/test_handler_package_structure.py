from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from production_optimizer.application.a1_handlers import NODE_HANDLERS as A1_HANDLERS
from production_optimizer.application.a2_handlers import NODE_HANDLERS as A2_HANDLERS
from production_optimizer.application.a3_handlers import NODE_HANDLERS as A3_HANDLERS
from production_optimizer.application.b1_handlers import NODE_HANDLERS as B1_HANDLERS
from production_optimizer.application.b2_handlers import NODE_HANDLERS as B2_HANDLERS
from production_optimizer.application.c0_handlers import NODE_HANDLERS as C0_HANDLERS
from production_optimizer.application.s01_handlers import NODE_HANDLERS as S01_HANDLERS
from production_optimizer.application.s02_handlers import NODE_HANDLERS as S02_HANDLERS
from production_optimizer.orchestration.catalog import (
    A1_NODE_IDS,
    A2_NODE_IDS,
    A3_NODE_IDS,
    ALL_BUSINESS_NODE_IDS,
    B1_NODE_IDS,
    B2_NODE_IDS,
    C0_NODE_IDS,
    S01_NODE_IDS,
    S02_NODE_IDS,
)

STAGE_HANDLERS: tuple[tuple[tuple[str, ...], Mapping[str, Any]], ...] = (
    (A1_NODE_IDS, A1_HANDLERS),
    (A2_NODE_IDS, A2_HANDLERS),
    (A3_NODE_IDS, A3_HANDLERS),
    (B1_NODE_IDS, B1_HANDLERS),
    (B2_NODE_IDS, B2_HANDLERS),
    (C0_NODE_IDS, C0_HANDLERS),
)

# S02 is not (yet) part of `ALL_BUSINESS_NODE_IDS` (that set is Lane A/B's 99
# business nodes; S02 is a shared-workflow stage past C0's handoff), so it is
# checked for the same one-module-per-node shape separately below rather than
# folded into `STAGE_HANDLERS`/`ALL_BUSINESS_NODE_IDS`.
SHARED_WORKFLOW_STAGE_HANDLERS: tuple[tuple[tuple[str, ...], Mapping[str, Any]], ...] = (
    (S01_NODE_IDS, S01_HANDLERS),
    (S02_NODE_IDS, S02_HANDLERS),
)


def _assert_one_descriptively_named_handler_module_per_node(
    expected_ids: tuple[str, ...], handlers: Mapping[str, Any]
) -> None:
    assert set(handlers) == set(expected_ids)
    for node_id, handler in handlers.items():
        node_token = node_id.lower().replace(".", "_")
        handler_name = cast("str", handler.__name__)
        assert f".nodes.{node_token}_" in handler.__module__
        assert handler_name.startswith(f"handle_{node_token}_")
        assert len(handler_name) > len(f"handle_{node_token}_")


def test_every_shared_workflow_stage_node_has_one_descriptively_named_handler_module() -> None:
    for expected_ids, handlers in SHARED_WORKFLOW_STAGE_HANDLERS:
        _assert_one_descriptively_named_handler_module_per_node(expected_ids, handlers)


def test_every_business_node_has_one_descriptively_named_handler_module() -> None:
    registered_ids = {node_id for _, handlers in STAGE_HANDLERS for node_id in handlers}
    assert registered_ids == ALL_BUSINESS_NODE_IDS

    for expected_ids, handlers in STAGE_HANDLERS:
        _assert_one_descriptively_named_handler_module_per_node(expected_ids, handlers)

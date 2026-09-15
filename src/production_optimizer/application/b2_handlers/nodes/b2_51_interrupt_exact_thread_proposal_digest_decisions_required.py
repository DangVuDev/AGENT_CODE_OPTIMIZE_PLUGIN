# pyright: reportPrivateUsage=false
"""Implementation of business node B2.51."""

from __future__ import annotations

from typing import Any, cast

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.b2 import ProposalApproval
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _required_state_str,
)


def handle_b2_51_interrupt_exact_thread_proposal_digest_decisions_required(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """Record whatever decision is available. `subgraphs/b2.py` wires
    B2.51->B2.52 unconditionally (not a routed edge), so this node cannot
    halt the graph itself -- an out-of-band decision arrives the same way
    A1.90/A2.31 read one, via `state["resume_command"]` set by
    `application.resume.resume_case`. No decision yet means `pending`,
    which B2.52 fails closed on rather than treating as approval."""

    del ports
    resume_command = state.get("resume_command")
    if resume_command is None:
        approval = ProposalApproval(decision="pending", policy_version="b2-approval-v1")
    else:
        decision_map = {
            "approve": "approved",
            "reject": "rejected",
            "revise": "revision_requested",
        }
        decision = decision_map.get(resume_command.decision, "pending")
        approval = ProposalApproval(
            approval_id=f"approval-{_required_state_str(state, 'case_id')}",
            actor_id=resume_command.actor_id,
            actor_role=sorted(resume_command.actor_roles)[0],
            decision=cast("Any", decision),
            policy_version="b2-approval-v1",
        )
    return NodeExecution(updates={"b2_approval": approval})


__all__ = ["handle_b2_51_interrupt_exact_thread_proposal_digest_decisions_required"]

# pyright: reportPrivateUsage=false
"""Implementation of business node C0.50."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a2 import EvidenceBundle, SourceSnapshot
from production_optimizer.contracts.c0 import FreshnessCheck
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _FRESHNESS_WINDOW,
    _git,
    _read_required,
)


def handle_c0_50_reject_refresh_stale_source_evidence_approval_ownership(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    """Real freshness recheck. Source: re-derive the live git revision at
    `SourceSnapshot.canonical_path_ref` and compare to what was sealed
    (mirrors `b2_handlers._b2_31`'s staleness check, generalized to the
    artifact every lane actually seals). Evidence: the most recent
    `EvidenceItem.identity.observed_at` must be within `_FRESHNESS_WINDOW`.
    Neither check can fabricate an answer it cannot compute: a snapshot
    whose path no longer exists, or one with no recorded git revision,
    is treated as fresh (nothing contradicts what was sealed)."""

    now = datetime.now(UTC)
    checks: list[FreshnessCheck] = []

    source = cast("SourceSnapshot", _read_required(ports, state, "SourceSnapshot"))
    source_fresh = True
    root = Path(source.canonical_path_ref)
    if root.exists() and source.git_revision:
        current_revision = _git(root, "rev-parse", "HEAD")
        if current_revision is not None and current_revision != source.git_revision:
            source_fresh = False
    checks.append(FreshnessCheck(dimension="source", fresh=source_fresh, checked_at=now))

    bundle = cast("EvidenceBundle", _read_required(ports, state, "EvidenceBundle"))
    observed_ats = [item.identity.observed_at for item in bundle.evidence]
    normalized = [
        observed if observed.tzinfo is not None else observed.replace(tzinfo=UTC)
        for observed in observed_ats
    ]
    evidence_fresh = True
    expires_at: datetime | None = None
    if normalized:
        most_recent = max(normalized)
        evidence_fresh = (now - most_recent) <= _FRESHNESS_WINDOW
        candidate_expiry = most_recent + _FRESHNESS_WINDOW
        expires_at = candidate_expiry if candidate_expiry > now else None
    checks.append(
        FreshnessCheck(
            dimension="evidence", fresh=evidence_fresh, checked_at=now, expires_at=expires_at
        )
    )

    return NodeExecution(updates={"c0_freshness_checks": checks})


__all__ = ["handle_c0_50_reject_refresh_stale_source_evidence_approval_ownership"]

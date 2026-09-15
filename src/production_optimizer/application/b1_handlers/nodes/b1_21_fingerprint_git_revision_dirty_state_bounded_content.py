# pyright: reportPrivateUsage=false
"""Implementation of business node B1.21."""

from __future__ import annotations

from pathlib import Path

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.b1 import ObservedSourceIdentity, RegisteredSourceSet
from production_optimizer.contracts.canonical import canonical_json, sha256_digest
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _ZERO_DIGEST,
    _base_envelope,
    _git,
    _put_envelope,
    _read_model,
    _require_ref,
    _seal,
)


def handle_b1_21_fingerprint_git_revision_dirty_state_bounded_content(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    registry = _read_model(
        ports, state, _require_ref(state, "RegisteredSourceSet"), RegisteredSourceSet
    )
    source = registry.sources[0] if registry.sources else None

    if source is None:
        identity = _seal(
            ObservedSourceIdentity(
                **_base_envelope(state, "ObservedSourceIdentity"),
                source_id="none",
                repository_id="none",
                git_revision=None,
                content_fingerprint=_ZERO_DIGEST,
            )
        )
    else:
        root = Path(source.local_path)
        git_root = _git(root, "rev-parse", "--show-toplevel") if root.exists() else None
        exact_git_root = bool(git_root) and Path(git_root).resolve() == root.resolve()
        git_revision = _git(root, "rev-parse", "HEAD") if exact_git_root else None
        fingerprint_content = canonical_json(
            {"repository_id": source.repository_id, "git_revision": git_revision}
        )
        identity = _seal(
            ObservedSourceIdentity(
                **_base_envelope(state, "ObservedSourceIdentity"),
                source_id=source.source_id,
                repository_id=source.repository_id,
                git_revision=git_revision,
                content_fingerprint=sha256_digest(fingerprint_content),
            )
        )
    ref = _put_envelope(ports, state, identity, node_id="B1.21")
    return NodeExecution(updates={"artifact_refs": [ref]})


__all__ = ["handle_b1_21_fingerprint_git_revision_dirty_state_bounded_content"]

# pyright: reportPrivateUsage=false
"""Implementation of business node A1.30."""

from __future__ import annotations

from pathlib import Path

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a1 import LocalSourceIdentity, ManualCasePayload
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _base_envelope,
    _git,
    _put_envelope,
    _read_model,
    _repository_id,
    _require_ref,
    _seal,
)


def handle_a1_30_resolve_allowed_root_canonicalize_path_block_traversal(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    payload = _read_model(ports, state, _require_ref(state, "ManualCasePayload"), ManualCasePayload)
    allowed_root = Path(payload.allowed_root).resolve()
    local_path = Path(payload.local_path).resolve()
    if not allowed_root.is_dir():
        raise ValueError(f"allowed root is not a directory: {allowed_root}")
    if not local_path.is_dir():
        raise ValueError(f"local source path is not a directory: {local_path}")
    if not local_path.is_relative_to(allowed_root):
        raise ValueError("local source path escapes the allowed root")

    # Git walks parent directories by default. A temporary or nested source
    # must never inherit the identity/revision of an unrelated ancestor repo.
    discovered_git_root = _git(local_path, "rev-parse", "--show-toplevel")
    exact_git_root = bool(discovered_git_root) and Path(discovered_git_root).resolve() == local_path
    git_revision = _git(local_path, "rev-parse", "HEAD") if exact_git_root else ""
    status = _git(local_path, "status", "--porcelain=v1") if exact_git_root else ""
    dirty_lines = [line for line in status.splitlines() if line.strip()]
    source = _seal(
        LocalSourceIdentity(
            **_base_envelope(state, "LocalSourceIdentity"),
            repository_id=_repository_id(local_path),
            # A real filesystem path, not a hash: `SourceReference` (A1.95)
            # carries this straight through to `OptimizationRequest.source`,
            # and A2.10/A2.20 reconstruct the canonical path as
            # `Path(allowed_root_id) / relative_path` -- that only works if
            # this is the real allowed root, not an opaque identifier.
            # `repository_id` above stays hashed since nothing needs it as
            # a path.
            allowed_root_id=str(allowed_root),
            canonical_path=str(local_path),
            relative_path=str(local_path.relative_to(allowed_root)) or ".",
            git_revision=git_revision or None,
            dirty=bool(dirty_lines),
            untracked_count=sum(1 for line in dirty_lines if line.startswith("??")),
        )
    )
    ref = _put_envelope(ports, state, source, node_id="A1.30")
    return NodeExecution(updates={"artifact_refs": [ref]})


__all__ = ["handle_a1_30_resolve_allowed_root_canonicalize_path_block_traversal"]

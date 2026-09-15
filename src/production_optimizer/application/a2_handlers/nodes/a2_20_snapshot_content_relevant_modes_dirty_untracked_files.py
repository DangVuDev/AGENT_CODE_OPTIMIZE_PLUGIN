# pyright: reportPrivateUsage=false
"""Implementation of business node A2.20."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a1 import OptimizationRequest
from production_optimizer.contracts.a2 import A2IntakeDecision, FileIdentity, SourceSnapshot
from production_optimizer.contracts.canonical import sha256_digest
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _base_envelope,
    _build_source_archive,
    _capture_stable_source,
    _git,
    _materialize_immutable_workspace,
    _materialize_source,
    _put_envelope,
    _read_model,
    _require_ref,
    _required_state_str,
    _seal,
)


def handle_a2_20_snapshot_content_relevant_modes_dirty_untracked_files(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    request_ref = _require_ref(state, "OptimizationRequest")
    request = _read_model(ports, state, request_ref, OptimizationRequest)
    intake = _read_model(ports, state, _require_ref(state, "A2IntakeDecision"), A2IntakeDecision)
    if not intake.verified:
        raise ValueError("A2.20 cannot snapshot an unverified A2 handoff")

    source_path, source_locator, provider_revision = _materialize_source(request, ports, state)
    git_root = _git(source_path, "rev-parse", "--show-toplevel")
    exact_git_root = bool(git_root) and Path(git_root).resolve() == source_path
    git_revision = _git(source_path, "rev-parse", "HEAD") if exact_git_root else ""
    resolved_revision = provider_revision or git_revision or None
    if (
        request.source.requested_revision is not None
        and resolved_revision != request.source.requested_revision
    ):
        raise ValueError(
            "materialized source revision does not match the approved requested revision"
        )
    status = _git(source_path, "status", "--porcelain=v1") if exact_git_root else ""
    dirty_lines = [line for line in status.splitlines() if line.strip()]
    dirty = bool(dirty_lines)
    captured, exclusions = _capture_stable_source(source_path)
    total_bytes = sum(len(content) for _, content, _ in captured)
    if total_bytes > request.budget.maximum_storage_bytes:
        raise ValueError("source snapshot exceeds maximum_storage_bytes")
    archive_bytes = _build_source_archive(captured)
    archive_digest = sha256_digest(archive_bytes)
    archive_ref = ports.artifacts.put_blob(
        tenant_id=_required_state_str(state, "tenant_id"),
        content=archive_bytes,
        content_digest=archive_digest,
        media_type="application/zip",
    )
    immutable_path = _materialize_immutable_workspace(captured, archive_digest)
    files = [
        FileIdentity(
            relative_path=relative,
            content_digest=sha256_digest(content),
            executable=executable,
        )
        for relative, content, executable in captured
    ]

    submodule_revisions: dict[str, str] = {}
    submodule_status = _git(source_path, "submodule", "status") if exact_git_root else ""
    for line in submodule_status.splitlines():
        if line.strip():
            parts = line.split()
            if len(parts) >= 2:
                revision = parts[0].lstrip("+-U ")
                path_name = parts[1]
                submodule_revisions[path_name] = revision

    snapshot = _seal(
        SourceSnapshot(
            **_base_envelope(state, "SourceSnapshot", parents=[request.content_digest]),
            repository_id=request.source.repository_id,
            canonical_path_ref=str(immutable_path),
            git_revision=resolved_revision,
            dirty=dirty,
            files=sorted(files, key=lambda f: f.relative_path),
            submodule_revisions=submodule_revisions,
            exclusions=sorted(exclusions),
            source_kind=request.source.source_kind,
            source_locator=source_locator,
            archive_ref=archive_ref,
            captured_at=datetime.now(UTC),
            total_bytes=total_bytes,
        )
    )
    ref = _put_envelope(ports, state, snapshot, node_id="A2.20")
    return NodeExecution(updates={"artifact_refs": [ref]})


__all__ = ["handle_a2_20_snapshot_content_relevant_modes_dirty_untracked_files"]

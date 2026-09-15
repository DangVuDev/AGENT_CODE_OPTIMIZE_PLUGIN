# pyright: reportPrivateUsage=false
"""Implementation of business node A1.50."""

from __future__ import annotations

from production_optimizer.application.node_runtime import NodeExecution, NodePorts
from production_optimizer.contracts.a1 import (
    FeatureScope,
    LocalSourceIdentity,
    ProjectProfile,
    RawRequestDraft,
)
from production_optimizer.contracts.state import OptimizationState

from ..shared import (
    _base_envelope,
    _discovered_vendor_dir_markers,
    _put_envelope,
    _read_model,
    _require_ref,
    _seal,
)


def handle_a1_50_resolve_registered_feature_first_then_evidence_based(
    state: OptimizationState, ports: NodePorts
) -> NodeExecution:
    draft = _read_model(ports, state, _require_ref(state, "RawRequestDraft"), RawRequestDraft)
    source = _read_model(
        ports,
        state,
        _require_ref(state, "LocalSourceIdentity"),
        LocalSourceIdentity,
    )
    profile = _read_model(ports, state, _require_ref(state, "ProjectProfile"), ProjectProfile)
    feature_id = draft.feature_id or "unknown-feature"
    include_paths = [source.relative_path if source.relative_path != "." else "*"]
    # `profile.generated_or_vendor_paths` holds individual *file* paths under
    # a vendor directory (e.g. "node_modules/lodash/index.js"), not bare
    # directory names -- so derive which of A1.40's known vendor markers
    # actually occur in this repo instead of misusing a file path as a
    # directory to exclude (see `A1_HONEST_ASSESSMENT.md` Problem 2.2).
    # Previously only "node_modules" was hardcoded here even when A1.40 had
    # already discovered "vendor/" or ".venv/" content too.
    exclude_paths: list[str] = [".git/**", "**/__pycache__/**"]
    discovered_markers = _discovered_vendor_dir_markers(profile.generated_or_vendor_paths)
    if discovered_markers:
        exclude_paths.extend(f"**/{marker}/**" for marker in discovered_markers)
    else:
        exclude_paths.append("node_modules/**")
    normalized_feature = "".join(
        character for character in feature_id.lower() if character.isalnum()
    )
    candidate_paths = sorted(
        path
        for path in profile.source_files
        if normalized_feature
        and normalized_feature in "".join(c for c in path.lower() if c.isalnum())
    )
    if candidate_paths:
        include_paths = candidate_paths[:100]
    confidence = 0.95 if candidate_paths else (0.8 if draft.feature_id else 0.0)
    scope = _seal(
        FeatureScope(
            **_base_envelope(
                state, "FeatureScope", parents=[draft.content_digest, profile.content_digest]
            ),
            feature_id=feature_id,
            include_paths=include_paths,
            exclude_paths=exclude_paths,
            confidence=confidence,
            rationale=(
                "scope is bounded to the authenticated repository and supported by matching "
                "project paths"
                if candidate_paths
                else "requester-declared feature is bounded to the authenticated repository; "
                "no narrower path match was proven"
            ),
            supporting_paths=candidate_paths[:50],
        )
    )
    ref = _put_envelope(ports, state, scope, node_id="A1.50")
    return NodeExecution(updates={"artifact_refs": [ref]})


__all__ = ["handle_a1_50_resolve_registered_feature_first_then_evidence_based"]

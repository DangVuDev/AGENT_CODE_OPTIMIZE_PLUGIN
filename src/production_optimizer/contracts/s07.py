from __future__ import annotations

from typing import Literal

from pydantic import Field

from .base import ContractModel
from .envelope import ArtifactEnvelope


class ChronologyEntry(ContractModel):
    """One real node execution, in catalog order with a pass number --
    honestly scoped: `contracts/state.py`'s `event_refs` channel is never
    populated by any handler in this codebase (a real, acknowledged gap, not
    hidden here), so this reconstructs from `completed_nodes`/`node_routes`
    (which are real and populated) rather than a true wall-clock event log."""

    node_id: str = Field(min_length=1)
    route: str = Field(min_length=1)
    pass_number: int = Field(ge=0)


class OutcomeEntry(ContractModel):
    """BR-07-001/BR-07-003: every criterion and every phase-repair attempt
    gets one of these, so failed or repaired work stays visible rather than
    silently dropped from the report."""

    category: Literal["completed", "repaired", "simplified", "reverted", "missing"]
    subject: str = Field(min_length=1)
    detail: str = Field(min_length=1)
    pass_number: int = Field(ge=0)


class EvidenceSummary(ContractModel):
    criterion_id: str = Field(min_length=1)
    metric_id: str = Field(min_length=1)
    baseline_mean: float
    treatment_mean: float
    absolute_change: float
    relative_change: float | None = None
    comparable: bool
    met: bool


class SourceChangeSummary(ContractModel):
    """BR-07-004 note: this milestone's S07 is only reachable via a KEEP
    outcome (see `s07_handlers` module docstring), so `rollback_executed` is
    always `False` here -- a REVERT's `RollbackReport` is a separate,
    already-sealed artifact from an earlier pass, not part of this
    summary.

    `applied_to_repository`/`applied_branch_name`/`applied_commit_sha`/
    `applied_failure_reason` are populated from S06.90's sealed
    `RepositoryApplyResult` -- a materially different thing from
    `rollback_command`/`rollback_executed` above, which describe the
    strategy's own declared rollback mechanism, not whether this KEEP was
    actually landed anywhere real."""

    phase_id: str = Field(min_length=1)
    base_revision: str = Field(min_length=1)
    changed_files: list[str] = Field(min_length=1)
    patch_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    rollback_command: str | None = None
    rollback_executed: bool
    applied_to_repository: bool
    applied_branch_name: str | None = None
    applied_commit_sha: str | None = None
    applied_failure_reason: str | None = None


class CostSummary(ContractModel):
    model_input_tokens: int = Field(ge=0)
    model_output_tokens: int = Field(ge=0)
    phase_repair_attempts: int = Field(ge=0)


class OptimizationReport(ArtifactEnvelope):
    """S07.80's sealed handoff. `json_report_digest`/`markdown_report_digest`
    point at the real published blob artifacts (BR-07's "Store JSON,
    Markdown" outputs); the structured fields here are the same facts,
    directly queryable without fetching either blob."""

    artifact_type: Literal["OptimizationReport"] = "OptimizationReport"
    schema_version: Literal["1.0"] = "1.0"
    case_outcome: Literal["KEEP"]
    phase_id: str = Field(min_length=1)
    decision_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    chronology: list[ChronologyEntry] = Field(min_length=1)
    outcomes: list[OutcomeEntry] = Field(min_length=1)
    evidence: list[EvidenceSummary] = Field(min_length=1)
    source_change: SourceChangeSummary
    cost: CostSummary
    narrative: str = Field(min_length=1)
    json_report_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    markdown_report_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    policy_version: str = Field(min_length=1)

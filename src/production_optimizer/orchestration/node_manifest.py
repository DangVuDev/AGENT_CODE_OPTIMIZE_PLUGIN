from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from .catalog import (
    A1_NODE_IDS,
    A2_NODE_IDS,
    A3_NODE_IDS,
    ALL_BUSINESS_NODE_IDS,
    B1_NODE_IDS,
    B2_NODE_IDS,
    C0_NODE_IDS,
)

NodeStatus = Literal["implemented", "blocked", "planned"]


@dataclass(frozen=True, slots=True)
class NodeManifestEntry:
    """One row of the 99-node business manifest.

    `description` is the node's purpose as stated in the delivery playbook
    (`docs/implementation/05-*`, `docs/implementation/06-*`,
    `docs/project-blueprint/shared-workflow/00-convergence.md`), not derived
    from code. `produces` names sealed `ArtifactEnvelope.artifact_type`
    values this node is responsible for; it is empty whenever the node's
    output is an intermediate/leaf model not split into its own envelope, or
    when no handler exists yet to confirm the shape — an empty tuple is not
    a claim that the node produces nothing, only that this manifest does not
    yet bind it to one. `status` mirrors what `build_*_registrations()`
    actually registers today, never what the contract layer merely supports.
    """

    node_id: str
    lane: str
    description: str
    produces: tuple[str, ...] = ()
    status: NodeStatus = "planned"
    blocked_reason: str | None = None


def _implemented(
    lane: str, rows: tuple[tuple[str, str, tuple[str, ...]], ...]
) -> dict[str, NodeManifestEntry]:
    return {
        node_id: NodeManifestEntry(
            node_id=node_id,
            lane=lane,
            description=description,
            produces=produces,
            status="implemented",
        )
        for node_id, description, produces in rows
    }


def _planned(
    lane: str, rows: tuple[tuple[str, str, tuple[str, ...]], ...]
) -> dict[str, NodeManifestEntry]:
    return {
        node_id: NodeManifestEntry(
            node_id=node_id,
            lane=lane,
            description=description,
            produces=produces,
            status="planned",
        )
        for node_id, description, produces in rows
    }


# A "blocked" status entry (`NodeManifestEntry(status="blocked", blocked_reason=...)`)
# is written by hand wherever a lane still has one, mirroring how
# `application.a2_handlers.A2_BLOCKED_NODES` used to gate A2.60-A2.95 before
# all 18 A2 nodes were implemented. No lane currently has one.

# --- A1: request intake (14 nodes, all implemented — application/a1_handlers.py) ---

_A1_ROWS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "A1.10",
        "Authenticate command; validate tenant/case/thread/idempotency; limit payload; "
        "preserve original input as artifact",
        ("IntakeEnvelope",),
    ),
    (
        "A1.20",
        "Structured input uses schema path; raw text uses approved extractor; "
        "preserve unknowns and conflicts",
        ("RawRequestDraft",),
    ),
    (
        "A1.30",
        "Resolve allowed root; canonicalize path; block traversal/symlink escape; "
        "detect Git, HEAD, dirty/untracked/submodules",
        ("LocalSourceIdentity",),
    ),
    (
        "A1.40",
        "Bounded read-only discovery of languages, manifests, tools, tests, size, "
        "vendor/generated paths and service boundaries",
        ("ProjectProfile",),
    ),
    (
        "A1.50",
        "Resolve registered feature first, then evidence-based paths/symbols; "
        "retain alternatives and confidence",
        ("FeatureScope",),
    ),
    (
        "A1.60",
        "Separate objective from requester remedy; retain remedy only as hypothesis",
        ("CanonicalObjective",),
    ),
    (
        "A1.61",
        "Define at least one criterion with metric, direction, target, unit and weight",
        ("CriterionSet",),
    ),
    (
        "A1.62",
        "Define correctness plus relevant security, compatibility, cost/resource/quality "
        "guardrails",
        ("GuardrailSet",),
    ),
    (
        "A1.63",
        "Apply tenant quota and policy; freeze deadline, worker/model/storage budgets and "
        "allowed analyzers",
        ("ExecutionBudgetArtifact",),
    ),
    (
        "A1.70",
        "Resolve repository-owned workload/scenario, dataset, environment, warmups, "
        "repetitions, concurrency and cache",
        ("WorkloadIdentity",),
    ),
    (
        "A1.71",
        "Map every criterion/guardrail to acceptable source types and minimum samples",
        ("EvidenceRequirementSet",),
    ),
    (
        "A1.80",
        "Deterministically check omissions, conflicts, units, duplicate IDs, impossible "
        "budget and overlapping scope; critic is advisory",
        ("A1QualityReport",),
    ),
    (
        "A1.90",
        "Evaluate scope/data/risk/RBAC policy; create digest-bound interrupt where required; "
        "validate resume actor, role, expiry and policy",
        ("A1ApprovalDecision",),
    ),
    (
        "A1.95",
        "Canonicalize, calculate digest, sign metadata, create-only persist and emit "
        "handoff event",
        ("OptimizationRequest",),
    ),
)

# --- A2: baseline & evidence collection (18 nodes; 6 implemented, 12 blocked) ---
# Blocked reasons are owned by `application.a2_handlers.A2_BLOCKED_NODES` (the
# handler module) — this manifest imports that dict rather than duplicating it,
# so the two never drift.

_A2_IMPLEMENTED_ROWS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "A2.10",
        "Verify request schema, digest, signature, approval, policy and source "
        "accessibility",
        ("A2IntakeDecision",),
    ),
    (
        "A2.20",
        "Snapshot content, relevant modes, dirty/untracked files and submodule "
        "revisions; exclude secret/disallowed paths",
        ("SourceSnapshot",),
    ),
    (
        "A2.30",
        "Parse snapshot to languages, modules, manifests, dependencies, symbols, "
        "tests, generated/vendor classification and tool coverage",
        ("RepositoryManifest",),
    ),
    (
        "A2.31",
        "Resolve only repository-owned build/test/lint/type/security/benchmark argv "
        "and working directory",
        ("VerificationManifest",),
    ),
    (
        "A2.40",
        "Bind every evidence requirement to collector/query/command, window, "
        "aggregation and minimum sample count",
        ("CollectorPlan",),
    ),
    (
        "A2.41",
        "Pin tool/runtime/lockfile versions, env names, hardware, concurrency and "
        "cache state",
        ("EnvironmentManifest",),
    ),
    (
        "A2.50",
        "Authorize argv, write roots, egress, secret leases, resources and timeout; "
        "create worker jobs through broker",
        ("ExecutionAuthorization",),
    ),
    (
        "A2.60",
        "Run applicable static/dependency/security/complexity analyzers against snapshot "
        "(lint/type commands via WorkerBroker; benchmark stays honestly unavailable)",
        ("BranchEvidenceRefs",),
    ),
    (
        "A2.61",
        "Run approved correctness checks; capture command, exit, duration, flakes and "
        "raw output",
        ("BranchEvidenceRefs",),
    ),
    (
        "A2.62",
        "Run exact warmups/repetitions; preserve each sample and environment identity "
        "(always unavailable today: A2.31 never emits a benchmark command)",
        ("BranchEvidenceRefs",),
    ),
    (
        "A2.63",
        "Import declared logs/metrics/traces/profiles/LLM traces using bounded queries "
        "(always unavailable today: no telemetry query adapter is wired)",
        ("BranchEvidenceRefs",),
    ),
    (
        "A2.64",
        "Build file/symbol/call/service/runtime map with confidence and unresolved edges "
        "(local ast parsing of Python source; other languages report a gap)",
        ("BranchEvidenceRefs",),
    ),
    (
        "A2.70",
        "Fan-in by stable branch identity; write raw bytes create-only with digest, "
        "MIME, collector/tool/command/time metadata",
        ("RawEvidenceFanIn",),
    ),
    (
        "A2.71",
        "Normalize registered units/dimensions and aggregate only after raw preservation",
        ("NormalizedEvidenceSet",),
    ),
    (
        "A2.80",
        "Bind tenant/case/request/feature/snapshot/workload/dataset/environment/model/"
        "sample/trace/collector provenance and assign trust",
        ("EvidenceBundle",),
    ),
    (
        "A2.90",
        "Evaluate coverage, samples, freshness, integrity, redaction, failures and "
        "analyzer coverage",
        ("EvidenceQualityReport",),
    ),
    (
        "A2.91",
        "Compare all material dimensions and produce per-dimension verdict",
        ("ComparabilityReport",),
    ),
    (
        "A2.95",
        "Aggregate from eligible raw samples; bind all parent digests; sign and "
        "publish A3 handoff",
        ("BaselineSnapshot",),
    ),
)


def _a2_entries() -> dict[str, NodeManifestEntry]:
    return _implemented("A2", _A2_IMPLEMENTED_ROWS)


# --- A3: root-cause analysis & solution portfolio (22 nodes, all planned) ---

_A3_ROWS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "A3.10",
        "Verify complete A1/A2 schema, digest chain, signatures, snapshot and gate status",
        ("A3IntakeDecision",),
    ),
    (
        "A3.11",
        "Index evidence IDs, trust, metrics, windows, feature locations and counterevidence",
        ("EvidenceCatalog",),
    ),
    (
        "A3.20",
        "Deterministically compare baseline with criteria/guardrails and distributions "
        "(fires on real A2 command evidence; business metrics A2 cannot yet collect "
        "legitimately produce zero signals, not a bug)",
        ("ProblemSignalSet",),
    ),
    (
        "A3.21",
        "Score impact, weight, scope, frequency, severity and trust using a documented "
        "deterministic formula (breach magnitude 70% + evidence weight 30%)",
        ("PrioritizedSignalSet",),
    ),
    (
        "A3.30",
        "Syntax/pattern analysis near prioritized signals (ast-based long-function/"
        "bare-except scan over the snapshot's Python files)",
        ("AnalyzerObservationBranch",),
    ),
    (
        "A3.31",
        "Applicable semantic/data-flow/call-graph analysis (ast-based call-target "
        "inventory per Python file)",
        ("AnalyzerObservationBranch",),
    ),
    (
        "A3.32",
        "Applicable domain analysis only (always unavailable today: no domain "
        "analyzer registered)",
        ("AnalyzerObservationBranch",),
    ),
    (
        "A3.33",
        "Correlate traces/profiles/logs/samples to files, symbols and paths (always "
        "unavailable today: A2.63 telemetry is always unavailable)",
        ("AnalyzerObservationBranch",),
    ),
    (
        "A3.40",
        "Give approved generator bounded catalogue context; require typed claims, "
        "evidence IDs, counterevidence and unknowns (real LLM call via "
        "ModelProviderPort, one bounded repair on invalid JSON)",
        ("FindingDraftSet",),
    ),
    (
        "A3.41",
        "Resolve every citation, scope and exact claim support deterministically",
        ("CitationResolutionReportSet",),
    ),
    (
        "A3.50",
        "Independent judge checks symptom, location, wording and contradictions "
        "(separate ModelCompletionRequest per finding, generator's context never "
        "shared)",
        ("FindingJudgementSet",),
    ),
    (
        "A3.51",
        "Assign observation/hypothesis/verified-cause maturity (never asserts T4 — "
        "today's single-sample-per-command A2 evidence cannot honestly support it, "
        "so verified_cause is always demoted to hypothesis)",
        ("FindingSet",),
    ),
    (
        "A3.60",
        "Generate materially different strategies tied to eligible findings (real "
        "LLM call; on a revision pass, regenerates only A3.82-targeted strategies)",
        ("StrategyDraftSet",),
    ),
    (
        "A3.61",
        "Define ordered phases with exactly one logical treatment each (validates "
        "generator-supplied phase sequencing deterministically)",
        ("StrategyDraftSet",),
    ),
    (
        "A3.62",
        "Assess every criterion and guardrail; label measured versus forecast (always "
        "forecast today — A3 never executes a strategy, so nothing is honestly "
        "measured yet)",
        ("StrategyDraftSet",),
    ),
    (
        "A3.63",
        "Capture concrete pros, cons, prerequisites, effort, uncertainty, operations "
        "and opportunity cost (deterministic duplicate-strategy detection via "
        "strategy_tradeoffs text)",
        ("StrategyDraftSet",),
    ),
    (
        "A3.64",
        "Bind validation commands/protocol, expected movement, stop conditions and "
        "executable rollback (real A2.31 VerificationManifest commands; git-revert "
        "rollback)",
        ("StrategyDraftSet",),
    ),
    (
        "A3.70",
        "Resolve every path/symbol/config/prompt/dependency against snapshot or label "
        "proposed creation (real SourceSnapshot.files lookup against generator-"
        "supplied target_paths)",
        ("StrategyDraftSet",),
    ),
    (
        "A3.80",
        "Determine risk from blast radius, reversibility, uncertainty, migration and "
        "security; assembles the final SolutionStrategy from every prior dimension",
        ("SolutionStrategySet",),
    ),
    (
        "A3.81",
        "Apply evidence, maturity, diversity, coverage, treatment and rollback hard "
        "gates; routes continue/revision/rejected",
        ("A3QualityReport",),
    ),
    (
        "A3.82",
        "Revise only failed dimensions; preserve attempts; enforce retry/token "
        "deadline (bounded revision loop back to A3.60)",
        ("RevisionDirective",),
    ),
    (
        "A3.90",
        "Seal all eligible/ineligible strategies, quality and provenance into "
        "SolutionPortfolio (FindingSet is already sealed by A3.51)",
        ("SolutionPortfolio",),
    ),
)

# --- B1: automatic discovery scan (26 nodes, all planned) ---

_B1_ROWS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "B1.10",
        "Authenticate scheduler/service command; create scan/thread; freeze tenant, "
        "window, policy and budget; enforce overlap rule",
        (),
    ),
    (
        "B1.20",
        "Load tenant-approved repository/feature/owner registrations at pinned "
        "versions; canonicalize and check accessibility",
        (),
    ),
    (
        "B1.21",
        "Fingerprint Git revision, dirty state and bounded content; compare "
        "registered/deployment identity",
        (),
    ),
    (
        "B1.30",
        "Inventory available metrics/logs/traces/profiles/benchmarks/LLM evaluation "
        "windows without executing workloads",
        (),
    ),
    (
        "B1.31",
        "Authorize endpoint, query template, parameters, time/range/cardinality, "
        "tenant and read-only credentials",
        (),
    ),
    ("B1.32", "Execute registered bounded metrics queries for the frozen window", ()),
    (
        "B1.33",
        "Execute structured log queries; require stable labels/fingerprint/trace IDs "
        "where configured",
        (),
    ),
    (
        "B1.34",
        "Query traces/profiles; capture span/frame coverage and deployment/source "
        "correlation",
        (),
    ),
    (
        "B1.35",
        "Query model/prompt/token/cost/latency/evaluation history when the registered "
        "feature is LLM-backed",
        (),
    ),
    (
        "B1.40",
        "Preserve raw records, normalize registered units and group by all material "
        "identities",
        (),
    ),
    (
        "B1.41",
        "Apply schema, source, freshness, trust and minimum-sample eligibility",
        (),
    ),
    ("B1.50", "Apply absolute SLO/guardrail/budget thresholds", ()),
    (
        "B1.51",
        "Compare compatible windows; quantify direction, effect and practical "
        "significance",
        (),
    ),
    (
        "B1.52",
        "Aggregate repeated slow spans/profile frames/error fingerprints/resource "
        "saturation",
        (),
    ),
    (
        "B1.53",
        "Detect quality drift, scorer failure, token/cost/latency inflation with "
        "stable cohorts",
        (),
    ),
    (
        "B1.60",
        "Resolve feature by explicit label, then trace/service/symbol registration; "
        "retain alternatives/confidence",
        (),
    ),
    (
        "B1.61",
        "Bind deployment/trace commit to available local Git object/content snapshot",
        (),
    ),
    (
        "B1.62",
        "Resolve code, service and decision owners; detect disagreement and escalation",
        (),
    ),
    (
        "B1.70",
        "Deterministically score severity, frequency, impact, trust, addressability "
        "and priority",
        (),
    ),
    ("B1.71", "Apply hard trust, identity, samples, actionability and policy gates", ()),
    (
        "B1.80",
        "Compute deterministic candidate fingerprint; compare active/recent cases; "
        "merge only same identity",
        (),
    ),
    (
        "B1.81",
        "Apply cooldown, expiry, severity/material-change override and suppression audit",
        (),
    ),
    (
        "B1.90",
        "Construct OptimizationRequest@1.0 origin automatic from verified facts and "
        "registries",
        (),
    ),
    (
        "B1.91",
        "Apply automatic-intake policy; request owner confirmation with digest-bound "
        "interrupt when needed",
        (),
    ),
    ("B1.95", "Invoke the exact compiled A2 subgraph with historical_recovery", ()),
    (
        "B1.96",
        "Seal scan/signal/request/baseline/owner/policy chain and atomically enqueue "
        "deterministic case start",
        ("QualifiedOpportunity",),
    ),
)

# --- B2: automatic proposal (12 nodes, all planned) ---

_B2_ROWS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "B2.10",
        "Verify opportunity schema/digests, unique case binding, source/owner "
        "availability, age and baseline comparability before spending budget",
        (),
    ),
    (
        "B2.20",
        "Select applicable analyzers using language, evidence, topology, risk, "
        "registry availability and budget",
        (),
    ),
    (
        "B2.21",
        "Build bounded/redacted context with evidence IDs, relevant source regions, "
        "counterevidence, criteria and truncation report",
        (),
    ),
    (
        "B2.22",
        "Invoke the exact compiled A3 subgraph with origin automatic",
        ("FindingSet", "SolutionPortfolio", "A3QualityReport"),
    ),
    (
        "B2.30",
        "Recheck feature/source/owner bindings; label detected facts versus inferred "
        "discovery assumptions",
        (),
    ),
    (
        "B2.31",
        "Compare current source, registry, evidence and policy versions with the B1 "
        "handoff",
        (),
    ),
    (
        "B2.40",
        "Consolidate measured impact, evidence, cause maturity, unknowns, "
        "eligible/ineligible strategies, tradeoffs, risk and quality",
        ("ProposalEnvelope",),
    ),
    ("B2.41", "Produce concise explanation from sealed structured facts", ()),
    ("B2.50", "Route by risk, security, confidence, ownership and policy", ()),
    (
        "B2.51",
        "Interrupt exact thread with proposal digest, decisions, required role, "
        "policy and expiry",
        (),
    ),
    (
        "B2.52",
        "Apply rejection/cooldown or target only requested A3/B2 dimensions for "
        "bounded revision",
        (),
    ),
    ("B2.60", "Seal proposal, approval and B1/A3 lineage; emit C0 handoff", ()),
)

# --- C0: shared convergence gate (7 nodes, all planned) ---
# Descriptions per docs/project-blueprint/shared-workflow/00-convergence.md.

_C0_ROWS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "C0.10",
        "Accept only manual or automatic origin; retain origin for audit but not "
        "downstream quality policy",
        (),
    ),
    (
        "C0.20",
        "Validate request, baseline, findings, solutions and quality schemas/versions",
        (),
    ),
    (
        "C0.30",
        "Confirm request, source snapshot, evidence and solution references form one "
        "immutable chain",
        (),
    ),
    (
        "C0.40",
        "Require B1 request/baseline to satisfy A1/A2 contracts and B2 to satisfy A3 "
        "policy",
        (),
    ),
    (
        "C0.50",
        "Reject or refresh stale source, evidence, approval or ownership before "
        "expensive work",
        (),
    ),
    (
        "C0.60",
        "Require comparable baseline, passed A3 quality and at least one eligible "
        "solution",
        ("ConvergenceDecision",),
    ),
    (
        "C0.70",
        "Produce lane-neutral references and handoff event for step 01",
        ("ConvergedCase",),
    ),
)


def _build_manifest() -> dict[str, NodeManifestEntry]:
    manifest: dict[str, NodeManifestEntry] = {
        **_a2_entries(),
        **_implemented("A1", _A1_ROWS),
        **_implemented("A3", _A3_ROWS),
        **_planned("B1", _B1_ROWS),
        **_planned("B2", _B2_ROWS),
        **_planned("C0", _C0_ROWS),
    }

    expected = ALL_BUSINESS_NODE_IDS
    actual = frozenset(manifest)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(f"node manifest is incomplete: missing={missing} extra={extra}")
    for lane, node_ids in (
        ("A1", A1_NODE_IDS),
        ("A2", A2_NODE_IDS),
        ("A3", A3_NODE_IDS),
        ("B1", B1_NODE_IDS),
        ("B2", B2_NODE_IDS),
        ("C0", C0_NODE_IDS),
    ):
        mismatched = [
            node_id for node_id in node_ids if manifest[node_id].lane != lane
        ]
        if mismatched:
            raise ValueError(f"node manifest lane mismatch for {lane}: {mismatched}")
    return manifest


NODE_MANIFEST: MappingProxyType[str, NodeManifestEntry] = MappingProxyType(_build_manifest())


def manifest_for(node_id: str) -> NodeManifestEntry:
    entry = NODE_MANIFEST.get(node_id)
    if entry is None:
        raise KeyError(f"{node_id!r} is not a known business node")
    return entry


def status_counts() -> dict[NodeStatus, int]:
    counts: dict[NodeStatus, int] = {"implemented": 0, "blocked": 0, "planned": 0}
    for entry in NODE_MANIFEST.values():
        counts[entry.status] += 1
    return counts


__all__ = [
    "NODE_MANIFEST",
    "NodeManifestEntry",
    "NodeStatus",
    "manifest_for",
    "status_counts",
]

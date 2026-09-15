from __future__ import annotations

from production_optimizer.application.node_contract import NodeSpec, SideEffectClass
from production_optimizer.application.node_runtime import (
    BusinessNodeHandler,
    NodeExecution,
    NodePorts,
    NodeRoute,
    NodeRuntime,
    RegisteredNode,
)
from production_optimizer.contracts.state import OptimizationState

from .nodes.a2_10_verify_request_schema_digest_signature_approval_policy import (
    handle_a2_10_verify_request_schema_digest_signature_approval_policy,
)
from .nodes.a2_20_snapshot_content_relevant_modes_dirty_untracked_files import (
    handle_a2_20_snapshot_content_relevant_modes_dirty_untracked_files,
)
from .nodes.a2_30_parse_snapshot_languages_modules_manifests_dependencies_symbols import (
    handle_a2_30_parse_snapshot_languages_modules_manifests_dependencies_symbols,
)
from .nodes.a2_31_resolve_repository_owned_build_test_lint_type import (
    handle_a2_31_resolve_repository_owned_build_test_lint_type,
)
from .nodes.a2_40_bind_every_evidence_requirement_collector_query_command import (
    handle_a2_40_bind_every_evidence_requirement_collector_query_command,
)
from .nodes.a2_41_pin_tool_runtime_lockfile_versions_env_names import (
    handle_a2_41_pin_tool_runtime_lockfile_versions_env_names,
)
from .nodes.a2_50_authorize_argv_write_roots_egress_secret_leases import (
    handle_a2_50_authorize_argv_write_roots_egress_secret_leases,
)
from .nodes.a2_60_run_applicable_static_dependency_security_complexity_analyzers import (
    handle_a2_60_run_applicable_static_dependency_security_complexity_analyzers,
)
from .nodes.a2_61_run_approved_correctness_checks_capture_command_exit import (
    handle_a2_61_run_approved_correctness_checks_capture_command_exit,
)
from .nodes.a2_62_run_exact_warmups_repetitions_preserve_each_sample import (
    handle_a2_62_run_exact_warmups_repetitions_preserve_each_sample,
)
from .nodes.a2_63_import_declared_logs_metrics_traces_profiles_llm import (
    handle_a2_63_import_declared_logs_metrics_traces_profiles_llm,
)
from .nodes.a2_64_build_file_symbol_call_service_runtime_map import (
    handle_a2_64_build_file_symbol_call_service_runtime_map,
)
from .nodes.a2_70_fan_stable_branch_identity_write_raw_bytes import (
    handle_a2_70_fan_stable_branch_identity_write_raw_bytes,
)
from .nodes.a2_71_normalize_registered_units_dimensions_aggregate_after_raw import (
    handle_a2_71_normalize_registered_units_dimensions_aggregate_after_raw,
)
from .nodes.a2_80_bind_tenant_case_request_feature_snapshot_workload import (
    handle_a2_80_bind_tenant_case_request_feature_snapshot_workload,
)
from .nodes.a2_90_evaluate_coverage_samples_freshness_integrity_redaction_failures import (
    handle_a2_90_evaluate_coverage_samples_freshness_integrity_redaction_failures,
)
from .nodes.a2_91_compare_all_material_dimensions_produce_per_dimension import (
    handle_a2_91_compare_all_material_dimensions_produce_per_dimension,
)
from .nodes.a2_95_aggregate_eligible_raw_samples_bind_all_parent import (
    handle_a2_95_aggregate_eligible_raw_samples_bind_all_parent,
)

NODE_HANDLERS = {
    "A2.10": handle_a2_10_verify_request_schema_digest_signature_approval_policy,
    "A2.20": handle_a2_20_snapshot_content_relevant_modes_dirty_untracked_files,
    "A2.30": handle_a2_30_parse_snapshot_languages_modules_manifests_dependencies_symbols,
    "A2.31": handle_a2_31_resolve_repository_owned_build_test_lint_type,
    "A2.40": handle_a2_40_bind_every_evidence_requirement_collector_query_command,
    "A2.41": handle_a2_41_pin_tool_runtime_lockfile_versions_env_names,
    "A2.50": handle_a2_50_authorize_argv_write_roots_egress_secret_leases,
    "A2.60": handle_a2_60_run_applicable_static_dependency_security_complexity_analyzers,
    "A2.61": handle_a2_61_run_approved_correctness_checks_capture_command_exit,
    "A2.62": handle_a2_62_run_exact_warmups_repetitions_preserve_each_sample,
    "A2.63": handle_a2_63_import_declared_logs_metrics_traces_profiles_llm,
    "A2.64": handle_a2_64_build_file_symbol_call_service_runtime_map,
    "A2.70": handle_a2_70_fan_stable_branch_identity_write_raw_bytes,
    "A2.71": handle_a2_71_normalize_registered_units_dimensions_aggregate_after_raw,
    "A2.80": handle_a2_80_bind_tenant_case_request_feature_snapshot_workload,
    "A2.90": handle_a2_90_evaluate_coverage_samples_freshness_integrity_redaction_failures,
    "A2.91": handle_a2_91_compare_all_material_dimensions_produce_per_dimension,
    "A2.95": handle_a2_95_aggregate_eligible_raw_samples_bind_all_parent,
}


A2_BLOCKED_NODES: dict[str, str] = {}

_A2_ROUTE_OVERRIDES: dict[str, set[str]] = {
    "A2.10": {NodeRoute.CONTINUE.value, NodeRoute.REJECTED.value},
    "A2.31": {NodeRoute.CONTINUE.value, NodeRoute.APPROVAL.value},
    "A2.40": {NodeRoute.CONTINUE.value, NodeRoute.MISSING.value},
    "A2.50": {NodeRoute.CONTINUE.value, NodeRoute.REJECTED.value},
    "A2.90": {NodeRoute.CONTINUE.value, NodeRoute.MISSING.value, NodeRoute.REJECTED.value},
    "A2.91": {NodeRoute.CONTINUE.value, NodeRoute.MISSING.value, NodeRoute.INCOMPARABLE.value},
}


def _spec(node_id: str) -> NodeSpec:
    routes = _A2_ROUTE_OVERRIDES.get(node_id, {NodeRoute.CONTINUE.value})
    side_effect_class = (
        SideEffectClass.EXTERNAL_JOB
        if node_id in {"A2.31", "A2.50", "A2.60", "A2.61", "A2.62"}
        else SideEffectClass.IDEMPOTENT_WRITE
    )
    return NodeSpec(
        node_id=node_id,
        business_task_id=node_id,
        owner="lane-1-a2",
        input_contract=f"{node_id}Input@1.0",
        output_contract=f"{node_id}Output@1.0",
        supported_schema_majors={1},
        idempotency_key_version="a2-production-v1",
        side_effect_class=side_effect_class,
        timeout_seconds=60,
        max_attempts=2,
        allowed_routes=routes,
        runbook="docs/implementation/05-lane-1-detailed-implementation-playbook.md",
        slo="A2 node completes within 60 seconds for local repositories",
    )


def _handler(node_id: str) -> BusinessNodeHandler:
    node_handler = NODE_HANDLERS[node_id]

    def execute(state: OptimizationState, ports: NodePorts | None, /) -> NodeExecution:
        if ports is None:
            raise RuntimeError("production A2 handlers require configured node ports")
        return node_handler(state, ports)

    execute.__name__ = node_handler.__name__
    execute.__module__ = node_handler.__module__
    return execute


def build_a2_registrations() -> dict[str, RegisteredNode]:
    # Imported lazily to avoid application -> orchestration package import
    # cycles while the application composition root is initializing.
    from production_optimizer.orchestration.catalog import A2_NODE_IDS

    return {
        node_id: RegisteredNode(spec=_spec(node_id), handler=_handler(node_id))
        for node_id in A2_NODE_IDS
    }


def build_bound_a2_registrations() -> dict[str, RegisteredNode]:
    """Compatibility alias for the former factory name."""
    return build_a2_registrations()


def build_a2_runtime(*, ports: NodePorts) -> NodeRuntime:
    """Build A2 with all 18 named production handlers."""
    return NodeRuntime(build_a2_registrations(), ports=ports)


__all__ = [
    "A2_BLOCKED_NODES",
    "NODE_HANDLERS",
    "build_a2_registrations",
    "build_a2_runtime",
    "build_bound_a2_registrations",
]

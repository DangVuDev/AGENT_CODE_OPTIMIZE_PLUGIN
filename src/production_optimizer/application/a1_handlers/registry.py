from .nodes.a1_10_authenticate_command_validate_tenant_case_thread_idempotency import (
    handle_a1_10_authenticate_command_validate_tenant_case_thread_idempotency,
)
from .nodes.a1_20_structured_input_uses_schema_path_raw_text import (
    handle_a1_20_structured_input_uses_schema_path_raw_text,
)
from .nodes.a1_30_resolve_allowed_root_canonicalize_path_block_traversal import (
    handle_a1_30_resolve_allowed_root_canonicalize_path_block_traversal,
)
from .nodes.a1_40_bounded_read_discovery_languages_manifests_tools_tests import (
    handle_a1_40_bounded_read_discovery_languages_manifests_tools_tests,
)
from .nodes.a1_50_resolve_registered_feature_first_then_evidence_based import (
    handle_a1_50_resolve_registered_feature_first_then_evidence_based,
)
from .nodes.a1_60_separate_objective_requester_remedy_retain_remedy_hypothesis import (
    handle_a1_60_separate_objective_requester_remedy_retain_remedy_hypothesis,
)
from .nodes.a1_61_define_least_one_criterion_metric_direction_target import (
    handle_a1_61_define_least_one_criterion_metric_direction_target,
)
from .nodes.a1_62_define_correctness_plus_relevant_security_compatibility_cost import (
    handle_a1_62_define_correctness_plus_relevant_security_compatibility_cost,
)
from .nodes.a1_63_apply_tenant_quota_policy_freeze_deadline_worker import (
    handle_a1_63_apply_tenant_quota_policy_freeze_deadline_worker,
)
from .nodes.a1_70_resolve_repository_owned_workload_scenario_dataset_environment import (
    handle_a1_70_resolve_repository_owned_workload_scenario_dataset_environment,
)
from .nodes.a1_71_map_every_criterion_guardrail_acceptable_source_types import (
    handle_a1_71_map_every_criterion_guardrail_acceptable_source_types,
)
from .nodes.a1_80_deterministically_check_omissions_conflicts_units_duplicate_ids import (
    handle_a1_80_deterministically_check_omissions_conflicts_units_duplicate_ids,
)
from .nodes.a1_90_evaluate_scope_data_risk_rbac_policy_create import (
    handle_a1_90_evaluate_scope_data_risk_rbac_policy_create,
)
from .nodes.a1_95_canonicalize_calculate_digest_sign_metadata_create_persist import (
    handle_a1_95_canonicalize_calculate_digest_sign_metadata_create_persist,
)

NODE_HANDLERS = {
    "A1.10": handle_a1_10_authenticate_command_validate_tenant_case_thread_idempotency,
    "A1.20": handle_a1_20_structured_input_uses_schema_path_raw_text,
    "A1.30": handle_a1_30_resolve_allowed_root_canonicalize_path_block_traversal,
    "A1.40": handle_a1_40_bounded_read_discovery_languages_manifests_tools_tests,
    "A1.50": handle_a1_50_resolve_registered_feature_first_then_evidence_based,
    "A1.60": handle_a1_60_separate_objective_requester_remedy_retain_remedy_hypothesis,
    "A1.61": handle_a1_61_define_least_one_criterion_metric_direction_target,
    "A1.62": handle_a1_62_define_correctness_plus_relevant_security_compatibility_cost,
    "A1.63": handle_a1_63_apply_tenant_quota_policy_freeze_deadline_worker,
    "A1.70": handle_a1_70_resolve_repository_owned_workload_scenario_dataset_environment,
    "A1.71": handle_a1_71_map_every_criterion_guardrail_acceptable_source_types,
    "A1.80": handle_a1_80_deterministically_check_omissions_conflicts_units_duplicate_ids,
    "A1.90": handle_a1_90_evaluate_scope_data_risk_rbac_policy_create,
    "A1.95": handle_a1_95_canonicalize_calculate_digest_sign_metadata_create_persist,
}

__all__ = ["NODE_HANDLERS"]

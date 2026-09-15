from .nodes.b1_10_authenticate_scheduler_service_command_create_scan_thread import (
    handle_b1_10_authenticate_scheduler_service_command_create_scan_thread,
)
from .nodes.b1_20_load_tenant_approved_repository_feature_owner_registrations import (
    handle_b1_20_load_tenant_approved_repository_feature_owner_registrations,
)
from .nodes.b1_21_fingerprint_git_revision_dirty_state_bounded_content import (
    handle_b1_21_fingerprint_git_revision_dirty_state_bounded_content,
)
from .nodes.b1_30_inventory_available_metrics_logs_traces_profiles_benchmarks import (
    handle_b1_30_inventory_available_metrics_logs_traces_profiles_benchmarks,
)
from .nodes.b1_31_authorize_endpoint_query_template_parameters_time_range import (
    handle_b1_31_authorize_endpoint_query_template_parameters_time_range,
)
from .nodes.b1_32_execute_registered_bounded_metrics_queries_frozen_window import (
    handle_b1_32_execute_registered_bounded_metrics_queries_frozen_window,
)
from .nodes.b1_33_execute_structured_log_queries_require_stable_labels import (
    handle_b1_33_execute_structured_log_queries_require_stable_labels,
)
from .nodes.b1_34_query_traces_profiles_capture_span_frame_coverage import (
    handle_b1_34_query_traces_profiles_capture_span_frame_coverage,
)
from .nodes.b1_35_query_model_prompt_token_cost_latency_evaluation import (
    handle_b1_35_query_model_prompt_token_cost_latency_evaluation,
)
from .nodes.b1_40_preserve_raw_records_normalize_registered_units_group import (
    handle_b1_40_preserve_raw_records_normalize_registered_units_group,
)
from .nodes.b1_41_apply_schema_source_freshness_trust_minimum_sample import (
    handle_b1_41_apply_schema_source_freshness_trust_minimum_sample,
)
from .nodes.b1_50_apply_absolute_slo_guardrail_budget_thresholds import (
    handle_b1_50_apply_absolute_slo_guardrail_budget_thresholds,
)
from .nodes.b1_51_compare_compatible_windows_quantify_direction_effect_practical import (
    handle_b1_51_compare_compatible_windows_quantify_direction_effect_practical,
)
from .nodes.b1_52_aggregate_repeated_slow_spans_profile_frames_error import (
    handle_b1_52_aggregate_repeated_slow_spans_profile_frames_error,
)
from .nodes.b1_53_detect_quality_drift_scorer_failure_token_cost import (
    handle_b1_53_detect_quality_drift_scorer_failure_token_cost,
)
from .nodes.b1_60_resolve_feature_explicit_label_then_trace_service import (
    handle_b1_60_resolve_feature_explicit_label_then_trace_service,
)
from .nodes.b1_61_bind_deployment_trace_commit_available_local_git import (
    handle_b1_61_bind_deployment_trace_commit_available_local_git,
)
from .nodes.b1_62_resolve_code_service_decision_owners_detect_disagreement import (
    handle_b1_62_resolve_code_service_decision_owners_detect_disagreement,
)
from .nodes.b1_70_deterministically_score_severity_frequency_impact_trust_addressability import (
    handle_b1_70_deterministically_score_severity_frequency_impact_trust_addressability,
)
from .nodes.b1_71_apply_hard_trust_identity_samples_actionability_policy import (
    handle_b1_71_apply_hard_trust_identity_samples_actionability_policy,
)
from .nodes.b1_80_compute_deterministic_candidate_fingerprint_compare_active_recent import (
    handle_b1_80_compute_deterministic_candidate_fingerprint_compare_active_recent,
)
from .nodes.b1_81_apply_cooldown_expiry_severity_material_change_override import (
    handle_b1_81_apply_cooldown_expiry_severity_material_change_override,
)
from .nodes.b1_90_construct_optimizationrequest_1_0_origin_automatic_verified import (
    handle_b1_90_construct_optimizationrequest_1_0_origin_automatic_verified,
)
from .nodes.b1_91_apply_automatic_intake_policy_request_owner_confirmation import (
    handle_b1_91_apply_automatic_intake_policy_request_owner_confirmation,
)
from .nodes.b1_95_invoke_exact_compiled_a2_subgraph_historical_recovery import (
    handle_b1_95_invoke_exact_compiled_a2_subgraph_historical_recovery,
)
from .nodes.b1_96_seal_scan_signal_request_baseline_owner_policy import (
    handle_b1_96_seal_scan_signal_request_baseline_owner_policy,
)

NODE_HANDLERS = {
    "B1.10": handle_b1_10_authenticate_scheduler_service_command_create_scan_thread,
    "B1.20": handle_b1_20_load_tenant_approved_repository_feature_owner_registrations,
    "B1.21": handle_b1_21_fingerprint_git_revision_dirty_state_bounded_content,
    "B1.30": handle_b1_30_inventory_available_metrics_logs_traces_profiles_benchmarks,
    "B1.31": handle_b1_31_authorize_endpoint_query_template_parameters_time_range,
    "B1.32": handle_b1_32_execute_registered_bounded_metrics_queries_frozen_window,
    "B1.33": handle_b1_33_execute_structured_log_queries_require_stable_labels,
    "B1.34": handle_b1_34_query_traces_profiles_capture_span_frame_coverage,
    "B1.35": handle_b1_35_query_model_prompt_token_cost_latency_evaluation,
    "B1.40": handle_b1_40_preserve_raw_records_normalize_registered_units_group,
    "B1.41": handle_b1_41_apply_schema_source_freshness_trust_minimum_sample,
    "B1.50": handle_b1_50_apply_absolute_slo_guardrail_budget_thresholds,
    "B1.51": handle_b1_51_compare_compatible_windows_quantify_direction_effect_practical,
    "B1.52": handle_b1_52_aggregate_repeated_slow_spans_profile_frames_error,
    "B1.53": handle_b1_53_detect_quality_drift_scorer_failure_token_cost,
    "B1.60": handle_b1_60_resolve_feature_explicit_label_then_trace_service,
    "B1.61": handle_b1_61_bind_deployment_trace_commit_available_local_git,
    "B1.62": handle_b1_62_resolve_code_service_decision_owners_detect_disagreement,
    "B1.70": handle_b1_70_deterministically_score_severity_frequency_impact_trust_addressability,
    "B1.71": handle_b1_71_apply_hard_trust_identity_samples_actionability_policy,
    "B1.80": handle_b1_80_compute_deterministic_candidate_fingerprint_compare_active_recent,
    "B1.81": handle_b1_81_apply_cooldown_expiry_severity_material_change_override,
    "B1.90": handle_b1_90_construct_optimizationrequest_1_0_origin_automatic_verified,
    "B1.91": handle_b1_91_apply_automatic_intake_policy_request_owner_confirmation,
    "B1.95": handle_b1_95_invoke_exact_compiled_a2_subgraph_historical_recovery,
    "B1.96": handle_b1_96_seal_scan_signal_request_baseline_owner_policy,
}

__all__ = ["NODE_HANDLERS"]

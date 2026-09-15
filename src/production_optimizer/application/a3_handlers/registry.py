from .nodes.a3_10_verify_complete_a1_a2_schema_digest_chain import (
    handle_a3_10_verify_complete_a1_a2_schema_digest_chain,
)
from .nodes.a3_11_index_evidence_ids_trust_metrics_windows_feature import (
    handle_a3_11_index_evidence_ids_trust_metrics_windows_feature,
)
from .nodes.a3_20_deterministically_compare_baseline_criteria_guardrails_distributions_fir import (
    handle_a3_20_deterministically_compare_baseline_criteria_guardrails_distributions_fir,
)
from .nodes.a3_21_score_impact_weight_scope_frequency_severity_trust import (
    handle_a3_21_score_impact_weight_scope_frequency_severity_trust,
)
from .nodes.a3_30_syntax_pattern_analysis_near_prioritized_signals_ast import (
    handle_a3_30_syntax_pattern_analysis_near_prioritized_signals_ast,
)
from .nodes.a3_31_applicable_semantic_data_flow_call_graph_analysis import (
    handle_a3_31_applicable_semantic_data_flow_call_graph_analysis,
)
from .nodes.a3_32_applicable_domain_analysis_always_unavailable_today_no import (
    handle_a3_32_applicable_domain_analysis_always_unavailable_today_no,
)
from .nodes.a3_33_correlate_traces_profiles_logs_samples_files_symbols import (
    handle_a3_33_correlate_traces_profiles_logs_samples_files_symbols,
)
from .nodes.a3_40_give_approved_generator_bounded_catalogue_context_require import (
    handle_a3_40_give_approved_generator_bounded_catalogue_context_require,
)
from .nodes.a3_41_resolve_every_citation_scope_exact_claim_support import (
    handle_a3_41_resolve_every_citation_scope_exact_claim_support,
)
from .nodes.a3_50_independent_judge_checks_symptom_location_wording_contradictions import (
    handle_a3_50_independent_judge_checks_symptom_location_wording_contradictions,
)
from .nodes.a3_51_assign_observation_hypothesis_verified_cause_maturity_never import (
    handle_a3_51_assign_observation_hypothesis_verified_cause_maturity_never,
)
from .nodes.a3_60_generate_materially_different_strategies_tied_eligible_findings import (
    handle_a3_60_generate_materially_different_strategies_tied_eligible_findings,
)
from .nodes.a3_61_define_ordered_phases_exactly_one_logical_treatment import (
    handle_a3_61_define_ordered_phases_exactly_one_logical_treatment,
)
from .nodes.a3_62_assess_every_criterion_guardrail_label_measured_versus import (
    handle_a3_62_assess_every_criterion_guardrail_label_measured_versus,
)
from .nodes.a3_63_capture_concrete_pros_cons_prerequisites_effort_uncertainty import (
    handle_a3_63_capture_concrete_pros_cons_prerequisites_effort_uncertainty,
)
from .nodes.a3_64_bind_validation_commands_protocol_expected_movement_stop import (
    handle_a3_64_bind_validation_commands_protocol_expected_movement_stop,
)
from .nodes.a3_70_resolve_every_path_symbol_config_prompt_dependency import (
    handle_a3_70_resolve_every_path_symbol_config_prompt_dependency,
)
from .nodes.a3_80_determine_risk_blast_radius_reversibility_uncertainty_migration import (
    handle_a3_80_determine_risk_blast_radius_reversibility_uncertainty_migration,
)
from .nodes.a3_81_apply_evidence_maturity_diversity_coverage_treatment_rollback import (
    handle_a3_81_apply_evidence_maturity_diversity_coverage_treatment_rollback,
)
from .nodes.a3_82_revise_failed_dimensions_preserve_attempts_enforce_retry import (
    handle_a3_82_revise_failed_dimensions_preserve_attempts_enforce_retry,
)
from .nodes.a3_90_seal_all_eligible_ineligible_strategies_quality_provenance import (
    handle_a3_90_seal_all_eligible_ineligible_strategies_quality_provenance,
)

NODE_HANDLERS = {
    "A3.10": handle_a3_10_verify_complete_a1_a2_schema_digest_chain,
    "A3.11": handle_a3_11_index_evidence_ids_trust_metrics_windows_feature,
    "A3.20": handle_a3_20_deterministically_compare_baseline_criteria_guardrails_distributions_fir,
    "A3.21": handle_a3_21_score_impact_weight_scope_frequency_severity_trust,
    "A3.30": handle_a3_30_syntax_pattern_analysis_near_prioritized_signals_ast,
    "A3.31": handle_a3_31_applicable_semantic_data_flow_call_graph_analysis,
    "A3.32": handle_a3_32_applicable_domain_analysis_always_unavailable_today_no,
    "A3.33": handle_a3_33_correlate_traces_profiles_logs_samples_files_symbols,
    "A3.40": handle_a3_40_give_approved_generator_bounded_catalogue_context_require,
    "A3.41": handle_a3_41_resolve_every_citation_scope_exact_claim_support,
    "A3.50": handle_a3_50_independent_judge_checks_symptom_location_wording_contradictions,
    "A3.51": handle_a3_51_assign_observation_hypothesis_verified_cause_maturity_never,
    "A3.60": handle_a3_60_generate_materially_different_strategies_tied_eligible_findings,
    "A3.61": handle_a3_61_define_ordered_phases_exactly_one_logical_treatment,
    "A3.62": handle_a3_62_assess_every_criterion_guardrail_label_measured_versus,
    "A3.63": handle_a3_63_capture_concrete_pros_cons_prerequisites_effort_uncertainty,
    "A3.64": handle_a3_64_bind_validation_commands_protocol_expected_movement_stop,
    "A3.70": handle_a3_70_resolve_every_path_symbol_config_prompt_dependency,
    "A3.80": handle_a3_80_determine_risk_blast_radius_reversibility_uncertainty_migration,
    "A3.81": handle_a3_81_apply_evidence_maturity_diversity_coverage_treatment_rollback,
    "A3.82": handle_a3_82_revise_failed_dimensions_preserve_attempts_enforce_retry,
    "A3.90": handle_a3_90_seal_all_eligible_ineligible_strategies_quality_provenance,
}

__all__ = ["NODE_HANDLERS"]

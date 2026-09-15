from .nodes.s01_10_freeze_decision_context_pin_portfolio_and_request_digests import (
    handle_s01_10_freeze_decision_context_pin_portfolio_and_request_digests,
)
from .nodes.s01_20_exclude_ineligible_unreversible_unresolved_or_reverted_strategies import (
    handle_s01_20_exclude_ineligible_unreversible_unresolved_or_reverted_strategies,
)
from .nodes.s01_30_normalize_benefit_effort_reversibility_and_risk_to_unit_scale import (
    handle_s01_30_normalize_benefit_effort_reversibility_and_risk_to_unit_scale,
)
from .nodes.s01_40_weight_benefit_by_criterion_and_compute_total_score import (
    handle_s01_40_weight_benefit_by_criterion_and_compute_total_score,
)
from .nodes.s01_50_rank_by_total_score_with_risk_tier_tiebreak import (
    handle_s01_50_rank_by_total_score_with_risk_tier_tiebreak,
)
from .nodes.s01_60_sensitivity_sweep_flags_close_calls_on_weight_change import (
    handle_s01_60_sensitivity_sweep_flags_close_calls_on_weight_change,
)
from .nodes.s01_70_decide_if_risk_confidence_or_sensitivity_requires_approval import (
    handle_s01_70_decide_if_risk_confidence_or_sensitivity_requires_approval,
)
from .nodes.s01_80_seal_ranking_result_then_auto_select_or_halt_for_approval import (
    handle_s01_80_seal_ranking_result_then_auto_select_or_halt_for_approval,
)
from .nodes.s01_90_seal_selected_solution_after_approval import (
    handle_s01_90_seal_selected_solution_after_approval,
)

NODE_HANDLERS = {
    "S01.10": handle_s01_10_freeze_decision_context_pin_portfolio_and_request_digests,
    "S01.20": handle_s01_20_exclude_ineligible_unreversible_unresolved_or_reverted_strategies,
    "S01.30": handle_s01_30_normalize_benefit_effort_reversibility_and_risk_to_unit_scale,
    "S01.40": handle_s01_40_weight_benefit_by_criterion_and_compute_total_score,
    "S01.50": handle_s01_50_rank_by_total_score_with_risk_tier_tiebreak,
    "S01.60": handle_s01_60_sensitivity_sweep_flags_close_calls_on_weight_change,
    "S01.70": handle_s01_70_decide_if_risk_confidence_or_sensitivity_requires_approval,
    "S01.80": handle_s01_80_seal_ranking_result_then_auto_select_or_halt_for_approval,
    "S01.90": handle_s01_90_seal_selected_solution_after_approval,
}

__all__ = ["NODE_HANDLERS"]

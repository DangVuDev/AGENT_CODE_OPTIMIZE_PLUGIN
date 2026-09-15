from .nodes.s02_10_revalidate_treatment_scope_against_pinned_snapshot import (
    handle_s02_10_revalidate_treatment_scope_against_pinned_snapshot,
)
from .nodes.s02_20_discover_real_test_dependencies_by_filename_convention import (
    handle_s02_20_discover_real_test_dependencies_by_filename_convention,
)
from .nodes.s02_30_draft_ordered_phases_and_tasks_via_generator_model_call import (
    handle_s02_30_draft_ordered_phases_and_tasks_via_generator_model_call,
)
from .nodes.s02_40_materialize_and_validate_draft_phases import (
    handle_s02_40_materialize_and_validate_draft_phases,
)
from .nodes.s02_50_materialize_and_validate_draft_tasks import (
    handle_s02_50_materialize_and_validate_draft_tasks,
)
from .nodes.s02_60_check_every_criterion_covered_by_done_criteria import (
    handle_s02_60_check_every_criterion_covered_by_done_criteria,
)
from .nodes.s02_70_check_every_implementation_phase_has_rollback_command import (
    handle_s02_70_check_every_implementation_phase_has_rollback_command,
)
from .nodes.s02_80_independent_critic_reviews_omissions_and_blast_radius import (
    handle_s02_80_independent_critic_reviews_omissions_and_blast_radius,
)
from .nodes.s02_81_deterministically_validate_and_seal_plan_and_tasklist import (
    handle_s02_81_deterministically_validate_and_seal_plan_and_tasklist,
)
from .nodes.s02_90_approve_and_seal_or_halt_for_owner_decision import (
    handle_s02_90_approve_and_seal_or_halt_for_owner_decision,
)

NODE_HANDLERS = {
    "S02.10": handle_s02_10_revalidate_treatment_scope_against_pinned_snapshot,
    "S02.20": handle_s02_20_discover_real_test_dependencies_by_filename_convention,
    "S02.30": handle_s02_30_draft_ordered_phases_and_tasks_via_generator_model_call,
    "S02.40": handle_s02_40_materialize_and_validate_draft_phases,
    "S02.50": handle_s02_50_materialize_and_validate_draft_tasks,
    "S02.60": handle_s02_60_check_every_criterion_covered_by_done_criteria,
    "S02.70": handle_s02_70_check_every_implementation_phase_has_rollback_command,
    "S02.80": handle_s02_80_independent_critic_reviews_omissions_and_blast_radius,
    "S02.81": handle_s02_81_deterministically_validate_and_seal_plan_and_tasklist,
    "S02.90": handle_s02_90_approve_and_seal_or_halt_for_owner_decision,
}

__all__ = ["NODE_HANDLERS"]

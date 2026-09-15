from .nodes.c0_10_accept_manual_automatic_origin_retain_origin_audit import (
    handle_c0_10_accept_manual_automatic_origin_retain_origin_audit,
)
from .nodes.c0_20_validate_request_baseline_findings_solutions_quality_schemas import (
    handle_c0_20_validate_request_baseline_findings_solutions_quality_schemas,
)
from .nodes.c0_30_confirm_request_source_snapshot_evidence_solution_references import (
    handle_c0_30_confirm_request_source_snapshot_evidence_solution_references,
)
from .nodes.c0_40_require_b1_request_baseline_satisfy_a1_a2 import (
    handle_c0_40_require_b1_request_baseline_satisfy_a1_a2,
)
from .nodes.c0_50_reject_refresh_stale_source_evidence_approval_ownership import (
    handle_c0_50_reject_refresh_stale_source_evidence_approval_ownership,
)
from .nodes.c0_60_require_comparable_baseline_passed_a3_quality_least import (
    handle_c0_60_require_comparable_baseline_passed_a3_quality_least,
)
from .nodes.c0_70_produce_lane_neutral_references_handoff_event_step import (
    handle_c0_70_produce_lane_neutral_references_handoff_event_step,
)

NODE_HANDLERS = {
    "C0.10": handle_c0_10_accept_manual_automatic_origin_retain_origin_audit,
    "C0.20": handle_c0_20_validate_request_baseline_findings_solutions_quality_schemas,
    "C0.30": handle_c0_30_confirm_request_source_snapshot_evidence_solution_references,
    "C0.40": handle_c0_40_require_b1_request_baseline_satisfy_a1_a2,
    "C0.50": handle_c0_50_reject_refresh_stale_source_evidence_approval_ownership,
    "C0.60": handle_c0_60_require_comparable_baseline_passed_a3_quality_least,
    "C0.70": handle_c0_70_produce_lane_neutral_references_handoff_event_step,
}

__all__ = ["NODE_HANDLERS"]

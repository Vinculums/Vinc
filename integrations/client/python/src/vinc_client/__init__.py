"""Python client for the Vinc REST v1 API.

Design and scope: [[vinc.plan.framework-integration-packages-2026-09-28]].
"""
__version__ = "0.1.19"

from .client import AsyncVincClient, VincClient  # noqa: E402
from .agent_plan import (AgentPlan, AgentRun, afinish_run, aplan_agent, finish_run,  # noqa: E402
                         plan_agent, role_address)
from .agent_plan import (AgentPlan, AgentRun, afinish_run, aplan_agent, finish_run,  # noqa: E402
                         plan_agent, role_address)
from .context import (BOUNDARY, SOURCES_LINE, fence, format_answer, format_brief,  # noqa: E402
                      format_context, format_search)
from .episode import build_episode_fragment  # noqa: E402
from .eval_card import (CARD_TAG, build_eval_card_fragment, current_cards,  # noqa: E402
                        record_eval_card)
from .run import OUTCOMES, build_run_fragment, run_episode_id, trace_ref  # noqa: E402
from .shims import (HARNESS_CONCEPT, MECHANISMS, Shim, attached_shims,  # noqa: E402
                    build_shim_fragment, current_shims, read_runs, retirement_drafts,
                    shim_lines)
from .review import Finding, PersonOnlyRequest, ReviewOutput, UnparsedReview, parse_review  # noqa: E402
from .approval import (ApprovalDecision, ApprovalRequest, ApprovalResult, RoleOutput,  # noqa: E402
                       prepare_person_only, resolve_person_only)
from .output_gate import (GateResult, OutputGate, OutputGateStopped, ToolProof,  # noqa: E402
                          preserve_required_route, sources_line)
from .package import build_package_fragment, format_package  # noqa: E402
from .handoff import check_package_handoff  # noqa: E402
from .prefetch import RolePrefetch  # noqa: E402
from .tool_policy import RoleToolPolicy  # noqa: E402
from .role_policy import RolePolicy  # noqa: E402
from .errors import (VincAuthError, VincError, VincInvalidArgument, VincNotFound,  # noqa: E402
                     VincPartialWrite, VincQuotaExceeded, VincRoleChanged, VincUnavailable)

__all__ = [
    "__version__", "VincClient", "AsyncVincClient",
    "BOUNDARY", "SOURCES_LINE", "fence", "format_answer", "format_brief", "format_context", "format_search", "build_episode_fragment",
    "OUTCOMES", "build_run_fragment", "run_episode_id", "trace_ref",
    "CARD_TAG", "build_eval_card_fragment", "current_cards", "record_eval_card",
    "HARNESS_CONCEPT", "MECHANISMS", "Shim", "attached_shims", "build_shim_fragment",
    "current_shims", "read_runs", "retirement_drafts", "shim_lines",
    "VincError", "VincAuthError", "VincNotFound", "VincInvalidArgument",
    "VincQuotaExceeded", "VincUnavailable", "VincPartialWrite", "VincRoleChanged",
    "Finding", "PersonOnlyRequest", "ReviewOutput", "UnparsedReview", "parse_review",
    "RoleOutput", "ApprovalRequest", "ApprovalDecision", "ApprovalResult",
    "prepare_person_only", "resolve_person_only",
    "GateResult", "OutputGate", "OutputGateStopped", "ToolProof", "preserve_required_route", "sources_line",
    "build_package_fragment", "format_package", "check_package_handoff",
    "RolePrefetch", "RoleToolPolicy", "RolePolicy",
    "AgentPlan", "AgentRun", "plan_agent", "aplan_agent", "finish_run", "afinish_run", "role_address",
]

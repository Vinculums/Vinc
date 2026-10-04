"""Python client for the Vinc REST v1 API.

Design and scope: [[vinc.plan.framework-integration-packages-2026-09-28]].
"""
__version__ = "0.1.0"

from .client import AsyncVincClient, VincClient  # noqa: E402
from .context import BOUNDARY, fence, format_answer, format_brief, format_search  # noqa: E402
from .episode import build_episode_fragment  # noqa: E402
from .review import Finding, PersonOnlyRequest, ReviewOutput, UnparsedReview, parse_review  # noqa: E402
from .output_gate import (GateResult, OutputGate, OutputGateStopped, ToolProof,  # noqa: E402
                          preserve_required_route)
from .errors import (VincAuthError, VincError, VincInvalidArgument, VincNotFound,  # noqa: E402
                     VincPartialWrite, VincQuotaExceeded, VincUnavailable)

__all__ = [
    "__version__", "VincClient", "AsyncVincClient",
    "BOUNDARY", "fence", "format_answer", "format_brief", "format_search", "build_episode_fragment",
    "VincError", "VincAuthError", "VincNotFound", "VincInvalidArgument",
    "VincQuotaExceeded", "VincUnavailable", "VincPartialWrite",
    "Finding", "PersonOnlyRequest", "ReviewOutput", "UnparsedReview", "parse_review",
    "GateResult", "OutputGate", "OutputGateStopped", "ToolProof", "preserve_required_route",
]

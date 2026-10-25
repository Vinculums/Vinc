"""Vinc for Microsoft Agent Framework.

Design and scope: [[vinc.plan.framework-integration-packages-2026-09-28]].
"""
__version__ = "0.1.11"

from .provider import (PackageProvider, VincContextProvider, create_vinc_tools,  # noqa: E402
                       record_episode, record_run, write_package, prefetch)
from .review import run_review  # noqa: E402
from .approval import VincPersonOnlyExecutor  # noqa: E402
from vinc_client import ReviewOutput, UnparsedReview  # noqa: E402
from .output_gate import VincOutputGateMiddleware  # noqa: E402

__all__ = ["__version__", "VincContextProvider", "create_vinc_tools", "record_episode",
           "run_review", "ReviewOutput", "UnparsedReview", "VincOutputGateMiddleware",
           "VincPersonOnlyExecutor", "PackageProvider", "write_package", "prefetch", "record_run"]

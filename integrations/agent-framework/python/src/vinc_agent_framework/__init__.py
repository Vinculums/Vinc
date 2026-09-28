"""Vinc for Microsoft Agent Framework.

Design and scope: [[vinc.plan.framework-integration-packages-2026-09-28]].
"""
__version__ = "0.1.0"

from .provider import VincContextProvider, create_vinc_tools, record_episode  # noqa: E402

__all__ = ["__version__", "VincContextProvider", "create_vinc_tools", "record_episode"]

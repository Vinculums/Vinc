"""Vinc for Microsoft Agent Framework.

Design and scope: [[vinc.plan.framework-integration-packages-2026-09-28]].
"""
__version__ = "0.1.0"

from .provider import (PackageProvider, VincContextProvider, create_vinc_tools,  # noqa: E402
                       record_episode, write_package)

__all__ = ["__version__", "VincContextProvider", "PackageProvider", "create_vinc_tools",
           "record_episode", "write_package"]

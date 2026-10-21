"""Vinc SDK. Framework dependencies are loaded only by their integration."""
from .sdk import *  # noqa: F403
from .sdk import AsyncVincClient, VincClient, __all__ as _sdk_exports

__version__ = "0.1.4"
Vinc = VincClient
AsyncVinc = AsyncVincClient
__all__ = [*_sdk_exports, "Vinc", "AsyncVinc"]

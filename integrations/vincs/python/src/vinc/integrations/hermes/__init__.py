"""Vinc for Hermes Agent: a memory provider Hermes loads through an entry point.

Install with ``pip install "vincs[hermes]"`` into the environment Hermes runs in,
then choose ``vinc`` in ``hermes memory setup``.
"""
from .provider import VincMemoryProvider, parse_config, read_config, register

__all__ = ["VincMemoryProvider", "register", "parse_config", "read_config"]

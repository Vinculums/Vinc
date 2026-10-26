"""Vinc for CrewAI: an agent built from a Vinc role, its read tools, the output gate as a task
guardrail and person-only actions through CrewAI's human input step.

Install with ``pip install "vincs[crewai]"``. Nothing here writes to Vinc on its own:
``record_run``, ``record_episode`` and ``write_package`` write because you call them.
Design: [[vinc.plan.agent-harness-work-order-2026-10-25 §3]].
"""
from .agent import VincAgent, create_agent, role_fields
from .approval import VincApprovalRejected, VincPersonOnlyInput
from .build import VincCrewAgent, abuild_agent, build_agent
from .helpers import (aget_vinc_package_context, aprefetch, arecord_episode, arecord_run,
                      awrite_package, get_vinc_package_context, prefetch, record_episode,
                      record_run, write_package)
from .output_gate import create_output_guardrail
from .tools import create_vinc_tools

__all__ = ["VincAgent", "create_agent", "role_fields", "build_agent", "abuild_agent",
           "VincCrewAgent", "create_vinc_tools",
           "create_output_guardrail", "VincPersonOnlyInput", "VincApprovalRejected",
           "prefetch", "aprefetch", "get_vinc_package_context", "aget_vinc_package_context",
           "record_episode", "arecord_episode", "record_run", "arecord_run",
           "write_package", "awrite_package"]

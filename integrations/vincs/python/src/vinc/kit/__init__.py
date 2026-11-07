"""The run kit: run a role on your own machine with your own model, and keep the record and trace.

``from vinc.kit import run_role`` or ``vinc agent run``. Needs an adapter extra
(``pip install "vincs[langgraph]"`` and so on). Nothing here calls a model of Vinc's.
Design: [[vinc.plan.agent-harness-work-order-2026-10-25]].
"""
from .run import ADAPTERS, DEFAULT_TRACE_DIR, KitError, KitRun, model_family, resolve_model, run_role
from .evalrun import EvalRun, run_suite
from .suite import Suite, SuiteError, load_suite
from .trace import BudgetExceeded, Meter, Trace

__all__ = ["run_role", "KitRun", "KitError", "ADAPTERS", "DEFAULT_TRACE_DIR", "resolve_model",
           "model_family", "Trace", "Meter", "BudgetExceeded", "run_suite", "EvalRun", "Suite",
           "SuiteError", "load_suite"]

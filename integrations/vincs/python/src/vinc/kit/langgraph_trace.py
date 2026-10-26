"""A LangChain callback handler that writes the trace and enforces the budget (LangGraph only)."""
from __future__ import annotations

from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

from .trace import Meter, Trace


def _message(message: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"type": getattr(message, "type", type(message).__name__),
                           "content": getattr(message, "content", str(message))}
    calls = getattr(message, "tool_calls", None)
    if calls:
        out["tool_calls"] = calls
    return out


class LangGraphTrace(BaseCallbackHandler):
    """A LangChain callback handler that records the trace and enforces the budget.

    ``raise_error`` is true so that a :class:`BudgetExceeded` raised before a call stops the
    graph instead of being logged and ignored.
    """

    raise_error = True

    def __init__(self, trace: Trace, meter: Meter) -> None:
        self.trace, self.meter = trace, meter

    def on_chat_model_start(self, serialized, messages, **kwargs) -> None:  # type: ignore[no-untyped-def]
        self.meter.before_model()
        self.trace.add({"t": "model_in", "messages": [[_message(m) for m in batch] for batch in messages]})

    def on_llm_end(self, response, **kwargs) -> None:  # type: ignore[no-untyped-def]
        generations = [[_message(getattr(g, "message", None) or g) if hasattr(g, "message")
                        else {"type": "text", "content": getattr(g, "text", "")} for g in batch]
                       for batch in getattr(response, "generations", [])]
        tin = tout = 0
        for batch in getattr(response, "generations", []):
            for g in batch:
                usage = getattr(getattr(g, "message", None), "usage_metadata", None) or {}
                tin += usage.get("input_tokens", 0) or 0
                tout += usage.get("output_tokens", 0) or 0
        self.meter.after_model(tin, tout)
        self.trace.add({"t": "model_out", "generations": generations})

    def on_llm_error(self, error, **kwargs) -> None:  # type: ignore[no-untyped-def]
        self.trace.add({"t": "model_error", "error": f"{type(error).__name__}: {error}"})

    def on_tool_start(self, serialized, input_str, **kwargs) -> None:  # type: ignore[no-untyped-def]
        self.meter.before_tool()
        self.trace.add({"t": "tool_call", "name": (serialized or {}).get("name"), "input": input_str})

    def on_tool_end(self, output, **kwargs) -> None:  # type: ignore[no-untyped-def]
        self.trace.add({"t": "tool_result", "output": getattr(output, "content", output)})

    def on_tool_error(self, error, **kwargs) -> None:  # type: ignore[no-untyped-def]
        self.trace.add({"t": "tool_error", "error": f"{type(error).__name__}: {error}"})

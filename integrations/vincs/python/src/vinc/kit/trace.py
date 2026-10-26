"""The raw trace of one run, and the budget guard that watches it.

A trace is the model's input and output and each tool call with its result, one JSON object per
line, never a summary. The file is named by the sha256 of its bytes and the run record keeps only
that hash, the size and the name, so two runs with the same input leave byte-identical files.
Times are not written into the trace for that reason.

The guard stops a run that is over its role's budget for ``model_calls``, ``tokens`` or
``wall_seconds``. ``tool_calls`` is already held by the role's tool policy, and ``cost_usd`` is
not guarded because the kit has no price list: a model's price is the person's to know.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Mapping


#: 예산 키 가운데 이 가드가 직접 세는 것.
GUARDED = ("model_calls", "tokens", "wall_seconds")


class BudgetExceeded(RuntimeError):
    """The run went past a budget key of its role. ``key`` is ``model_calls``, ``tokens`` or
    ``wall_seconds``."""

    def __init__(self, key: str, limit: float) -> None:
        super().__init__(f"the role's budget for {key} ({limit:g}) is used up")
        self.key, self.limit = key, limit


class Trace:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def add(self, event: Mapping[str, Any]) -> None:
        self.events.append(dict(event))

    def to_bytes(self) -> bytes:
        return b"".join(json.dumps(e, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
                        + b"\n" for e in self.events)

    def sha256(self) -> str:
        return hashlib.sha256(self.to_bytes()).hexdigest()

    def write(self, directory: str | Path) -> Path:
        folder = Path(directory)
        folder.mkdir(parents=True, exist_ok=True)
        data = self.to_bytes()
        path = folder / f"{hashlib.sha256(data).hexdigest()}.jsonl"
        if not path.exists():
            path.write_bytes(data)
        return path


class Meter:
    """What one run has used, against the role's budget (``AgentPlan.budget``)."""

    def __init__(self, budget: Mapping[str, float] | None = None, clock=time.monotonic) -> None:
        self.budget = dict(budget or {})
        self._clock, self._start = clock, clock()
        self.model_calls = self.tool_calls = self.tokens_in = self.tokens_out = 0

    def _over(self, key: str, used: float) -> None:
        limit = self.budget.get(key)
        if limit is not None and used > limit:
            raise BudgetExceeded(key, limit)

    def before_model(self) -> None:
        """Called before every model call: the call about to start counts."""
        self._over("wall_seconds", self._clock() - self._start)
        self._over("tokens", self.tokens_in + self.tokens_out)
        self._over("model_calls", self.model_calls + 1)
        self.model_calls += 1

    def before_tool(self) -> None:
        self._over("wall_seconds", self._clock() - self._start)
        self.tool_calls += 1

    def after_model(self, tokens_in: int, tokens_out: int) -> None:
        self.tokens_in += max(0, int(tokens_in or 0))
        self.tokens_out += max(0, int(tokens_out or 0))

"""A LangChain chat model that runs on the person's Claude subscription through headless Claude Code.

``--model claude-code:<model id>`` makes ``vinc agent run`` and ``vinc eval run`` call
``claude -p`` once per model call, with the login already on this machine, so a Max subscription
carries the run and no API key is involved. It is for a person running their own account on their own
machine; a subscription is not a service to share.

How a call is made, and why:

- ``ANTHROPIC_API_KEY`` and ``ANTHROPIC_AUTH_TOKEN`` are removed from the child's environment. With a key
  set, Claude Code bills the API, not the subscription, and ``--bare`` does not read the login.
- The call is isolated: an empty working directory, no tools, no settings, no MCP servers, no session
  file, and a pinned model id. The conversation goes in on stdin and the system text in a temporary
  file, because a role's reading is far longer than a command line allows.
- Tools are off, so the agent answers from what the role already read. A model that cannot call tools
  gets ``vinc_brief`` and ``vinc_search`` as plain text it never uses.
- Each call reads the stream's ``rate_limit_event``. At five-hour use 0.80 or seven-day use 0.90 the
  next call is refused with :class:`SubscriptionPaused` and the time the window resets, instead of
  spending the rest of the week. The list-price cost Claude Code reports is kept in ``meter``.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from .run import KitError

FIVE_HOUR_STOP = 0.80
SEVEN_DAY_STOP = 0.90
_DROPPED_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


class SubscriptionPaused(KitError):
    """The subscription's window is close to full. Nothing was sent; the message says until when."""


def _when(epoch: Any) -> str:
    try:
        return _dt.datetime.fromtimestamp(int(epoch)).astimezone().strftime("%Y-%m-%d %H:%M %Z")
    except (TypeError, ValueError, OverflowError, OSError):
        return "its reset time"


def render(messages: list[BaseMessage]) -> tuple[str, str]:
    """``(system text, conversation)``: system messages apart, the rest as a plain transcript."""
    system, turns = [], []
    for message in messages:
        text = message.content if isinstance(message.content, str) else json.dumps(
            message.content, ensure_ascii=False)
        kind = getattr(message, "type", "human")
        if kind == "system":
            system.append(text)
        elif kind == "ai":
            turns.append(f"Assistant:\n{text}")
        elif kind == "tool":
            turns.append(f"Tool result:\n{text}")
        else:
            turns.append(text if len(messages) - len(system) == 1 else f"Human:\n{text}")
    return "\n\n".join(system), "\n\n".join(turns)


def parse_stream(stdout: str) -> dict[str, Any]:
    """The answer, usage and meter out of a ``stream-json`` run. Raises ``KitError`` if there is none."""
    out: dict[str, Any] = {"text": None, "usage": {}, "meter": {}, "cost_usd": None, "source": None}
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            out["source"] = event.get("apiKeySource")
        elif kind == "rate_limit_event":
            windows = (event.get("rate_limit_info") or {}).get("unifiedWindows") or {}
            out["meter"].update({k: v for k, v in windows.items() if isinstance(v, dict)})
        elif kind == "result":
            if event.get("subtype") != "success" or event.get("is_error"):
                raise KitError("Claude Code ended the call without an answer: "
                               f"{str(event.get('result') or event.get('subtype'))[:200]}")
            out["text"] = event.get("result")
            out["usage"] = event.get("usage") or {}
            out["cost_usd"] = event.get("total_cost_usd")
    if not isinstance(out["text"], str):
        raise KitError("Claude Code returned no result event")
    return out


class ClaudeCodeChat(BaseChatModel):
    """One model call is one headless ``claude -p`` run on the subscription."""

    model: str
    claude_bin: str = "claude"
    timeout_s: int = 900
    five_hour_stop: float = FIVE_HOUR_STOP
    seven_day_stop: float = SEVEN_DAY_STOP
    meter: dict = Field(default_factory=dict)
    list_price_usd: float = 0.0

    @property
    def _llm_type(self) -> str:
        return "claude-code"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ClaudeCodeChat":
        return self

    def _pause_reason(self) -> str | None:
        five, seven = self.meter.get("five_hour") or {}, self.meter.get("seven_day") or {}
        if float(five.get("utilization") or 0) >= self.five_hour_stop:
            return f"five-hour use is {five['utilization']:.2f}; it resets {_when(five.get('resetsAt'))}"
        if float(seven.get("utilization") or 0) >= self.seven_day_stop:
            return f"seven-day use is {seven['utilization']:.2f}; it resets {_when(seven.get('resetsAt'))}"
        return None

    def _generate(self, messages: list[BaseMessage], stop: list[str] | None = None,
                  run_manager: Any = None, **kwargs: Any) -> ChatResult:
        reason = self._pause_reason()
        if reason:
            raise SubscriptionPaused(f"paused to keep the subscription from running out: {reason}")
        system, conversation = render(messages)
        env = {k: v for k, v in os.environ.items() if k not in _DROPPED_ENV}
        with tempfile.TemporaryDirectory(prefix="vinc-claude-") as work:
            command = [self.claude_bin, "-p", "--output-format", "stream-json", "--verbose",
                       "--tools", "", "--setting-sources", "", "--strict-mcp-config",
                       "--no-session-persistence", "--model", self.model]
            if system:
                path = Path(work) / "system.md"
                path.write_text(system, encoding="utf-8")
                command += ["--system-prompt-file", str(path)]
            try:
                done = subprocess.run(command, input=conversation, text=True, encoding="utf-8",
                                      capture_output=True, cwd=work, env=env, timeout=self.timeout_s)
            except FileNotFoundError:
                raise KitError("Claude Code is not installed on this machine (no `claude` command)") from None
            except subprocess.TimeoutExpired:
                raise KitError(f"Claude Code did not answer within {self.timeout_s} seconds") from None
        if done.returncode != 0 and not done.stdout.strip():
            raise KitError(f"Claude Code failed: {done.stderr.strip()[:300] or done.returncode}")
        got = parse_stream(done.stdout)
        if got["source"] not in (None, "none"):
            raise KitError("Claude Code reported an API key as its login (apiKeySource="
                           f"{got['source']!r}), so this call would be billed to the API, not the "
                           "subscription. Remove the key from this environment.")
        self.meter.update(got["meter"])
        if isinstance(got["cost_usd"], (int, float)):
            self.list_price_usd = round(self.list_price_usd + float(got["cost_usd"]), 8)
        usage = got["usage"]
        tin = sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens",
                                                    "cache_read_input_tokens"))
        tout = int(usage.get("output_tokens") or 0)
        message = AIMessage(content=got["text"], usage_metadata={
            "input_tokens": tin, "output_tokens": tout, "total_tokens": tin + tout})
        return ChatResult(generations=[ChatGeneration(message=message)])

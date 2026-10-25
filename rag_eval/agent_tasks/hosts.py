"""호스트 셋 — 같은 모델 실행기와 같은 비교군 표면을 각자의 루프로 돌린다.

- ``mcp``: 이 모듈의 맨 루프. 모델 → 도구 호출 → 결과 → 모델. MCP 도구를 JSON-RPC 로 부른다.
- ``langgraph``: LangChain ``create_agent``(LangGraph 위). A3 는 ``vinc_langgraph.VincContextMiddleware``.
- ``agent-framework``: Microsoft Agent Framework ``Agent``. A3 는 ``vinc_agent_framework.VincContextProvider``.

세 호스트 모두 모델 호출은 :class:`Stepper` 하나를 지난다. 그래서 모델이 받은 입력(프레임워크가
붙인 시스템 블록 포함)이 같은 꼴로 원본 기록에 남고, 걸음 상한도 같은 자리에서 걸린다.
도구 호출 상한은 어댑터의 상한과 같은 수·같은 문장이고 A2 의 모든 도구 호출을 합쳐 센다.
근거: [[decision:vin318-parity-is-judged-on-three-runs-with-a-shared-tool-call-cap-2026-10-07]]

헤드리스 구독 실행처럼 루프를 통째로 쥐는 실행기는 :class:`Host` 를 하나 더 구현한다. ``run`` 이
같은 :class:`RunOutput` 과 원본 기록을 내면 채점·기록·지표는 손대지 않는다.
"""
from __future__ import annotations

import asyncio
import importlib.metadata as md
import json
import threading
from dataclasses import dataclass, field
from typing import Protocol

from rag_eval.agent_tasks import record
from rag_eval.agent_tasks.arms import A3_SETTINGS, Surface
from rag_eval.agent_tasks.models import ModelRunner, ToolCall, Turn

HOSTS = ("mcp", "langgraph", "agent-framework")
#: 어댑터(`vinc_client.tools`)의 상한과 같은 수와 같은 문장.
TOOL_CALLS_PER_RUN = 4
CAP_MESSAGE = "Vinc tool call cap reached ({limit} per run); answer from what you already have."


@dataclass
class RunOutput:
    final_text: str
    steps: int = 0
    tool_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    model_error: str = ""
    model_versions: list[str] = field(default_factory=list)
    host_error: str = ""


class Host(Protocol):
    name: str

    def describe(self) -> dict: ...

    def run(self, *, prompt: str, system: str, surface: Surface, model: ModelRunner,
            trace: record.Trace, max_steps: int, cap: int) -> RunOutput: ...


def _versions(*pkgs: str) -> dict:
    out = {}
    for p in pkgs:
        try:
            out[p] = md.version(p)
        except md.PackageNotFoundError:
            out[p] = ""
    return out


class Stepper:
    """모델 한 걸음. 입력·출력을 원본 기록에 남기고, 상한에 닿으면 도구 호출을 거둔다."""

    def __init__(self, model: ModelRunner, trace: record.Trace, max_steps: int) -> None:
        self.model, self.trace, self.max_steps = model, trace, max_steps
        self.out = RunOutput(final_text="")

    def step(self, messages: list[dict], tools: list[dict]) -> Turn:
        self.out.steps += 1
        turn = self.model.step(messages, tools)
        forced = False
        if turn.tool_calls and self.out.steps >= self.max_steps:
            turn = Turn(text=turn.text, tokens_in=turn.tokens_in, tokens_out=turn.tokens_out,
                        model_version=turn.model_version, error=turn.error)
            forced = True
        self.out.tokens_in += turn.tokens_in
        self.out.tokens_out += turn.tokens_out
        if turn.model_version and turn.model_version not in self.out.model_versions:
            self.out.model_versions.append(turn.model_version)
        if turn.error:
            self.out.model_error = turn.error
        self.trace.add({"t": "model", "step": self.out.steps, "input": messages,
                        "tools": [t["name"] for t in tools],
                        "output": {"text": turn.text, "tool_calls": [
                            {"id": c.id, "name": c.name, "args": c.args} for c in turn.tool_calls]},
                        "tokens_in": turn.tokens_in, "tokens_out": turn.tokens_out,
                        "model_version": turn.model_version, "error": turn.error,
                        "forced_final": forced})
        return turn


class ToolRunner:
    """A2 도구 호출 하나. 상한을 세고, 결과 봉투를 분류해 원본 기록에 남긴다."""

    def __init__(self, surface: Surface, trace: record.Trace, cap: int, stepper: Stepper) -> None:
        self.surface, self.trace, self.cap, self.stepper = surface, trace, cap, stepper
        self.used = 0
        self._given: set[str] = set()
        # LangGraph 는 한 턴의 도구 호출을 스레드로 나눠 돌린다. 세는 것과 id 배정은 이 잠금 안에서.
        self._lock = threading.Lock()

    def next_id(self, name: str) -> str:
        """프레임워크가 함수에 넘기지 않는 호출 id: 마지막 모델 출력의 그 이름 호출 중 아직
        배정하지 않은 첫 것."""
        with self._lock:
            model = next((e for e in reversed(self.trace.events) if e["t"] == "model"), None)
            for c in (model or {}).get("output", {}).get("tool_calls", []):
                if c["name"] == name and c["id"] not in self._given:
                    self._given.add(c["id"])
                    return c["id"]
        return ""

    def call(self, call_id: str, name: str, args: dict) -> str:
        with self._lock:
            self.stepper.out.tool_calls += 1
            capped = self.used >= self.cap
            if not capped:
                self.used += 1
            step = self.stepper.out.steps
        if capped:
            text, kind, env = CAP_MESSAGE.format(limit=self.cap), "cap", None
        else:
            env = self.surface.mcp.call(name, args)
            text, kind = record.envelope_text(env), record.classify(env)
        with self._lock:
            self.trace.add({"t": "tool", "step": step, "id": call_id, "name": name,
                            "args": args, "result": text,
                            "is_error": bool(env and env.get("isError")), "kind": kind})
        return text


def _system(system: str, surface: Surface) -> str:
    return f"{system}\n\n{surface.context}" if surface.context else system


class McpHost:
    """맨 MCP 루프. 도구는 표면이 준 MCP 도구 그대로, A3 블록은 vinc-client 의 같은 함수로."""
    name = "mcp"

    def describe(self) -> dict:
        return {"host": self.name, "packages": _versions("vinc-client")}

    def run(self, *, prompt, system, surface, model, trace, max_steps, cap) -> RunOutput:
        stepper = Stepper(model, trace, max_steps)
        tools = ToolRunner(surface, trace, cap, stepper)
        sys_text = _system(system, surface)
        if surface.arm == "A3":
            from vinc_client import VincClient   # noqa: PLC0415
            with VincClient(surface.vinc.key, **surface.vinc.client_args()) as client:
                block = client.get_context(prompt, **A3_SETTINGS)
            if block:
                sys_text = f"{sys_text}\n{block}"
        messages: list[dict] = [{"role": "system", "content": sys_text},
                                {"role": "user", "content": prompt}]
        while True:
            turn = stepper.step(messages, surface.tools)
            if not turn.tool_calls:
                stepper.out.final_text = turn.text
                return stepper.out
            messages.append({"role": "assistant", "content": turn.text, "tool_calls": [
                {"id": c.id, "name": c.name, "args": c.args} for c in turn.tool_calls]})
            for c in turn.tool_calls:
                messages.append({"role": "tool", "tool_call_id": c.id, "name": c.name,
                                 "content": tools.call(c.id, c.name, c.args)})


# ── LangGraph ────────────────────────────────────────────────────────────────

def _lc_text(content) -> str:
    if isinstance(content, str):
        return content
    return "".join(p.get("text") or "" if isinstance(p, dict) else str(p) for p in content or [])


def _lc_neutral(messages) -> list[dict]:
    out = []
    for m in messages:
        kind = getattr(m, "type", "")
        if kind == "system":
            out.append({"role": "system", "content": _lc_text(m.content)})
        elif kind == "human":
            out.append({"role": "user", "content": _lc_text(m.content)})
        elif kind == "ai":
            row = {"role": "assistant", "content": _lc_text(m.content)}
            if getattr(m, "tool_calls", None):
                row["tool_calls"] = [{"id": c.get("id") or "", "name": c["name"],
                                      "args": c.get("args") or {}} for c in m.tool_calls]
            out.append(row)
        elif kind == "tool":
            out.append({"role": "tool", "tool_call_id": getattr(m, "tool_call_id", ""),
                        "name": getattr(m, "name", "") or "", "content": _lc_text(m.content)})
    return out


def _lc_model(stepper: Stepper):
    from langchain_core.language_models.chat_models import BaseChatModel   # noqa: PLC0415
    from langchain_core.messages import AIMessage                          # noqa: PLC0415
    from langchain_core.outputs import ChatGeneration, ChatResult          # noqa: PLC0415
    from langchain_core.utils.function_calling import convert_to_openai_tool  # noqa: PLC0415

    class HarnessChatModel(BaseChatModel):
        """LangChain 모델 자리에 :class:`Stepper` 를 앉힌다. 도구는 bind_tools 로 받는다."""

        @property
        def _llm_type(self) -> str:
            return "rag-eval-harness"

        def bind_tools(self, tools, **kwargs):
            specs = []
            for t in tools:
                fn = convert_to_openai_tool(t)["function"]
                specs.append({"name": fn["name"], "description": fn.get("description") or "",
                              "parameters": fn.get("parameters") or {}})
            return self.bind(harness_tools=specs, **kwargs)

        def _generate(self, messages, stop=None, **kwargs):
            turn = stepper.step(_lc_neutral(messages), kwargs.get("harness_tools") or [])
            msg = AIMessage(content=turn.text, tool_calls=[
                {"name": c.name, "args": c.args, "id": c.id, "type": "tool_call"}
                for c in turn.tool_calls])
            return ChatResult(generations=[ChatGeneration(message=msg)])

    return HarnessChatModel()


class LangGraphHost:
    name = "langgraph"

    def describe(self) -> dict:
        return {"host": self.name, "packages": _versions(
            "vinc-langgraph", "vinc-client", "langgraph", "langchain", "langchain-core")}

    def run(self, *, prompt, system, surface, model, trace, max_steps, cap) -> RunOutput:
        from langchain.agents import create_agent           # noqa: PLC0415
        from langchain_core.messages import HumanMessage    # noqa: PLC0415
        from langchain_core.tools import StructuredTool     # noqa: PLC0415
        stepper = Stepper(model, trace, max_steps)
        runner = ToolRunner(surface, trace, cap, stepper)

        def make(spec: dict):
            def fn(**kwargs):
                return runner.call(runner.next_id(spec["name"]), spec["name"], kwargs)
            return StructuredTool.from_function(func=fn, name=spec["name"],
                                                description=spec["description"],
                                                args_schema=spec["parameters"])
        middleware = []
        client = None
        if surface.arm == "A3":
            from vinc_client import VincClient                 # noqa: PLC0415
            from vinc_langgraph import VincContextMiddleware   # noqa: PLC0415
            client = VincClient(surface.vinc.key, **surface.vinc.client_args())
            middleware.append(VincContextMiddleware(
                client=client, cache_seconds=0, tool_calls_per_run=None, **A3_SETTINGS))
        try:
            agent = create_agent(_lc_model(stepper), tools=[make(s) for s in surface.tools],
                                 system_prompt=_system(system, surface), middleware=middleware)
            state = agent.invoke({"messages": [HumanMessage(prompt)]},
                                 {"recursion_limit": 2 * max_steps + 4})
        finally:
            if client is not None:
                client.close()
        last = [m for m in state["messages"] if getattr(m, "type", "") == "ai"]
        stepper.out.final_text = _lc_text(last[-1].content) if last else ""
        return stepper.out


# ── Microsoft Agent Framework ────────────────────────────────────────────────

def _af_neutral(instructions, messages) -> list[dict]:
    out = []
    names: dict[str, str] = {}
    if instructions:
        out.append({"role": "system", "content": str(instructions)})
    for m in messages:
        role = str(getattr(m, "role", "")).lower().rsplit(".", 1)[-1]
        calls, results, texts = [], [], []
        for c in getattr(m, "contents", None) or []:
            kind = getattr(c, "type", "")
            if kind == "function_call":
                args = c.arguments
                if isinstance(args, str):
                    try:
                        args = json.loads(args or "{}")
                    except json.JSONDecodeError:
                        args = {"_unparsed": args}
                calls.append({"id": c.call_id, "name": c.name, "args": dict(args or {})})
                names[c.call_id] = c.name
            elif kind == "function_result":
                results.append({"role": "tool", "tool_call_id": c.call_id,
                                "name": names.get(c.call_id, ""),
                                "content": _af_result_text(c.result)})
            elif kind == "text":
                texts.append(c.text or "")
        text = "".join(texts)
        if role == "system":
            out.append({"role": "system", "content": text})
        elif role == "user":
            out.append({"role": "user", "content": text})
        elif role == "assistant":
            row = {"role": "assistant", "content": text}
            if calls:
                row["tool_calls"] = calls
            out.append(row)
        out.extend(results)
    return out


def _af_result_text(result) -> str:
    if isinstance(result, str):
        return result
    if isinstance(result, list):
        return "".join(getattr(p, "text", None) or str(p) for p in result)
    return "" if result is None else str(result)


def _af_tool_spec(tool) -> dict:
    params = tool.parameters() if callable(getattr(tool, "parameters", None)) else getattr(tool, "parameters", {})
    return {"name": tool.name, "description": getattr(tool, "description", "") or "",
            "parameters": dict(params or {})}


class AgentFrameworkHost:
    name = "agent-framework"

    def describe(self) -> dict:
        return {"host": self.name, "packages": _versions(
            "vinc-agent-framework", "vinc-client", "agent-framework-core")}

    def run(self, *, prompt, system, surface, model, trace, max_steps, cap) -> RunOutput:
        return asyncio.run(self._run(prompt=prompt, system=system, surface=surface, model=model,
                                     trace=trace, max_steps=max_steps, cap=cap))

    async def _run(self, *, prompt, system, surface, model, trace, max_steps, cap) -> RunOutput:
        from agent_framework import (Agent, BaseChatClient, ChatMiddlewareLayer,  # noqa: PLC0415
                                     ChatResponse, Content, FunctionInvocationLayer,
                                     FunctionTool, Message)
        stepper = Stepper(model, trace, max_steps)
        runner = ToolRunner(surface, trace, cap, stepper)

        class HarnessChat(FunctionInvocationLayer, ChatMiddlewareLayer, BaseChatClient):
            async def _inner_get_response(self, *, messages, stream, options, **kwargs):  # type: ignore[override]
                specs = [_af_tool_spec(t) for t in options.get("tools") or []]
                turn = stepper.step(_af_neutral(options.get("instructions"), messages), specs)
                contents = [Content.from_function_call(c.id, c.name, arguments=c.args)
                            for c in turn.tool_calls] or [turn.text]
                return ChatResponse(messages=Message("assistant", contents))

        def make(spec: dict):
            def fn(**kwargs):
                return runner.call(runner.next_id(spec["name"]), spec["name"], kwargs)
            return FunctionTool(name=spec["name"], description=spec["description"], func=fn,
                                input_model=spec["parameters"])
        providers = []
        client = None
        if surface.arm == "A3":
            from vinc_agent_framework import VincContextProvider   # noqa: PLC0415
            from vinc_client import AsyncVincClient                # noqa: PLC0415
            client = AsyncVincClient(surface.vinc.key, **surface.vinc.client_args())
            providers.append(VincContextProvider(client, **A3_SETTINGS))
        try:
            agent = Agent(HarnessChat(), instructions=_system(system, surface),
                          tools=[make(s) for s in surface.tools] or None,
                          context_providers=providers or None)
            response = await agent.run(prompt)
        finally:
            if client is not None:
                await client.aclose()
        stepper.out.final_text = response.text or ""
        return stepper.out


def make(name: str) -> Host:
    if name == "mcp":
        return McpHost()
    if name == "langgraph":
        return LangGraphHost()
    if name == "agent-framework":
        return AgentFrameworkHost()
    raise ValueError(f"unknown host {name!r}; one of {HOSTS}")


__all__ = ["HOSTS", "Host", "RunOutput", "Stepper", "ToolRunner", "McpHost", "LangGraphHost",
           "AgentFrameworkHost", "make", "ToolCall"]

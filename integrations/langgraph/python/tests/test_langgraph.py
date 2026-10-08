from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.types import Command

from vinc_client import AsyncVincClient, VincAuthError, VincClient
from vinc_langgraph import (aget_vinc_context, build_system_message, create_vinc_tools,
                            get_vinc_context, last_user_text, record_episode)

RO_KEY = "vinc_ro_" + "a" * 32
SK_KEY = "vinc_sk_" + "b" * 32
RESOLVED = {
    "status": "resolved",
    "node": {"id": "decision:tokens-are-oklch", "title": "Colour tokens are stored as OKLCH"},
    "one_liner": "Tokens keep OKLCH so contrast can be checked per theme.",
    "grounds": ["decision:tokens-are-oklch"],
}
DOWN = (503, {"error": {"code": "upstream_unavailable", "message": "down"}})


class Routes:
    def __init__(self, routes: dict[tuple[str, str], Any]):
        self.routes = routes
        self.seen: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        answer = self.routes.get((request.method, request.url.path))
        if answer is None:
            return httpx.Response(404, json={"error": {"code": "route_not_found", "message": ""}})
        status, body = answer if isinstance(answer, tuple) else (200, answer)
        return httpx.Response(status, json=body)

    def paths(self) -> list[str]:
        return [r.url.path for r in self.seen]


def sync_client(routes: Routes, key: str = RO_KEY) -> VincClient:
    return VincClient(key, transport=httpx.MockTransport(routes))


def async_client(routes: Routes, key: str = RO_KEY) -> AsyncVincClient:
    return AsyncVincClient(key, transport=httpx.MockTransport(routes))


class RecordingModel(BaseChatModel):
    """Answers "ok" and keeps every message list it was given."""

    calls: list = []

    @property
    def _llm_type(self) -> str:
        return "recording"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.calls.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])

    def system_text(self) -> str:
        first = self.calls[-1][0]
        return first.text if isinstance(first, SystemMessage) else ""


# ── 헬퍼 ───────────────────────────────────────────────────────────────────────

def test_last_user_text_takes_the_newest_human_message():
    msgs = [HumanMessage("first"), AIMessage("answer"), HumanMessage("the follow-up")]
    assert last_user_text(msgs) == "the follow-up"
    assert last_user_text("  plain  ") == "plain"
    assert last_user_text([AIMessage("only ai")]) == ""


def test_get_vinc_context_sync_and_async():
    routes = Routes({("POST", "/v1/brief"): RESOLVED})
    assert "decision:tokens-are-oklch" in get_vinc_context(sync_client(routes),
                                                           [HumanMessage("why oklch tokens")])
    block = asyncio.run(aget_vinc_context(async_client(routes), "why oklch tokens"))
    assert "decision:tokens-are-oklch" in block


def test_context_is_none_when_vinc_is_down():
    routes = Routes({("POST", "/v1/brief"): DOWN})
    assert get_vinc_context(sync_client(routes), "why oklch tokens") is None


def test_build_system_message_appends_and_keeps():
    assert build_system_message(None) is None
    base = SystemMessage(content="You review design changes.")
    assert build_system_message(None, base) is base
    fresh = build_system_message("BLOCK")
    assert fresh.text == "BLOCK"
    joined = build_system_message("BLOCK", "You review design changes.")
    assert "You review design changes." in joined.text and "BLOCK" in joined.text


def test_plain_system_context_preserves_string_and_message_metadata():
    base = SystemMessage(content="Review.", id="system-id", name="reviewer",
                         additional_kwargs={"trusted": True})
    joined = build_system_message("DATA", base)
    assert joined.content == "Review.\nDATA"
    assert joined.id == base.id and joined.name == base.name
    assert joined.additional_kwargs == base.additional_kwargs
    assert base.content == "Review."
    assert build_system_message("DATA", "").content == "DATA"


def test_structured_system_context_keeps_blocks_and_metadata():
    blocks = [{"type": "text", "text": "Review.", "cache_control": {"type": "ephemeral"}}]
    base = SystemMessage(content=blocks, id="structured-id")
    joined = build_system_message("DATA", base)
    assert isinstance(joined.content, list)
    assert joined.content[-1] == {"type": "text", "text": "DATA"}
    assert joined.content[0]["cache_control"] == {"type": "ephemeral"}
    assert joined.id == base.id and base.content == blocks


# ── 도구 ───────────────────────────────────────────────────────────────────────

def test_tools_are_read_only_and_work_sync_and_async():
    routes = Routes({("POST", "/v1/brief"): RESOLVED,
                     ("POST", "/v1/search"): {"rows": [], "node_hits": [
                         {"id": "concept:spacing", "title": "Spacing", "kind": "concept"}]}})
    tools = {t.name: t for t in create_vinc_tools(sync_client(routes), async_client(routes))}
    assert sorted(tools) == ["vinc_brief", "vinc_search"]
    assert "decision:tokens-are-oklch" in tools["vinc_brief"].invoke({"query": "tokens"})
    out = asyncio.run(tools["vinc_search"].ainvoke({"query": "spacing", "limit": 2}))
    assert "concept:spacing" in out and json.loads(routes.seen[-1].content)["limit"] == 2


def test_tools_degrade_to_a_sentence_when_vinc_is_down():
    routes = Routes({("POST", "/v1/brief"): DOWN})
    brief = {t.name: t for t in create_vinc_tools(sync_client(routes))}["vinc_brief"]
    assert "not available" in brief.invoke({"query": "tokens"})


def test_tools_need_a_client():
    with pytest.raises(ValueError):
        create_vinc_tools()


# ── 쓰기 ───────────────────────────────────────────────────────────────────────

def test_record_episode_needs_a_write_key():
    routes = Routes({})
    with pytest.raises(VincAuthError):
        record_episode(sync_client(routes), "Merged", about=["decision:tokens-are-oklch"])
    assert routes.seen == []


# ── create_agent 미들웨어 ──────────────────────────────────────────────────────

def make_agent(middleware):
    from langchain.agents import create_agent
    model = RecordingModel()
    model.calls = []
    return create_agent(model, system_prompt="You review design changes.",
                        middleware=[middleware]), model


def test_middleware_puts_the_block_in_the_system_message():
    from vinc_langgraph import VincContextMiddleware
    routes = Routes({("POST", "/v1/brief"): RESOLVED})
    agent, model = make_agent(VincContextMiddleware(sync_client(routes)))
    out = agent.invoke({"messages": [HumanMessage("Why are colour tokens OKLCH?")]})
    assert out["messages"][-1].content == "ok"
    system = model.system_text()
    assert "You review design changes." in system and "decision:tokens-are-oklch" in system
    assert routes.paths() == ["/v1/brief"]


def test_middleware_reuses_the_answer_for_the_same_question():
    from vinc_langgraph import VincContextMiddleware
    routes = Routes({("POST", "/v1/brief"): RESOLVED})
    agent, _ = make_agent(VincContextMiddleware(sync_client(routes)))
    for _ in range(2):
        agent.invoke({"messages": [HumanMessage("Why are colour tokens OKLCH?")]})
    assert routes.paths() == ["/v1/brief"]


def test_middleware_async_path_and_vinc_down():
    from vinc_langgraph import VincContextMiddleware
    routes = Routes({("POST", "/v1/brief"): DOWN})
    agent, model = make_agent(VincContextMiddleware(async_client=async_client(routes)))
    out = asyncio.run(agent.ainvoke({"messages": [HumanMessage("why are tokens oklch")]}))
    assert out["messages"][-1].content == "ok"
    assert "DATA" not in model.system_text()


# ── 예제 그래프: interrupt 로 멈추고, 승인한 것만 쓴다 ─────────────────────────

def load_example():
    path = Path(__file__).resolve().parents[1] / "examples" / "approve_then_record.py"
    spec = importlib.util.spec_from_file_location("approve_then_record", path)
    module = importlib.util.module_from_spec(spec)
    # 상태 TypedDict 의 주석을 LangGraph 가 모듈 전역으로 푼다 — 등록 안 하면 못 찾는다.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("approved", [True, False])
def test_example_graph_writes_only_after_approval(approved):
    reads = Routes({("POST", "/v1/brief"): RESOLVED})
    writes = Routes({("GET", "/v1/nodes/decision:tokens-are-oklch"): RESOLVED,
                     ("POST", "/v1/fragments"): {"warnings": [], "stored": []}})
    app = load_example().build_graph(sync_client(reads), sync_client(writes, SK_KEY))
    config = {"configurable": {"thread_id": f"t-{approved}"}}
    paused = app.invoke({"question": "Does the secondary button meet contrast on dark?",
                         "about": ["decision:tokens-are-oklch"]}, config)
    assert "__interrupt__" in paused and writes.seen == []
    done = app.invoke(Command(resume=approved), config)
    if approved:
        assert writes.paths() == ["/v1/nodes/decision:tokens-are-oklch", "/v1/fragments"]
        assert done["written"] is not None
    else:
        assert writes.seen == [] and done["written"] is None
    assert reads.paths() == ["/v1/brief"]

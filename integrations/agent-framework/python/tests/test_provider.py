from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from agent_framework import Agent, BaseChatClient, ChatResponse, Message, SessionContext

from vinc_agent_framework import VincContextProvider, create_vinc_tools, record_episode
from vinc_client import AsyncVincClient, VincAuthError

RO_KEY = "vinc_ro_" + "a" * 32
SK_KEY = "vinc_sk_" + "b" * 32

RESOLVED = {
    "status": "resolved",
    "node": {"id": "decision:tokens-are-oklch", "title": "Colour tokens are stored as OKLCH"},
    "one_liner": "Tokens keep OKLCH so contrast can be checked per theme.",
    "grounds": ["decision:tokens-are-oklch"],
}


def vinc(routes: dict[tuple[str, str], Any], key: str = RO_KEY) -> tuple[AsyncVincClient, list]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        answer = routes.get((request.method, request.url.path))
        if answer is None:
            return httpx.Response(404, json={"error": {"code": "route_not_found", "message": ""}})
        status, body = answer if isinstance(answer, tuple) else (200, answer)
        return httpx.Response(status, json=body)

    return AsyncVincClient(key, transport=httpx.MockTransport(handler)), seen


class RecordingChatClient(BaseChatClient):
    """Answers "ok" and keeps what the agent sent, so a test can read the instructions."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[dict[str, Any]] = []

    async def _inner_get_response(self, *, messages, stream, options, **kwargs):  # type: ignore[override]
        self.calls.append({"messages": list(messages), "options": dict(options)})
        return ChatResponse(messages=Message("assistant", ["ok"]))

    def sent_text(self) -> str:
        call = self.calls[-1]
        parts = [str(call["options"].get("instructions") or "")]
        parts += [m.text or "" for m in call["messages"]]
        return "\n".join(parts)


def context_for(*messages: Message) -> SessionContext:
    return SessionContext(input_messages=list(messages))


def run(coro):
    return asyncio.run(coro)


def run_agent(agent: Agent, prompt: str):
    """실제 사용처럼 이벤트 루프 안에서 부른다. 루프 밖에서 agent.run() 을 만들어 넘기면
    1.8.x 의 관측 코드가 컨텍스트 변수를 다른 컨텍스트에서 되돌리려다 실패한다."""
    async def go():
        return await agent.run(prompt)
    return asyncio.run(go())


def tool_text(result: Any) -> str:
    """버전에 따라 invoke 는 문자열이나 Content 목록을 돌려준다(1.19 는 목록)."""
    if isinstance(result, str):
        return result
    return "".join(getattr(part, "text", None) or str(part) for part in result)


# ── before_run ────────────────────────────────────────────────────────────────

def test_before_run_adds_the_fenced_block_to_the_instructions():
    client, seen = vinc({("POST", "/v1/brief"): RESOLVED})
    provider = VincContextProvider(client)
    ctx = context_for(Message("user", ["Why are colour tokens OKLCH?"]))
    run(provider.before_run(agent=None, session=None, context=ctx, state={}))
    assert len(ctx.instructions) == 1
    assert "decision:tokens-are-oklch" in ctx.instructions[0] and "DATA" in ctx.instructions[0]
    assert json.loads(seen[0].content)["query"] == "Why are colour tokens OKLCH?"


def test_the_newest_user_message_is_the_query():
    client, seen = vinc({("POST", "/v1/brief"): RESOLVED})
    ctx = context_for(Message("user", ["first question"]), Message("assistant", ["an answer"]),
                      Message("user", ["the follow-up question"]))
    run(VincContextProvider(client).before_run(agent=None, session=None, context=ctx, state={}))
    assert json.loads(seen[0].content)["query"] == "the follow-up question"


def test_nothing_matched_adds_nothing():
    client, _ = vinc({("POST", "/v1/brief"): {"status": "none"},
                      ("POST", "/v1/search"): {"rows": [], "node_hits": []}})
    ctx = context_for(Message("user", ["nothing like this exists"]))
    run(VincContextProvider(client).before_run(agent=None, session=None, context=ctx, state={}))
    assert ctx.instructions == []


def test_vinc_down_never_fails_the_run():
    client, _ = vinc({("POST", "/v1/brief"): (503, {"error": {"code": "upstream_unavailable",
                                                             "message": "down"}})})
    ctx = context_for(Message("user", ["why are tokens oklch"]))
    run(VincContextProvider(client).before_run(agent=None, session=None, context=ctx, state={}))
    assert ctx.instructions == []


def test_a_key_problem_is_raised_not_hidden():
    client, _ = vinc({("POST", "/v1/brief"): (401, {"error": {"code": "not_authenticated",
                                                             "message": ""}})})
    ctx = context_for(Message("user", ["why are tokens oklch"]))
    with pytest.raises(VincAuthError):
        run(VincContextProvider(client).before_run(agent=None, session=None, context=ctx,
                                                   state={}))


def test_tools_are_added_only_when_asked_and_are_read_only():
    client, _ = vinc({("POST", "/v1/brief"): RESOLVED})
    plain = context_for(Message("user", ["why are tokens oklch"]))
    run(VincContextProvider(client).before_run(agent=None, session=None, context=plain, state={}))
    assert plain.tools == []
    with_tools = context_for(Message("user", ["why are tokens oklch"]))
    run(VincContextProvider(client, expose_tools=True).before_run(
        agent=None, session=None, context=with_tools, state={}))
    assert sorted(t.name for t in with_tools.tools) == ["vinc_brief", "vinc_search"]


# ── after_run 은 아무것도 쓰지 않는다 ───────────────────────────────────────────

def test_after_run_sends_nothing():
    client, seen = vinc({}, key=SK_KEY)
    ctx = context_for(Message("user", ["we merged the contrast fix"]))
    run(VincContextProvider(client).after_run(agent=None, session=None, context=ctx, state={}))
    assert seen == []


# ── 실제 Agent 를 거쳐서 ───────────────────────────────────────────────────────

def test_an_agent_run_carries_the_block_to_the_model_and_writes_nothing():
    client, seen = vinc({("POST", "/v1/brief"): RESOLVED})
    chat = RecordingChatClient()
    agent = Agent(chat, instructions="You review design changes.",
                  context_providers=[VincContextProvider(client)])
    result = run_agent(agent, "Why are colour tokens OKLCH?")
    assert result.text == "ok"
    sent = chat.sent_text()
    assert "You review design changes." in sent and "decision:tokens-are-oklch" in sent
    assert [r.url.path for r in seen] == ["/v1/brief"]


def test_an_agent_run_goes_on_when_vinc_is_down():
    client, _ = vinc({("POST", "/v1/brief"): (502, {"error": {"code": "upstream_unavailable",
                                                             "message": ""}})})
    chat = RecordingChatClient()
    agent = Agent(chat, context_providers=[VincContextProvider(client)])
    assert run_agent(agent, "why are tokens oklch").text == "ok"
    assert "DATA" not in chat.sent_text()


# ── 도구 ───────────────────────────────────────────────────────────────────────

def test_brief_tool_returns_the_block_and_degrades_to_a_sentence():
    client, _ = vinc({("POST", "/v1/brief"): RESOLVED})
    brief_tool = {t.name: t for t in create_vinc_tools(client)}["vinc_brief"]
    assert "decision:tokens-are-oklch" in tool_text(run(brief_tool.invoke(query="tokens")))
    down, _ = vinc({("POST", "/v1/brief"): (429, {"error": {"code": "quota_exceeded",
                                                           "message": ""}})})
    down_tool = {t.name: t for t in create_vinc_tools(down)}["vinc_brief"]
    assert "not available" in tool_text(run(down_tool.invoke(query="tokens")))


def test_search_tool_passes_the_limit():
    client, seen = vinc({("POST", "/v1/search"): {"rows": [], "node_hits": [
        {"id": "concept:spacing", "title": "Spacing", "kind": "concept"}]}})
    search_tool = {t.name: t for t in create_vinc_tools(client)}["vinc_search"]
    assert "concept:spacing" in tool_text(run(search_tool.invoke(query="spacing", limit=3)))
    assert json.loads(seen[0].content)["limit"] == 3


# ── 쓰기 ───────────────────────────────────────────────────────────────────────

def test_record_episode_needs_a_write_key():
    client, seen = vinc({})
    with pytest.raises(VincAuthError):
        run(record_episode(client, "Merged", about=["decision:tokens-are-oklch"]))
    assert seen == []


def test_record_episode_writes_one_fragment():
    client, seen = vinc({("GET", "/v1/nodes/decision:tokens-are-oklch"): RESOLVED,
                         ("POST", "/v1/fragments"): {"warnings": [], "stored": []}}, key=SK_KEY)
    run(record_episode(client, "Merged the contrast fix", about=["decision:tokens-are-oklch"],
                       date="2026-09-28"))
    assert [r.url.path for r in seen] == ["/v1/nodes/decision:tokens-are-oklch", "/v1/fragments"]

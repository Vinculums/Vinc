from __future__ import annotations

import asyncio
from typing import Any

import httpx
from agent_framework import Agent, BaseChatClient, ChatResponse, Content, FunctionInvocationLayer, Message
from vinc_agent_framework import VincContextProvider, create_vinc_tools
from vinc_client import AsyncVincClient
from vinc_client.tools import ToolCallCap, cap_message
from test_provider import tool_text

KEY = "vinc_ro_" + "a" * 32


def handler(request):
    if request.url.path.endswith("brief"):
        return httpx.Response(200, json={"status": "resolved", "node": {
            "id": "decision:fixture", "cite_id": "decision:fixture"}, "one_liner": "Keep it."})
    return httpx.Response(200, json={"node_hits": []})


class BriefsThenDone(FunctionInvocationLayer, BaseChatClient):
    """Asks for ``n`` vinc_brief calls in one turn, then answers once their results are back."""

    def __init__(self, n: int = 5) -> None:
        super().__init__()
        self.n = n

    async def _inner_get_response(self, *, messages, stream, options, **kwargs: Any):  # type: ignore[override]
        messages = list(messages)
        if not any(c.type == "function_result" for m in messages for c in m.contents):
            return ChatResponse(messages=Message("assistant", [
                Content.from_function_call(f"c{len(messages)}-{i}", "vinc_brief", arguments={"query": f"q{i}"})
                for i in range(self.n)]))
        return ChatResponse(messages=Message("assistant", ["done"]))


def function_results(response):
    return [str(c.result) for m in response.messages for c in m.contents if c.type == "function_result"]


def test_the_fifth_vinc_call_of_a_run_gets_the_cap_sentence_and_a_new_run_starts_fresh():
    # 근거: [[decision:vin318-parity-is-judged-on-three-runs-with-a-shared-tool-call-cap-2026-10-07]]
    async def go():
        async with AsyncVincClient(KEY, transport=httpx.MockTransport(handler)) as client:
            chat = BriefsThenDone()
            agent = Agent(chat, context_providers=[VincContextProvider(client, expose_tools=True)])
            for _ in range(2):
                results = function_results(await agent.run("Review."))
                assert [("decision:fixture" in r) for r in results] == [True] * 4 + [False]
                assert results[4] == cap_message(4)
            # 동시에 도는 런은 각자 자기 상한을 가진다(도구를 런마다 만든다).
            agent3 = Agent(BriefsThenDone(n=3), context_providers=[VincContextProvider(client, expose_tools=True)])

            async def one(i):  # 1.8.x: agent.run 은 자기 태스크 안에서 만들고 기다려야 한다(컨텍스트 변수).
                return await agent3.run(f"Q{i}")
            for response in await asyncio.gather(*(one(i) for i in range(4))):
                assert len(function_results(response)) == 3 and all(
                    "decision:fixture" in r for r in function_results(response))
            off = Agent(BriefsThenDone(), context_providers=[VincContextProvider(
                client, expose_tools=True, tool_calls_per_run=None)])
            assert all("decision:fixture" in r for r in function_results(await off.run("Review.")))
            # 직접 만든 도구는 cap 을 주었을 때만, 그 cap 의 수명 동안 센다.
            brief, search = create_vinc_tools(client, cap=ToolCallCap(2))
            assert "decision:fixture" in tool_text(await brief.invoke(query="q1"))
            assert "No matches" in tool_text(await search.invoke(query="q2"))
            assert tool_text(await brief.invoke(query="q3")) == cap_message(2)
    asyncio.run(go())

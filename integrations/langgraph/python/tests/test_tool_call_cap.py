from __future__ import annotations

import asyncio

import httpx
import pytest
from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from vinc_client import AsyncVincClient, VincClient
from vinc_client.tools import ToolCallCap
from vinc_langgraph import VincContextMiddleware, create_vinc_tools

KEY = "vinc_ro_" + "a" * 32


def handler(request):
    if request.url.path.endswith("brief"):
        return httpx.Response(200, json={"status": "resolved", "node": {
            "id": "decision:fixture", "cite_id": "decision:fixture"}, "one_liner": "Keep it."})
    return httpx.Response(200, json={"node_hits": []})


class FiveBriefsThenDone(BaseChatModel):
    """First model call asks for five vinc_brief calls in one turn; the second answers."""
    turns: int = 0

    @property
    def _llm_type(self):
        return "five-briefs"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.turns += 1
        if self.turns % 2:
            calls = [{"name": "vinc_brief", "args": {"query": f"q{i}"}, "id": f"c{self.turns}-{i}"}
                     for i in range(5)]
            return ChatResult(generations=[ChatGeneration(message=AIMessage("", tool_calls=calls))])
        return ChatResult(generations=[ChatGeneration(message=AIMessage("done"))])


def tool_results(state):
    return [m.content for m in state["messages"] if isinstance(m, ToolMessage)]


@pytest.mark.parametrize("async_mode", [False, True])
def test_fifth_vinc_tool_call_in_a_run_gets_the_cap_sentence_and_the_next_run_starts_fresh(async_mode):
    # 근거: [[decision:vin318-parity-is-judged-on-three-runs-with-a-shared-tool-call-cap-2026-10-07]]
    expected = ToolCallCap(4).message

    async def go():
        client = (AsyncVincClient if async_mode else VincClient)(
            KEY, transport=httpx.MockTransport(handler))
        tools = (create_vinc_tools(async_client=client) if async_mode
                 else create_vinc_tools(client=client))
        middleware = VincContextMiddleware(**{"async_client" if async_mode else "client": client},
                                           cache_seconds=0)
        agent = create_agent(FiveBriefsThenDone(), tools=tools, middleware=[middleware])
        for _ in range(2):
            value = {"messages": [HumanMessage("Review.")]}
            state = await agent.ainvoke(value) if async_mode else agent.invoke(value)
            results = tool_results(state)
            assert len(results) == 5 and results.count(expected) == 1
            assert sum("decision:fixture" in r for r in results) == 4
    asyncio.run(go())


def test_cap_can_be_turned_off_and_ignores_other_tools():
    with VincClient(KEY, transport=httpx.MockTransport(handler)) as client:
        off = VincContextMiddleware(client=client, cache_seconds=0, tool_calls_per_run=None)
        agent = create_agent(FiveBriefsThenDone(), tools=create_vinc_tools(client=client),
                             middleware=[off])
        results = tool_results(agent.invoke({"messages": [HumanMessage("Review.")]}))
        assert len(results) == 5 and all("decision:fixture" in r for r in results)
        capped = VincContextMiddleware(client=client, tool_calls_per_run=0)
        assert capped._capped(type("R", (), {"tool_call": {"name": "other", "id": "x"}})()) is None

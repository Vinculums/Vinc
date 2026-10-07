from __future__ import annotations

import asyncio

import httpx
import pytest
from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from vinc_client import AsyncVincClient, VincClient
from vinc_client.tools import cap_message
from vinc_langgraph import VincContextMiddleware, create_vinc_tools
from vinc_langgraph.middleware import vinc_tool_call_ids

KEY = "vinc_ro_" + "a" * 32


def handler(request):
    if request.url.path.endswith("brief"):
        return httpx.Response(200, json={"status": "resolved", "node": {
            "id": "decision:fixture", "cite_id": "decision:fixture"}, "one_liner": "Keep it."})
    return httpx.Response(200, json={"node_hits": []})


class BriefsThenDone(BaseChatModel):
    """Asks for ``n`` vinc_brief calls in one turn, then answers once their results are back."""
    n: int = 5

    @property
    def _llm_type(self):
        return "briefs-then-done"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if getattr(messages[-1], "type", "") != "tool":
            calls = [{"name": "vinc_brief", "args": {"query": f"q{i}"}, "id": f"c{len(messages)}-{i}"}
                     for i in range(self.n)]
            return ChatResult(generations=[ChatGeneration(message=AIMessage("", tool_calls=calls))])
        return ChatResult(generations=[ChatGeneration(message=AIMessage("done"))])


def tool_results(state):
    return [m.content for m in state["messages"] if isinstance(m, ToolMessage)]


@pytest.mark.parametrize("async_mode", [False, True])
def test_the_fifth_vinc_call_of_a_run_gets_the_cap_sentence_and_a_new_run_starts_fresh(async_mode):
    # 근거: [[decision:vin318-parity-is-judged-on-three-runs-with-a-shared-tool-call-cap-2026-10-07]]
    async def go():
        client = (AsyncVincClient if async_mode else VincClient)(KEY, transport=httpx.MockTransport(handler))
        tools = create_vinc_tools(async_client=client) if async_mode else create_vinc_tools(client=client)
        middleware = VincContextMiddleware(**{"async_client" if async_mode else "client": client},
                                           cache_seconds=0)
        agent = create_agent(BriefsThenDone(), tools=tools, middleware=[middleware])
        for _ in range(2):
            value = {"messages": [HumanMessage("Review.")]}
            state = await agent.ainvoke(value) if async_mode else agent.invoke(value)
            results = tool_results(state)
            # 모델이 적은 순서대로: 앞 넷은 Vinc 답, 다섯째가 상한 문장. 병렬 실행 순서와 무관하다.
            assert [("decision:fixture" in r) for r in results] == [True] * 4 + [False]
            assert results[4] == cap_message(4)
    asyncio.run(go())


def test_concurrent_runs_on_one_agent_do_not_share_the_cap():
    async def go():
        async with AsyncVincClient(KEY, transport=httpx.MockTransport(handler)) as client:
            agent = create_agent(BriefsThenDone(n=3), tools=create_vinc_tools(async_client=client),
                                 middleware=[VincContextMiddleware(async_client=client, cache_seconds=0)])
            states = await asyncio.gather(*(agent.ainvoke({"messages": [HumanMessage(f"Q{i}")]})
                                            for i in range(4)))
            for state in states:
                assert all("decision:fixture" in r for r in tool_results(state)) and len(tool_results(state)) == 3
    asyncio.run(go())


def test_cap_can_be_turned_off_and_only_counts_vinc_tools():
    with VincClient(KEY, transport=httpx.MockTransport(handler)) as client:
        agent = create_agent(BriefsThenDone(), tools=create_vinc_tools(client=client),
                             middleware=[VincContextMiddleware(client=client, cache_seconds=0,
                                                               tool_calls_per_run=None)])
        results = tool_results(agent.invoke({"messages": [HumanMessage("Review.")]}))
        assert len(results) == 5 and all("decision:fixture" in r for r in results)
    messages = [HumanMessage("old"), AIMessage("", tool_calls=[{"name": "vinc_brief", "args": {}, "id": "a"}]),
                HumanMessage("new"), AIMessage("", tool_calls=[
                    {"name": "other", "args": {}, "id": "x"}, {"name": "vinc_search", "args": {}, "id": "b"},
                    {"name": "vinc_brief", "args": {}, "id": "c"}])]
    # 새 사람 메시지 뒤부터 세고, vinc 도구가 아닌 호출은 세지 않는다.
    assert vinc_tool_call_ids(messages) == ["b", "c"]

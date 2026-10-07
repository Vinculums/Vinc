from __future__ import annotations

import asyncio

import httpx
from agent_framework import Message
from vinc_agent_framework import VincContextProvider, create_vinc_tools
from vinc_client import AsyncVincClient
from vinc_client.tools import ToolCallCap
from test_provider import context_for, tool_text

KEY = "vinc_ro_" + "a" * 32


def handler(request):
    if request.url.path.endswith("brief"):
        return httpx.Response(200, json={"status": "resolved", "node": {
            "id": "decision:fixture", "cite_id": "decision:fixture"}, "one_liner": "Keep it."})
    return httpx.Response(200, json={"node_hits": []})


def test_both_tools_share_one_cap_and_before_run_resets_it():
    # 근거: [[decision:vin318-parity-is-judged-on-three-runs-with-a-shared-tool-call-cap-2026-10-07]]
    async def go():
        async with AsyncVincClient(KEY, transport=httpx.MockTransport(handler)) as client:
            provider = VincContextProvider(client, expose_tools=True, tool_calls_per_run=2)
            brief, search = provider._tools
            assert provider.tool_cap.limit == 2
            assert "decision:fixture" in tool_text(await brief.invoke(query="q1"))
            assert "No matches" in tool_text(await search.invoke(query="q2"))
            assert tool_text(await brief.invoke(query="q3")) == ToolCallCap(2).message
            await provider.before_run(agent=None, session=None,
                                      context=context_for(Message("user", "Review.")), state={})
            assert "decision:fixture" in tool_text(await brief.invoke(query="q4"))
            # 상한 없이 만든 도구와 끈 프로바이더는 세지 않는다.
            free = create_vinc_tools(client)[0]
            off = VincContextProvider(client, expose_tools=True, tool_calls_per_run=None)
            for _ in range(5):
                assert "decision:fixture" in tool_text(await free.invoke(query="q"))
                assert "decision:fixture" in tool_text(await off._tools[0].invoke(query="q"))
            assert VincContextProvider(client, expose_tools=True).tool_cap.limit == 4
    asyncio.run(go())

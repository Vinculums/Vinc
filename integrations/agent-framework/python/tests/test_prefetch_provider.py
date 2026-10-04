from __future__ import annotations

import asyncio
import httpx
from agent_framework import Agent
from vinc_agent_framework import VincContextProvider
from vinc_client import AsyncVincClient
from test_provider import RecordingChatClient


def test_role_prefetch_reaches_real_agent_before_model_without_tool_call():
    seen = []

    def handler(request):
        seen.append(request)
        payload = ({"found": True, "id": "concept:agent-reviewer", "props": {"reads": []}}
                   if request.method == "GET" else {"status": "resolved",
                       "node": {"id": "decision:fixture", "cite_id": "decision:fixture"},
                       "one_liner": "Keep it."} if request.url.path.endswith("brief")
                   else {"node_hits": []})
        return httpx.Response(200, json=payload)

    class PrefetchedChat(RecordingChatClient):
        async def _inner_get_response(self, **kwargs):
            assert len(seen) == 3
            return await super()._inner_get_response(**kwargs)

    async def go():
        async with AsyncVincClient("vinc_ro_" + "a" * 32,
                                   transport=httpx.MockTransport(handler)) as client:
            chat = PrefetchedChat()
            agent = Agent(chat, context_providers=[VincContextProvider(
                client, prefetch_role="concept:agent-reviewer", expose_tools=True)])
            await agent.run("Review the policy.")
            assert "decision:fixture" in chat.sent_text() and "concept:agent-reviewer" in chat.sent_text()
            assert all(r.method == "GET" or r.url.path.endswith(("brief", "search")) for r in seen)

    asyncio.run(go())

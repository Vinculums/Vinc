from __future__ import annotations

import asyncio
import hashlib
import httpx
import pytest
from agent_framework import Agent
from vinc_agent_framework import VincContextProvider
from vinc_client import AsyncVincClient, VincInvalidArgument
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


def test_budget_error_stops_model_and_corrected_provider_runs():
    text = "Mandatory rule. " * 2000
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if request.url.path.startswith("/v1/roles/"):
            payload = {"found": True, "id": "concept:agent-test", "props": {"reads": ["large"]}}
        elif request.url.path.startswith("/v1/documents/"):
            payload = {"doc_id": "large", "executed": True, "verified": True, "text": text,
                       "content_hash": hashlib.sha256(text.encode()).hexdigest()}
        else:
            payload = {"status": "none"}
        return httpx.Response(200, json=payload)

    async def go():
        async with AsyncVincClient("vinc_ro_" + "a" * 32,
                                   transport=httpx.MockTransport(handler)) as client:
            chat = RecordingChatClient()
            async def invoke(**kwargs):
                agent = Agent(chat, context_providers=[VincContextProvider(
                    client, prefetch_role="concept:agent-test", **kwargs)])
                return await agent.run("Review.")
            with pytest.raises(VincInvalidArgument) as caught:
                await invoke()
            assert not chat.calls
            assert len(seen) == 2
            await invoke(prefetch_max_chars=caught.value.details["recommended_max_chars"])
            assert len(chat.calls) == 1
            assert text in chat.sent_text()

    asyncio.run(go())

from __future__ import annotations

import asyncio

import httpx
from agent_framework import Agent
from vinc_agent_framework import PackageProvider
from vinc_client import AsyncVincClient, build_package_fragment

from test_provider import RecordingChatClient


def test_real_agent_receives_package_by_id_without_after_run_write():
    node = build_package_fragment("Which limit?", "Keep the 36 hour threshold.",
                                  claims=[{"claim": "Stale after 36 hours.",
                                           "evidence_ids": ["decision:threshold"]}],
                                  produced_by="producer", run_id="mechanical-fixture",
                                  package_id="record:handoff")["nodes"][0]
    seen = []

    def handler(request):
        seen.append(request)
        assert request.method == "GET"
        data = node if request.url.path.endswith("record:handoff") else {"id": "decision:threshold"}
        return httpx.Response(200, json={"found": True, "node": data})

    async def go():
        async with AsyncVincClient("vinc_ro_" + "a" * 32,
                                   transport=httpx.MockTransport(handler)) as client:
            chat = RecordingChatClient()
            agent = Agent(chat, instructions="Review the existing conclusion.",
                          context_providers=[PackageProvider(client, "record:handoff",
                                                             space="personal")])
            await agent.run("Continue from the package.")
            assert "Keep the 36 hour threshold." in chat.sent_text()
            assert "decision:threshold" in chat.sent_text() and "DATA" in chat.sent_text()
            await agent.run("Continue again.")
            assert len(seen) == 4  # 권한 판정은 두 번째 실행에도 새로 한다.

    asyncio.run(go())

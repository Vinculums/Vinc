import asyncio
import json

import httpx
import pytest
from vinc_agent_framework import record_episode
from vinc_client import AsyncVincClient, VincAuthError


def test_episode_retry_identity_passes_through_framework_helper():
    seen = []
    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"warnings": []})
    async def go():
        async with AsyncVincClient("vinc_sk_" + "b" * 32, transport=httpx.MockTransport(handler)) as client:
            for _ in range(2):
                await record_episode(client, "Approved", episode_id="episode:run-one", date="2026-10-06")
            with pytest.raises(ValueError):
                await record_episode(client, "Approved", episode_id="record:wrong")
        async with AsyncVincClient("vinc_ro_" + "a" * 32, transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(VincAuthError):
                await record_episode(client, "Approved", episode_id="episode:run-one")
    asyncio.run(go())
    assert len(seen) == 2 and seen[0] == seen[1]
    assert seen[0]["nodes"][0]["id"] == "episode:run-one"


def test_helpers_pass_create_only_and_operation_id_through():
    # VIN-601: 헬퍼가 ingest 와 같은 재시도·생성 전용 계약을 본문에 그대로 싣는다.
    from vinc_agent_framework import write_package
    seen = []
    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"warnings": []})
    op = "00000000-0000-4000-8000-000000000601"
    async def go():
        async with AsyncVincClient("vinc_sk_" + "b" * 32, transport=httpx.MockTransport(handler)) as client:
            await record_episode(client, "Approved", episode_id="episode:run-one",
                                 create_only=True, operation_id=op)
            await write_package(client, "Which?", "This.",
                                claims=[{"claim": "Holds.", "evidence_ids": ["decision:policy"]}],
                                produced_by="agent:writer", run_id="run-1", package_id="record:handoff",
                                space="01a0a0e0-3f91-7128-9876-6b543f28de0a",
                                create_only=True, operation_id=op)
    asyncio.run(go())
    assert [(s["create_only"], s["operation_id"]) for s in seen] == [(True, op), (True, op)]

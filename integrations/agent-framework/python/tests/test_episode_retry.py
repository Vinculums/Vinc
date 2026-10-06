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

import asyncio
import json

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from vinc_client import AsyncVincClient, VincAuthError, VincClient
from vinc_langgraph import arecord_episode, record_episode

from test_langgraph import load_example

SK = "vinc_sk_" + "b" * 32
RO = "vinc_ro_" + "a" * 32


@pytest.mark.parametrize("async_mode", [False, True])
def test_helper_passes_retry_identity_and_readonly_still_blocks(async_mode):
    seen = []
    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"warnings": []})
    client = (AsyncVincClient if async_mode else VincClient)(SK, transport=httpx.MockTransport(handler))
    for _ in range(2):
        if async_mode:
            asyncio.run(arecord_episode(client, "Approved", episode_id="episode:run-one", date="2026-10-06"))
        else:
            record_episode(client, "Approved", episode_id="episode:run-one", date="2026-10-06")
    assert seen[0] == seen[1]
    assert seen[0]["nodes"][0]["id"] == "episode:run-one"
    with pytest.raises(ValueError):
        if async_mode:
            asyncio.run(arecord_episode(client, "Approved", episode_id="record:wrong"))
        else:
            record_episode(client, "Approved", episode_id="record:wrong")
    readonly = (AsyncVincClient if async_mode else VincClient)(RO, transport=httpx.MockTransport(handler))
    with pytest.raises(VincAuthError):
        if async_mode:
            asyncio.run(arecord_episode(readonly, "Approved", episode_id="episode:run-one"))
        else:
            record_episode(readonly, "Approved", episode_id="episode:run-one")
    assert len(seen) == 2


def test_host_checkpoint_rebuild_and_lost_ack_keep_one_episode_identity():
    writes = []
    drafts = []
    fail = [True]
    def handler(request):
        if request.url.path.endswith("brief"):
            return httpx.Response(200, json={"status": "none"})
        if request.url.path.endswith("search"):
            return httpx.Response(200, json={"node_hits": []})
        writes.append(json.loads(request.content))
        if fail[0]:
            fail[0] = False
            raise httpx.ReadTimeout("ACK lost after write")
        return httpx.Response(200, json={"warnings": []})
    def review(question, context):
        drafts.append(question)
        return "Approved draft"
    reader = VincClient(RO, transport=httpx.MockTransport(handler))
    writer = VincClient(SK, transport=httpx.MockTransport(handler))
    checkpointer = InMemorySaver()
    config = {"configurable": {"thread_id": "host-run-one"}}
    example = load_example()
    first = example.build_graph(reader, writer, review, checkpointer=checkpointer)
    paused = first.invoke({"question": "Review policy"}, config)
    saved_id = paused["episode_id"]
    assert not writes and len(drafts) == 1
    rebuilt = example.build_graph(reader, writer, review, checkpointer=checkpointer)
    from vinc_client import VincUnavailable
    with pytest.raises(VincUnavailable):
        rebuilt.invoke(Command(resume=True), config)
    rebuilt.invoke(None, config)
    assert len(drafts) == 1 and len(writes) == 2
    assert writes[0] == writes[1]
    assert writes[0]["nodes"][0]["id"] == saved_id

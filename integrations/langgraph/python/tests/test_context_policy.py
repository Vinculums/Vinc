"""context_policy 를 주면 블록을 compile_context 로 만든다. FULL_TEXT 는 prefetch 와 같은 블록이다."""
from __future__ import annotations

import asyncio
import hashlib

import httpx
import pytest
from langchain.agents import create_agent
from langchain_core.messages import HumanMessage
from vinc_client import FULL_TEXT, AsyncVincClient, ContextItem, ContextPlan, VincClient
from vinc_langgraph import VincContextMiddleware
from test_langgraph import RecordingModel

TEXT = "Keep every rule.\nTAIL RULE"


def handler(request):
    path = request.url.path
    if path.startswith("/v1/roles/"):
        payload = {"found": True, "id": "concept:agent-reviewer", "domain": "agent",
                   "props": {"reads": ["vinc.spec.fixture"]}}
    elif path.startswith("/v1/documents/"):
        payload = {"executed": True, "doc_id": "vinc.spec.fixture", "verified": True, "text": TEXT,
                   "content_hash": hashlib.sha256(TEXT.encode()).hexdigest()}
    elif path.startswith("/v1/nodes/"):
        payload = {"executed": True, "found": True, "node": {"id": "concept:doc.vinc.spec.fixture"}}
    elif path.endswith("brief"):
        payload = {"status": "resolved", "node": {"id": "decision:fixture", "cite_id": "decision:fixture"},
                   "one_liner": "Keep it."}
    else:
        payload = {"node_hits": [{"id": "record:lesson", "cite_id": "record:lesson", "title": "Lesson"}]}
    return httpx.Response(200, json=payload)


class NoEvidence:
    id = "no-evidence"

    def plan(self, request, reader):
        return ContextPlan(items=tuple(ContextItem(read) for read, _ in reader.required))


def _system(async_mode, **kwargs):
    async def go():
        client = (AsyncVincClient if async_mode else VincClient)(
            "vinc_ro_" + "a" * 32, transport=httpx.MockTransport(handler))
        model = RecordingModel()
        agent = create_agent(model, middleware=[VincContextMiddleware(
            **{"async_client" if async_mode else "client": client},
            prefetch_role="concept:agent-reviewer", **kwargs)])
        message = {"messages": [HumanMessage(content="Review the policy.")]}
        if async_mode:
            await agent.ainvoke(message)
        else:
            agent.invoke(message)
        return model.system_text()
    return asyncio.run(go())


@pytest.mark.parametrize("async_mode", [False, True])
def test_full_text_policy_gives_the_prefetch_block(async_mode):
    assert _system(async_mode, context_policy=FULL_TEXT) == _system(async_mode)


@pytest.mark.parametrize("async_mode", [False, True])
def test_a_policy_changes_what_the_model_sees(async_mode):
    text = _system(async_mode, context_policy=NoEvidence())
    assert "TAIL RULE" in text and "record:lesson" not in text
    assert "record:lesson" in _system(async_mode)


def test_context_policy_needs_a_role():
    with pytest.raises(ValueError, match="context_policy needs prefetch_role"):
        VincContextMiddleware(client=VincClient("vinc_ro_" + "a" * 32), context_policy=FULL_TEXT)

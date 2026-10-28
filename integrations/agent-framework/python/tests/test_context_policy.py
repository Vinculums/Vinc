"""context_policy 를 주면 블록을 acompile_context 로 만든다. FULL_TEXT 는 prefetch 와 같은 블록이다."""
from __future__ import annotations

import asyncio
import hashlib

import httpx
import pytest
from agent_framework import Agent
from vinc_agent_framework import VincContextProvider
from vinc_client import FULL_TEXT, AsyncVincClient, ContextItem, ContextPlan
from test_provider import RecordingChatClient

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


def _sent(**kwargs):
    async def go():
        async with AsyncVincClient("vinc_ro_" + "a" * 32,
                                   transport=httpx.MockTransport(handler)) as client:
            chat = RecordingChatClient()
            agent = Agent(chat, context_providers=[VincContextProvider(
                client, prefetch_role="concept:agent-reviewer", **kwargs)])
            await agent.run("Review the policy.")
            return chat.sent_text()
    return asyncio.run(go())


def test_full_text_policy_gives_the_prefetch_block():
    assert _sent(context_policy=FULL_TEXT) == _sent()


def test_a_policy_changes_what_the_model_sees():
    text = _sent(context_policy=NoEvidence())
    assert "TAIL RULE" in text and "record:lesson" not in text
    assert "record:lesson" in _sent()


def test_context_policy_needs_a_role():
    with pytest.raises(ValueError, match="context_policy needs prefetch_role"):
        VincContextProvider(AsyncVincClient("vinc_ro_" + "a" * 32), context_policy=FULL_TEXT)

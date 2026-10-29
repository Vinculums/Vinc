"""Agent Framework 의 문서 읽기 도구: expose_documents 일 때만 있고, 같은 상한을 나눠 쓰며, 정책의 read_tool 을 켠다."""
from __future__ import annotations

import asyncio
import hashlib
import json

import httpx
import pytest
from agent_framework import Agent
from vinc_agent_framework import VincContextProvider, create_vinc_tools
from vinc_client import AsyncVincClient, ContextItem, ContextPlan, VincInvalidArgument
from vinc_client.tools import ToolCallCap
from test_provider import RecordingChatClient

TEXT = "# Spec\nKeep every rule.\n## Tail\nTAIL RULE\n"


def handler(request):
    path = request.url.path
    if path.startswith("/v1/roles/"):
        payload = {"found": True, "id": "concept:agent-reviewer",
                   "props": {"reads": ["vinc.spec.fixture"]}}
    elif path.startswith("/v1/documents/"):
        payload = {"executed": True, "doc_id": "vinc.spec.fixture", "verified": True, "text": TEXT,
                   "content_hash": hashlib.sha256(TEXT.encode()).hexdigest()}
    elif path.startswith("/v1/nodes/"):
        payload = {"executed": True, "found": False, "node": None}
    else:
        payload = {"status": "none"}
    return httpx.Response(200, json=payload)


def text(value):
    return value if isinstance(value, str) else "".join(part.text for part in value)


class Pointer:
    id = "pointer"

    def plan(self, request, reader):
        return ContextPlan(items=tuple(ContextItem(read, form="pointer") for read, _ in reader.required))


def test_the_tool_exists_only_when_asked_for_and_shares_the_cap():
    async def go():
        async with AsyncVincClient("vinc_ro_" + "a" * 32,
                                   transport=httpx.MockTransport(handler)) as client:
            assert [t.name for t in create_vinc_tools(client)] == ["vinc_brief", "vinc_search"]
            cap = ToolCallCap(2)
            tools = create_vinc_tools(client, cap=cap, documents=True)
            assert [t.name for t in tools] == ["vinc_brief", "vinc_search", "vinc_doc_get"]
            doc_tool = tools[2]
            first = await doc_tool.invoke(doc_id="vinc.spec.fixture", start=0, end=6)
            data = json.loads(text(first).split("```\n", 1)[1].rsplit("\n```", 1)[0])
            assert data["text"] == "# Spec" and data["cite_as"].startswith("no citable id")
            await tools[0].invoke(query="decision:x")
            assert text(await doc_tool.invoke(doc_id="vinc.spec.fixture")) == cap.message
    asyncio.run(go())


def test_expose_documents_turns_on_shortened_readings():
    async def go(**kw):
        async with AsyncVincClient("vinc_ro_" + "a" * 32,
                                   transport=httpx.MockTransport(handler)) as client:
            chat = RecordingChatClient()
            agent = Agent(chat, context_providers=[VincContextProvider(
                client, prefetch_role="concept:agent-reviewer", context_policy=Pointer(),
                expose_tools=True, **kw)])
            await agent.run("Review the policy.")
            return chat.sent_text()
    with pytest.raises(VincInvalidArgument, match="I4"):
        asyncio.run(go())
    text = asyncio.run(go(expose_documents=True))
    assert "TAIL RULE" not in text and "vinc_doc_get" in text


def test_expose_documents_needs_expose_tools():
    with pytest.raises(ValueError, match="expose_documents needs expose_tools"):
        VincContextProvider(AsyncVincClient("vinc_ro_" + "a" * 32), expose_documents=True)


def test_a_role_that_disallows_the_tool_keeps_its_readings_in_full():
    def handler2(request):
        if request.url.path.startswith("/v1/roles/"):
            return httpx.Response(200, json={"found": True, "id": "concept:agent-reviewer",
                "props": {"reads": ["vinc.spec.fixture"], "disallowed_tools": ["vinc_doc_get"]}})
        return handler(request)

    async def go():
        async with AsyncVincClient("vinc_ro_" + "a" * 32,
                                   transport=httpx.MockTransport(handler2)) as client:
            agent = Agent(RecordingChatClient(), context_providers=[VincContextProvider(
                client, prefetch_role="concept:agent-reviewer", context_policy=Pointer(),
                expose_tools=True, expose_documents=True)])
            await agent.run("Review the policy.")
    with pytest.raises(VincInvalidArgument, match="I4"):
        asyncio.run(go())


def test_expose_documents_with_no_policy_uses_the_compiler_default():
    # 정책을 주지 않아도 vinc_doc_get 을 내주면 과제 근거 정책(필수 문서는 포인터), 아니면 prefetch 그대로다.
    def handler2(props):
        def inner(request):
            if request.url.path.startswith("/v1/roles/"):
                return httpx.Response(200, json={"found": True, "id": "concept:agent-reviewer",
                                                 "props": {"reads": ["vinc.spec.fixture"], **props}})
            return handler(request)
        return inner

    async def go(props=None, **kw):
        async with AsyncVincClient("vinc_ro_" + "a" * 32,
                                   transport=httpx.MockTransport(handler2(props or {}))) as client:
            chat = RecordingChatClient()
            agent = Agent(chat, context_providers=[VincContextProvider(
                client, prefetch_role="concept:agent-reviewer", expose_tools=True, **kw)])
            await agent.run("Review the policy.")
            return chat.sent_text()
    with_tool = asyncio.run(go(expose_documents=True))
    assert "TAIL RULE" not in with_tool and "Required reading (not included; read it with vinc_doc_get" in with_tool
    assert "TAIL RULE" in asyncio.run(go())
    assert "TAIL RULE" in asyncio.run(go({"disallowed_tools": ["vinc_doc_get"]}, expose_documents=True))

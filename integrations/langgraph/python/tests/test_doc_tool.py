"""LangGraph 의 문서 읽기 도구: documents=True 일 때만 있고, 미들웨어가 그것을 보면 정책이 필수 문서를 줄일 수 있다."""
from __future__ import annotations

import asyncio
import hashlib
import json

import httpx
import pytest
from langchain.agents import create_agent
from langchain_core.messages import HumanMessage
from vinc_client import (AsyncVincClient, ContextItem, ContextPlan, VincClient,
                         VincInvalidArgument)
from vinc_langgraph import VincContextMiddleware, create_vinc_tools
from test_langgraph import RecordingModel

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
        payload = {"executed": True, "found": True, "node": {"id": "concept:doc.vinc.spec.fixture"}}
    else:
        payload = {"status": "none"}
    return httpx.Response(200, json=payload)


class ToolModel(RecordingModel):
    def bind_tools(self, tools, **kwargs):
        return self


class Pointer:
    id = "pointer"

    def plan(self, request, reader):
        return ContextPlan(items=tuple(ContextItem(read, form="pointer") for read, _ in reader.required))


def test_the_tool_exists_only_when_asked_for():
    client = VincClient("vinc_ro_" + "a" * 32, transport=httpx.MockTransport(handler))
    assert [t.name for t in create_vinc_tools(client)] == ["vinc_brief", "vinc_search"]
    tools = create_vinc_tools(client, documents=True)
    assert [t.name for t in tools] == ["vinc_brief", "vinc_search", "vinc_doc_get"]
    out = tools[2].invoke({"doc_id": "vinc.spec.fixture", "section": "Tail"})
    data = json.loads(out.split("```\n", 1)[1].rsplit("\n```", 1)[0])
    assert data["text"] == "## Tail\nTAIL RULE\n" and data["cite_as"] == "concept:doc.vinc.spec.fixture"
    assert set(tools[2].args) == {"doc_id", "start", "end", "section"}


@pytest.mark.parametrize("async_mode", [False, True])
def test_a_shortening_policy_needs_the_tool_in_the_agent(async_mode):
    async def go(documents):
        client = (AsyncVincClient if async_mode else VincClient)(
            "vinc_ro_" + "a" * 32, transport=httpx.MockTransport(handler))
        model = ToolModel()
        kw = {"async_client" if async_mode else "client": client}
        agent = create_agent(model, tools=create_vinc_tools(**kw, documents=documents),
                             middleware=[VincContextMiddleware(
                                 **kw, prefetch_role="concept:agent-reviewer",
                                 context_policy=Pointer())])
        message = {"messages": [HumanMessage(content="Review the policy.")]}
        if async_mode:
            await agent.ainvoke(message)
        else:
            agent.invoke(message)
        return model.system_text()
    with pytest.raises(VincInvalidArgument, match="I4"):
        asyncio.run(go(False))
    text = asyncio.run(go(True))
    assert "TAIL RULE" not in text and "vinc_doc_get" in text and "vinc.spec.fixture" in text


def _role_handler(props):
    def handler2(request):
        if request.url.path.startswith("/v1/roles/"):
            return httpx.Response(200, json={"found": True, "id": "concept:agent-reviewer",
                                             "props": {"reads": ["vinc.spec.fixture"], **props}})
        return handler(request)
    return handler2


@pytest.mark.parametrize("props,cap_map", [
    ({"disallowed_tools": ["vinc_doc_get"]}, None),
    ({"capabilities": {"approve": ["vinc.read.documents"]}}, {"vinc_doc_get": "vinc.read.documents"}),
    ({"budget": {"tool_calls": 0}}, None),
])
def test_a_role_that_rules_out_the_tool_keeps_its_readings_in_full(props, cap_map):
    client = VincClient("vinc_ro_" + "a" * 32, transport=httpx.MockTransport(_role_handler(props)))
    agent = create_agent(ToolModel(), tools=create_vinc_tools(client, documents=True),
                         middleware=[VincContextMiddleware(
                             client=client, prefetch_role="concept:agent-reviewer",
                             context_policy=Pointer(), capability_map=cap_map)])
    with pytest.raises(VincInvalidArgument, match="I4"):
        agent.invoke({"messages": [HumanMessage(content="Review the policy.", id="h1")]})


class PointerWhenAllowed:
    id = "pointer-when-allowed"

    def plan(self, request, reader):
        form = "pointer" if request.read_tool else "full"
        return ContextPlan(items=tuple(ContextItem(read, form=form) for read, _ in reader.required))


def test_a_shared_middleware_keeps_blocks_apart_for_agents_with_and_without_the_tool():
    client = VincClient("vinc_ro_" + "a" * 32, transport=httpx.MockTransport(handler))
    middleware = VincContextMiddleware(client=client, prefetch_role="concept:agent-reviewer",
                                       context_policy=PointerWhenAllowed())
    message = {"messages": [HumanMessage(content="Review the policy.", id="same")]}
    with_tool, without = ToolModel(), ToolModel()
    create_agent(with_tool, tools=create_vinc_tools(client, documents=True),
                 middleware=[middleware]).invoke(message)
    create_agent(without, tools=create_vinc_tools(client), middleware=[middleware]).invoke(message)
    assert "TAIL RULE" not in with_tool.system_text()
    assert "TAIL RULE" in without.system_text()


def test_the_tool_call_path_does_not_compile():
    from langchain_core.messages import AIMessage
    seen = []

    def counting(request):
        seen.append(request.url.path)
        return handler(request)
    client = VincClient("vinc_ro_" + "a" * 32, transport=httpx.MockTransport(counting))
    middleware = VincContextMiddleware(client=client, prefetch_role="concept:agent-reviewer",
                                       context_policy=Pointer())
    call = {"name": "vinc_doc_get", "args": {"doc_id": "vinc.spec.fixture"}, "id": "c1"}

    class Request:
        tool_call = call
        state = {"messages": [HumanMessage(content="Review.", id="h"),
                              AIMessage(content="", tool_calls=[call])]}
    result = middleware.wrap_tool_call(Request(), lambda r: "ran")
    assert result == "ran"
    assert seen == ["/v1/roles/concept:agent-reviewer"]

"""Offline parity through both installed framework packages; no model service calls."""
import asyncio
import json
import hashlib

import httpx
import pytest
from agent_framework import Agent, BaseChatClient, ChatResponse, Message
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from vinc_client import AsyncVincClient, VincClient, VincInvalidArgument, VincNotFound
from vinc_client.context import DEFAULT_MAX_CHARS
from vinc_agent_framework import create_vinc_tools as maf_tools, prefetch as maf_prefetch
from vinc_agent_framework import run_review as maf_review
from vinc_langgraph import create_vinc_tools as lg_tools, prefetch as lg_prefetch
from vinc_langgraph import aprefetch as lg_aprefetch, run_review as lg_review, arun_review

KEY = "vinc_ro_" + "a" * 32
SPACE = "01a0a0e0-3f91-7128-9876-6b543f28de0a"
ROLE = "concept:agent-reviewer-judgement"
VALID = json.dumps({"verdict": "RETURN", "findings": [
    {"what": "Keep the cited rule.", "cite": ["record:rule"]}]})


def response(request, seen):
    seen.append((request.method, request.url.path, dict(request.url.params),
                 json.loads(request.content) if request.content else None))
    if request.url.path.startswith("/v1/documents/"):
        doc_text = "Preserve this evidence."
        body = {"executed": True, "verified": True, "doc_id": "vinc.spec.fixture",
                "text": doc_text, "content_hash": hashlib.sha256(doc_text.encode()).hexdigest()}
    elif request.method == "GET":
        body = {"found": True, "id": ROLE, "domain": "vinc/engineering",
                "props": {"reads": ["vinc.spec.fixture"], "mission": "Review evidence."}}
    elif request.url.path.endswith("brief"):
        query = json.loads(request.content)["query"]
        body = {"status": "resolved", "node": {"id": "concept:rule", "cite_id": "concept:rule",
            "title": "Rule"}, "one_liner": query, "excerpts": [{"doc_id": "vinc.spec.fixture",
            "cite_id": "chunk:rule", "doc_title": "Rule", "excerpt": "Preserve this evidence."}]}
    else:
        body = {"node_hits": [{"id": "record:rule", "cite_id": "record:rule", "title": "Rule"}],
                "rows": [{"doc_id": "vinc.spec.fixture", "cite_id": "chunk:rule",
                          "doc_title": "Rule", "snippet": "Preserve this evidence."}]}
    return httpx.Response(200, json=body)


def text(value):
    return value if isinstance(value, str) else "".join(part.text for part in value)


def test_same_read_tools_defaults_caps_context_and_citations():
    async def run():
        ma_seen, lg_seen = [], []
        async with AsyncVincClient(KEY, transport=httpx.MockTransport(
                lambda req: response(req, ma_seen))) as ma_client:
            async with AsyncVincClient(KEY, transport=httpx.MockTransport(
                    lambda req: response(req, lg_seen))) as lg_client:
                ma, lg = maf_tools(ma_client), lg_tools(async_client=lg_client)
                assert [tool.name for tool in ma] == [tool.name for tool in lg] == [
                    "vinc_brief", "vinc_search"]
                ma_schema = ma[1].to_json_schema_spec()["function"]["parameters"]["properties"]
                lg_schema = lg[1].args_schema.model_json_schema()["properties"]
                for key in ("default", "minimum", "maximum", "type"):
                    assert ma_schema["limit"][key] == lg_schema["limit"][key]
                assert ma_schema["limit"]["default"] == 5
                assert ma_schema["limit"]["maximum"] == 20
                for index in (0, 1):
                    left = text(await ma[index].invoke(query="Review evidence."))
                    right = await lg[index].ainvoke({"query": "Review evidence."})
                    assert left == right and len(left) <= DEFAULT_MAX_CHARS
                    assert "DATA" in left and "chunk:rule" in left
                    assert "(vinc.spec.fixture)" not in left
                assert ma_seen == lg_seen
                assert ma_seen[-1][3]["limit"] == 5
    asyncio.run(run())


def _brief_404(code):
    def handle(req):
        if req.url.path.endswith("brief") and json.loads(req.content)["query"] == "decision:missing":
            return httpx.Response(404, json={"error": {"code": code, "message": "missing"}})
        return response(req, [])
    return handle


def test_missing_id_is_the_same_tool_result_in_both_and_the_run_continues():
    # 없는 id 는 두 패키지(LangGraph 는 동기·비동기 둘 다)에서 같은 도구 결과가 되고, 다음 호출이 그대로 돈다.
    # 근거: [[record:vin318-live-exp02-r2-parity-tool-calls-2-vs-1-2026-10-07]]
    expected = "Vinc found nothing for that (not_found); try a different query."
    handle = _brief_404("not_found")
    with VincClient(KEY, transport=httpx.MockTransport(handle)) as sync_client:
        lg_sync = lg_tools(client=sync_client)[0]
        assert lg_sync.invoke({"query": "decision:missing"}) == expected
        sync_next = lg_sync.invoke({"query": "Review"})
    async def run():
        async with AsyncVincClient(KEY, transport=httpx.MockTransport(handle)) as client:
            ma, lg = maf_tools(client)[0], lg_tools(async_client=client)[0]
            left = text(await ma.invoke(query="decision:missing"))
            right = await lg.ainvoke({"query": "decision:missing"})
            assert left == right == expected
            assert text(await ma.invoke(query="Review")) == await lg.ainvoke({"query": "Review"}) == sync_next
    asyncio.run(run())


def test_space_not_found_on_brief_raises_in_both():
    # 404 라도 space_not_found 는 설정 오류다. 도구 결과로 삼키지 않고 두 패키지 모두 올린다.
    handle = _brief_404("space_not_found")
    with VincClient(KEY, transport=httpx.MockTransport(handle)) as sync_client:
        with pytest.raises(VincNotFound, match="space_not_found"):
            lg_tools(client=sync_client)[0].invoke({"query": "decision:missing"})
    async def run():
        async with AsyncVincClient(KEY, transport=httpx.MockTransport(handle)) as client:
            for call in (lambda: maf_tools(client)[0].invoke(query="decision:missing"),
                         lambda: lg_tools(async_client=client)[0].ainvoke({"query": "decision:missing"})):
                with pytest.raises(VincNotFound, match="space_not_found"):
                    await call()
    asyncio.run(run())


@pytest.mark.parametrize("limit", [0, 21])
def test_both_frameworks_reject_out_of_range_search_before_http(limit):
    async def run():
        seen = []
        async with AsyncVincClient(KEY, transport=httpx.MockTransport(
                lambda req: response(req, seen))) as client:
            for call in (lambda: maf_tools(client)[1].invoke(query="Review", limit=limit),
                         lambda: lg_tools(async_client=client)[1].ainvoke(
                             {"query": "Review", "limit": limit})):
                with pytest.raises(Exception, match="limit"):
                    await call()
            assert not seen
    asyncio.run(run())


@pytest.mark.parametrize("framework", ["maf", "langgraph"])
def test_async_read_tools_allow_caller_selected_parallel_calls(framework):
    async def run():
        arrivals = 0
        both_arrived = asyncio.Event()
        async def handle(req):
            nonlocal arrivals
            arrivals += 1
            if arrivals == 2:
                both_arrived.set()
            await asyncio.wait_for(both_arrived.wait(), timeout=2)
            return response(req, [])
        async with AsyncVincClient(KEY, transport=httpx.MockTransport(handle)) as client:
            if framework == "maf":
                tool = maf_tools(client)[0]
                calls = [tool.invoke(query=q) for q in ("First", "Second")]
            else:
                tool = lg_tools(async_client=client)[0]
                calls = [tool.ainvoke({"query": q}) for q in ("First", "Second")]
            results = await asyncio.gather(*calls)
            assert arrivals == 2 and len(results) == 2
    asyncio.run(run())


@pytest.mark.parametrize("task", ["Review the policy.", "한국어 근거를 확인하세요."])
def test_same_selected_role_prefetch_bytes_calls_and_budget(task):
    sync_seen, ma_seen, lg_seen = [], [], []
    with VincClient(KEY, transport=httpx.MockTransport(lambda r: response(r, sync_seen))) as client:
        sync_block = lg_prefetch(client, ROLE, task, space=SPACE)
        with pytest.raises(VincInvalidArgument, match="prefetch_budget_exceeded"):
            lg_prefetch(client, ROLE, task, space=SPACE, max_chars=100)
    async def run():
        async with AsyncVincClient(KEY, transport=httpx.MockTransport(
                lambda r: response(r, ma_seen))) as client:
            ma_block = await maf_prefetch(client, ROLE, task, space=SPACE)
            with pytest.raises(VincInvalidArgument, match="prefetch_budget_exceeded"):
                await maf_prefetch(client, ROLE, task, space=SPACE, max_chars=100)
        async with AsyncVincClient(KEY, transport=httpx.MockTransport(
                lambda r: response(r, lg_seen))) as client:
            lg_block = await lg_aprefetch(client, ROLE, task, space=SPACE)
        assert sync_block.encode() == ma_block.encode() == lg_block.encode()
        # client 0.1.4: 선택 읽기가 역할 읽기와 겹치므로 도착 순서는 계약이 아니다. 요청 집합을 대조한다.
        assert sorted(sync_seen[:4], key=repr) == sorted(ma_seen[:4], key=repr) == sorted(lg_seen, key=repr)
        assert len(lg_seen) == 4 and all(row[2]["space"] == SPACE for row in lg_seen)
        assert all(cite in lg_block for cite in (ROLE, "concept:rule", "chunk:rule", "record:rule"))
        assert len(lg_block) <= 24000
    asyncio.run(run())


class RecordedClient(BaseChatClient):
    def __init__(self):
        super().__init__()
        self.calls = []
    async def _inner_get_response(self, *, messages, stream, options, **kwargs):
        self.calls.append(dict(options))
        return ChatResponse(messages=Message("assistant", ["invalid" if len(self.calls) == 1 else VALID]))


class RecordedModel(BaseChatModel):
    calls: list = []
    @property
    def _llm_type(self):
        return "offline-parity"
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls.append(dict(kwargs))
        return ChatResult(generations=[ChatGeneration(message=AIMessage(
            content="invalid" if len(self.calls) == 1 else VALID))])


@pytest.mark.parametrize("async_mode", [False, True])
def test_reasoning_options_preserved_through_real_chat_clients_and_parse_retry(async_mode):
    options = {"max_completion_tokens": 2048, "reasoning_effort": "low"}
    original = dict(options)
    client, model = RecordedClient(), RecordedModel()
    ma = asyncio.run(maf_review(Agent(client=client, name="reviewer"), "Review",
                                native_structured=False, options=options))
    lg = (asyncio.run(arun_review(model, "Review", native_structured=False, options=options))
          if async_mode else lg_review(model, "Review", native_structured=False, options=options))
    assert ma.model_dump() == lg.model_dump()
    assert options == original and len(client.calls) == len(model.calls) == 2
    for call in client.calls + model.calls:
        assert {key: call[key] for key in options} == options
        assert "max_tokens" not in call and "temperature" not in call
    assert all(call["tool_choice"] == "none" for call in client.calls)


@pytest.mark.parametrize("async_mode", [False, True])
def test_native_review_schema_cannot_be_overridden_by_generation_options(async_mode):
    from vinc_client import ReviewOutput
    class NativeModel(RecordedModel):
        def bind_tools(self, tools, *, tool_choice=None, **kwargs):
            assert tools == [ReviewOutput]
            return self.bind(tools=tools, tool_choice=tool_choice, **kwargs)
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            self.calls.append(dict(kwargs))
            assert kwargs["tools"] == [ReviewOutput]
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="",
                tool_calls=[{"name": "ReviewOutput", "id": "review", "args": json.loads(VALID)}]))])
    options = {"max_completion_tokens": 2048, "reasoning_effort": "low",
               "tools": ["untrusted"], "tool_choice": "required", "response_format": "untrusted"}
    original = dict(options)
    client, model = RecordedClient(), NativeModel()
    ma = asyncio.run(maf_review(Agent(client=client, name="reviewer"), "Review", options=options))
    lg = (asyncio.run(arun_review(model, "Review", options=options))
          if async_mode else lg_review(model, "Review", options=options))
    assert ma.model_dump() == lg.model_dump() and options == original
    assert all(call.get("tools", []) == [] and call["tool_choice"] == "none"
               and call["response_format"] is ReviewOutput for call in client.calls)
    assert all("response_format" not in call and call["tools"] == [ReviewOutput]
               for call in model.calls)

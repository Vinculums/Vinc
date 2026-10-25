"""Offline parity through both installed framework packages; no model service calls."""
import asyncio
import json
import hashlib

import httpx
import pytest
from agent_framework import Agent, BaseChatClient, ChatResponse, FunctionInvocationLayer, Message
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
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
        if request.url.path.startswith("/v1/nodes/"):
            body = {"executed": True, "found": True,
                    "node": {"id": "concept:doc.vinc.spec.fixture"}}
        else:
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
                # 모델이 받는 스키마 전체(이름·설명·인자)가 같아야 한다. title 은 어느 쪽에도 없다.
                # 근거: [[record:vin318-live-exp02-r2-parity-tool-calls-2-vs-1-2026-10-07]]
                ma_specs = [tool.to_json_schema_spec()["function"] for tool in ma]
                lg_specs = [convert_to_openai_tool(tool)["function"] for tool in lg]
                assert ma_specs == lg_specs
                assert '"title"' not in json.dumps(ma_specs)
                limit = ma_specs[1]["parameters"]["properties"]["limit"]
                assert (limit["default"], limit["minimum"], limit["maximum"]) == (5, 1, 20)
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
        assert sorted(sync_seen[:5], key=repr) == sorted(ma_seen[:5], key=repr) == sorted(lg_seen, key=repr)
        assert len(lg_seen) == 5 and all(row[2]["space"] == SPACE for row in lg_seen)
        assert all(cite in lg_block for cite in (ROLE, "concept:rule", "chunk:rule", "record:rule"))
        # 두 어댑터 모두 vinc-client 의 블록을 그대로 쓴다: 필수 문서의 인용 id 가 함께 간다.
        assert "(cite as concept:doc.vinc.spec.fixture)" in lg_block
        # VIN-314: 블록 머리의 Sources 줄 지시도 두 어댑터에 같은 바이트로 간다.
        assert '"Sources: [id] [id]"' in lg_block and '"Sources: none"' in lg_block
        assert len(lg_block) <= 24000
    asyncio.run(run())


@pytest.mark.parametrize("missing_status", [200, 404])
def test_missing_required_document_mirror_is_uncitable_in_both_frameworks(missing_status):
    seen = []
    def handle(request):
        if request.url.path.startswith("/v1/nodes/"):
            seen.append(request)
            body = ({"executed": True, "found": False, "node": None} if missing_status == 200
                    else {"error": {"code": "not_found"}})
            return httpx.Response(missing_status, json=body)
        return response(request, [])
    with VincClient(KEY, transport=httpx.MockTransport(handle)) as client:
        sync_block = lg_prefetch(client, ROLE, "Review.", space=SPACE)
    async def run():
        async with AsyncVincClient(KEY, transport=httpx.MockTransport(handle)) as client:
            return (await maf_prefetch(client, ROLE, "Review.", space=SPACE),
                    await lg_aprefetch(client, ROLE, "Review.", space=SPACE))
    ma_block, lg_block = asyncio.run(run())
    assert sync_block == ma_block == lg_block
    assert "vinc.spec.fixture (no citable id)" in lg_block
    assert "concept:doc.vinc.spec.fixture" not in lg_block
    assert "Preserve this evidence." in lg_block
    assert len(seen) == 3 and all(req.url.params["space"] == SPACE for req in seen)


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



class FiveBriefsThenDone(RecordedModel):
    """One turn of five vinc_brief calls, then an answer."""
    turns: int = 0
    def bind_tools(self, tools, **kwargs):
        return self
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.turns += 1
        if self.turns == 1:
            return ChatResult(generations=[ChatGeneration(message=AIMessage("", tool_calls=[
                {"name": "vinc_brief", "args": {"query": f"q{i}"}, "id": f"c{i}"} for i in range(5)]))])
        return ChatResult(generations=[ChatGeneration(message=AIMessage("done"))])


class FiveBriefsChat(FunctionInvocationLayer, BaseChatClient):
    async def _inner_get_response(self, *, messages, stream, options, **kwargs):
        from agent_framework import Content
        if not any(c.type == "function_result" for m in messages for c in m.contents):
            return ChatResponse(messages=Message("assistant", [Content.from_function_call(
                f"c{i}", "vinc_brief", arguments={"query": f"q{i}"}) for i in range(5)]))
        return ChatResponse(messages=Message("assistant", ["done"]))


def test_same_tool_call_cap_and_sentence_in_both_frameworks():
    # 한 런에 vinc 도구 4회. 다섯째는 두 패키지에서 같은 문장이 도구 결과로 돌아온다. LangGraph 는
    # 미들웨어가 그래프 상태에서, Agent Framework 는 런마다 새로 만든 도구가 센다.
    # 근거: [[decision:vin318-parity-is-judged-on-three-runs-with-a-shared-tool-call-cap-2026-10-07]]
    from langchain.agents import create_agent
    from langchain_core.messages import HumanMessage, ToolMessage
    from vinc_agent_framework import VincContextProvider
    from vinc_langgraph import VincContextMiddleware
    from vinc_client.tools import TOOL_CALLS_PER_RUN, cap_message
    assert TOOL_CALLS_PER_RUN == 4
    with VincClient(KEY, transport=httpx.MockTransport(lambda r: response(r, []))) as client:
        middleware = VincContextMiddleware(client=client, cache_seconds=0)
        agent = create_agent(FiveBriefsThenDone(), tools=lg_tools(client=client), middleware=[middleware])
        lg_results = [m.content for m in agent.invoke({"messages": [HumanMessage("Review.")]})["messages"]
                      if isinstance(m, ToolMessage)]
    async def run():
        async with AsyncVincClient(KEY, transport=httpx.MockTransport(lambda r: response(r, []))) as client:
            provider = VincContextProvider(client, expose_tools=True)
            res = await Agent(FiveBriefsChat(), context_providers=[provider]).run("Review.")
            return [str(c.result) for m in res.messages for c in m.contents
                    if c.type == "function_result"], provider.tool_calls_per_run
    ma_results, ma_limit = asyncio.run(run())
    assert middleware.tool_calls_per_run == ma_limit == 4
    assert lg_results[:4] == ma_results[:4] and all("concept:rule" in r for r in lg_results[:4])
    assert lg_results[4] == ma_results[4] == cap_message(4)
    assert cap_message(4) == "Vinc tool call cap reached (4 per run); answer from what you already have."


def test_same_sources_line_in_context_mode_in_both_frameworks():
    # context 모드(prefetch 없음)도 두 패키지가 같은 블록을 넣고, 그 머리가 SOURCES_LINE 이다.
    from agent_framework import SessionContext
    from vinc_agent_framework import VincContextProvider
    from vinc_client import SOURCES_LINE
    from vinc_langgraph import get_vinc_context
    with VincClient(KEY, transport=httpx.MockTransport(lambda r: response(r, []))) as client:
        lg_block = get_vinc_context(client, "Review evidence.")
    async def run():
        async with AsyncVincClient(KEY, transport=httpx.MockTransport(lambda r: response(r, []))) as client:
            ctx = SessionContext(input_messages=[Message("user", ["Review evidence."])])
            await VincContextProvider(client).before_run(agent=None, session=None, context=ctx, state={})
            return ctx.instructions[0]
    ma_block = asyncio.run(run())
    assert lg_block == ma_block and lg_block.startswith(SOURCES_LINE + "\n")


@pytest.mark.parametrize("instructions", ["Review the policy.", "근거에 따라 검토하세요."])
def test_native_agents_deliver_identical_plain_system_instructions(instructions):
    from langchain.agents import create_agent
    from langchain_core.messages import HumanMessage
    from vinc_agent_framework import VincContextProvider
    from vinc_langgraph import VincContextMiddleware

    class CaptureSystem(RecordedModel):
        system_contents: list = []
        def bind_tools(self, tools, **kwargs):
            return self
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            self.system_contents.append(messages[0].content)
            return ChatResult(generations=[ChatGeneration(message=AIMessage("done"))])

    model = CaptureSystem()
    with VincClient(KEY, transport=httpx.MockTransport(lambda req: response(req, []))) as client:
        create_agent(model, tools=lg_tools(client), system_prompt=instructions,
                     middleware=[VincContextMiddleware(client=client)]).invoke(
                         {"messages": [HumanMessage("Review evidence.")]})

    async def run():
        async with AsyncVincClient(KEY, transport=httpx.MockTransport(
                lambda req: response(req, []))) as client:
            chat = RecordedClient()
            await Agent(chat, instructions=instructions,
                        context_providers=[VincContextProvider(client, expose_tools=True)]).run(
                            "Review evidence.")
            return chat.calls[0]["instructions"]

    ma_instructions = asyncio.run(run())
    assert isinstance(model.system_contents[0], str)
    assert model.system_contents == [ma_instructions]


def test_write_helpers_share_the_retry_contract_in_both_frameworks():
    # VIN-601: 두 패키지의 쓰기 헬퍼가 같은 인자를 받고 같은 본문을 보낸다(create_only·operation_id 포함).
    import inspect
    from vinc_agent_framework import record_episode as maf_episode, write_package as maf_package
    from vinc_langgraph import (arecord_episode as lg_aepisode, awrite_package as lg_apackage,
                                record_episode as lg_episode, write_package as lg_package)
    def params(fn):
        return [p for p in inspect.signature(fn).parameters if p != "client"]
    assert params(maf_episode) == params(lg_episode) == params(lg_aepisode)
    assert params(maf_package) == params(lg_package) == params(lg_apackage)
    for name in ("create_only", "operation_id"):
        assert name in params(maf_episode) and name in params(maf_package)

    op = "00000000-0000-4000-8000-000000000601"
    episode = {"episode_id": "episode:run-one", "date": "2026-10-16",
               "create_only": True, "operation_id": op}
    package = {"claims": [{"claim": "Holds.", "evidence_ids": ["decision:policy"]}],
               "produced_by": "agent:writer", "run_id": "run-1", "package_id": "record:handoff",
               "space": SPACE, "create_only": True, "operation_id": op}
    sk = "vinc_sk_" + "b" * 32
    sync_seen, ma_seen, lg_seen = [], [], []
    with VincClient(sk, transport=httpx.MockTransport(lambda r: response(r, sync_seen))) as client:
        lg_episode(client, "Approved", **episode)
        lg_package(client, "Which?", "This.", **package)
    async def run():
        async with AsyncVincClient(sk, transport=httpx.MockTransport(
                lambda r: response(r, ma_seen))) as client:
            await maf_episode(client, "Approved", **episode)
            await maf_package(client, "Which?", "This.", **package)
        async with AsyncVincClient(sk, transport=httpx.MockTransport(
                lambda r: response(r, lg_seen))) as client:
            await lg_aepisode(client, "Approved", **episode)
            await lg_apackage(client, "Which?", "This.", **package)
    asyncio.run(run())
    assert sync_seen == ma_seen == lg_seen and len(ma_seen) == 2
    assert all((row[3]["create_only"], row[3]["operation_id"]) == (True, op) for row in ma_seen)


def test_same_disallowed_tools_are_hidden_and_refused_in_both_frameworks(caplog):
    # 같은 계약이면 두 패키지가 같은 도구를 모델에서 빼고, 그래도 불린 호출에 같은 문장을 돌려주고,
    # 어느 도구와도 맞지 않은 항목을 같은 경고로 한 번 알린다(정확한 이름 일치).
    import logging
    from agent_framework import ChatMiddlewareLayer, Content, tool as maf_tool
    from langchain.agents import create_agent
    from langchain_core.messages import HumanMessage, ToolMessage
    from langchain_core.tools import tool as lg_tool
    from vinc_agent_framework import VincContextProvider
    from vinc_client import RoleToolPolicy
    from vinc_langgraph import VincContextMiddleware

    def role_with_denylist(request, seen):
        if request.method == "GET" and request.url.path.startswith("/v1/roles/"):
            seen.append(request.url.path)
            return httpx.Response(200, json={"found": True, "id": ROLE, "props": {
                "reads": [], "disallowed_tools": ["Bash", "Edit"]}})
        return response(request, [])

    @lg_tool("Bash")
    def lg_bash(command: str) -> str:
        """Run a shell command."""
        raise AssertionError("a disallowed tool ran")

    @lg_tool("lookup")
    def lg_lookup(query: str) -> str:
        """A local lookup."""
        return "local answer"

    @maf_tool(name="Bash")
    def ma_bash(command: str) -> str:
        """Run a shell command."""
        raise AssertionError("a disallowed tool ran")

    @maf_tool(name="lookup")
    def ma_lookup(query: str) -> str:
        """A local lookup."""
        return "local answer"

    class LgModel(BaseChatModel):
        seen_tools: list = []

        @property
        def _llm_type(self):
            return "calls-bash"

        def bind_tools(self, tools, **kwargs):
            self.seen_tools.append(sorted(t.name for t in tools))
            return self

        def _generate(self, messages, **kwargs):
            if any(m.type == "tool" for m in messages):
                return ChatResult(generations=[ChatGeneration(message=AIMessage("done"))])
            return ChatResult(generations=[ChatGeneration(message=AIMessage("", tool_calls=[
                {"name": "Bash", "args": {"command": "ls"}, "id": "c1"}]))])

    class MaChat(FunctionInvocationLayer, ChatMiddlewareLayer, BaseChatClient):
        def __init__(self):
            super().__init__()
            self.seen_tools = []

        async def _inner_get_response(self, *, messages, stream, options, **kwargs):
            self.seen_tools.append(sorted(t.name for t in options.get("tools") or []))
            if any(c.type == "function_result" for m in messages for c in m.contents):
                return ChatResponse(messages=Message("assistant", ["done"]))
            return ChatResponse(messages=Message("assistant", [
                Content.from_function_call("c1", "Bash", arguments={"command": "ls"})]))

    lg_roles, ma_roles = [], []
    with caplog.at_level(logging.WARNING):
        with VincClient(KEY, transport=httpx.MockTransport(
                lambda r: role_with_denylist(r, lg_roles))) as client:
            model = LgModel()
            agent = create_agent(model, tools=[lg_bash, lg_lookup], middleware=[
                VincContextMiddleware(client=client, prefetch_role=ROLE)])
            state = agent.invoke({"messages": [HumanMessage("Review.")]})
            lg_refusal = [m.content for m in state["messages"] if isinstance(m, ToolMessage)]
        lg_warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        caplog.clear()

        async def run():
            async with AsyncVincClient(KEY, transport=httpx.MockTransport(
                    lambda r: role_with_denylist(r, ma_roles))) as client:
                chat = MaChat()
                res = await Agent(chat, tools=[ma_bash, ma_lookup], context_providers=[
                    VincContextProvider(client, prefetch_role=ROLE)]).run("Review.")
                return chat.seen_tools, [str(c.result) for m in res.messages for c in m.contents
                                         if c.type == "function_result"]
        ma_seen_tools, ma_refusal = asyncio.run(run())
        ma_warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]

    assert model.seen_tools == ma_seen_tools == [["lookup"], ["lookup"]]
    assert lg_refusal == ma_refusal == [RoleToolPolicy(ROLE, ["Bash"]).refusal("Bash")]
    assert lg_warnings == ma_warnings and len(lg_warnings) == 1 and ": Edit." in lg_warnings[0]
    assert len(lg_roles) == len(ma_roles) == 1


def test_same_role_pin_request_refusal_and_block_in_both_frameworks():
    # 같은 고정이면 두 패키지가 같은 요청(expected_hash)을 보내고, 역할이 바뀌었으면 같은 예외로
    # 모델 호출 전에 멈추며, 지금 해시로 고정하면 고정 없이 읽은 것과 같은 블록을 넣는다.
    from langchain.agents import create_agent
    from langchain_core.messages import HumanMessage
    from vinc_agent_framework import VincContextProvider
    from vinc_client import VincRoleChanged
    from vinc_langgraph import VincContextMiddleware

    current, stale = "c" * 64, "d" * 64

    def pinned_hub(request, seen):
        if request.method == "GET" and request.url.path.startswith("/v1/roles/"):
            expected = request.url.params.get("expected_hash")
            seen.append(expected)
            if expected not in (None, current):
                return httpx.Response(409, json={"error": {
                    "code": "role_changed", "message": "The role changed since it was pinned.",
                    "details": {"id": ROLE, "rev": 4, "hash": current}}})
            return httpx.Response(200, json={"found": True, "id": ROLE, "domain": "vinc/engineering",
                "props": {"reads": ["vinc.spec.fixture"], "mission": "Review evidence."},
                "rev": 4, "hash": current})
        return response(request, [])

    class LgModel(BaseChatModel):
        systems: list = []

        @property
        def _llm_type(self):
            return "records-system"

        def _generate(self, messages, **kwargs):
            self.systems.append(messages[0].text)
            return ChatResult(generations=[ChatGeneration(message=AIMessage("done"))])

    class MaChat(BaseChatClient):
        def __init__(self):
            super().__init__()
            self.instructions = []

        async def _inner_get_response(self, *, messages, stream, options, **kwargs):
            self.instructions.append(options.get("instructions") or "")
            return ChatResponse(messages=Message("assistant", ["done"]))

    def langgraph(pin, seen):
        model = LgModel()
        model.systems = []
        with VincClient(KEY, transport=httpx.MockTransport(lambda r: pinned_hub(r, seen))) as client:
            agent = create_agent(model, middleware=[VincContextMiddleware(
                client=client, prefetch_role=ROLE, prefetch_pin=pin)])
            try:
                agent.invoke({"messages": [HumanMessage("Review.")]})
            except VincRoleChanged as exc:
                return model.systems, exc
        return model.systems, None

    def maf(pin, seen):
        chat = MaChat()
        async def run():
            async with AsyncVincClient(KEY, transport=httpx.MockTransport(
                    lambda r: pinned_hub(r, seen))) as client:
                await Agent(chat, context_providers=[VincContextProvider(
                    client, prefetch_role=ROLE, prefetch_pin=pin)]).run("Review.")
        try:
            asyncio.run(run())
        except VincRoleChanged as exc:
            return chat.instructions, exc
        return chat.instructions, None

    lg_seen, ma_seen = [], []
    lg_calls, lg_error = langgraph(stale, lg_seen)
    ma_calls, ma_error = maf(stale, ma_seen)
    assert lg_calls == ma_calls == []
    assert (lg_error.code, lg_error.status, lg_error.details) == (
        ma_error.code, ma_error.status, ma_error.details) == ("role_changed", 409,
                                                              {"id": ROLE, "rev": 4, "hash": current})
    assert lg_seen == ma_seen == [stale]

    pinned = langgraph(current, [])[0], maf(current, [])[0]
    plain = langgraph(None, [])[0], maf(None, [])[0]
    assert pinned == plain
    assert len(pinned[0]) == len(pinned[1]) == 1
    assert pinned[0][0] == pinned[1][0] and ROLE in pinned[0][0]


def test_run_record_helpers_send_the_same_body_in_both_frameworks():
    # VIN-778: 실행 기록 헬퍼 셋(LangGraph 동기·비동기, Agent Framework)이 같은 본문을 보낸다.
    from vinc_agent_framework import record_run as maf_run
    from vinc_langgraph import arecord_run as lg_arun, record_run as lg_run
    fields = {"role": ROLE, "outcome": "success", "steps": 2, "tool_calls": 3,
              "cited_ids": ["record:rule"], "date": "2026-10-25"}
    sk = "vinc_sk_" + "b" * 32
    sync_seen, ma_seen, lg_seen = [], [], []
    with VincClient(sk, transport=httpx.MockTransport(lambda r: response(r, sync_seen))) as client:
        lg_run(client, "run-778", **fields)

    async def run():
        async with AsyncVincClient(sk, transport=httpx.MockTransport(
                lambda r: response(r, ma_seen))) as client:
            await maf_run(client, "run-778", **fields)
        async with AsyncVincClient(sk, transport=httpx.MockTransport(
                lambda r: response(r, lg_seen))) as client:
            await lg_arun(client, "run-778", **fields)
    asyncio.run(run())
    assert sync_seen == ma_seen == lg_seen
    posts = [seen for seen in sync_seen if seen[0] == "POST"]
    assert len(posts) == 1 and posts[0][3]["nodes"][0]["props"]["run_id"] == "run-778"


_PROFILE = next(p for p in json.loads((__import__("pathlib").Path(__file__).resolve().parents[1]
                / "fixtures" / "output_gate" / "proposed-role-checks.json").read_text(
                    encoding="utf-8"))["profiles"] if p["id"] == "concept:agent-dev-core")


def _build_hub(seen):
    def handler(request):
        seen.append((request.method, request.url.path,
                     json.loads(request.content) if request.content else None))
        path = request.url.path
        if path.startswith("/v1/roles/"):
            body = {"found": True, "id": "concept:agent-dev-core", "title": "Core developer",
                    "rev": 2, "hash": "a" * 64,
                    "props": {**_PROFILE, "mission": "m", "tier": "execute", "rung": "strong",
                              "never": ["merge"], "reads": []},
                    "works_with": {"hands_to": [{"from": "concept:agent-dev-core",
                                                 "to": "concept:agent-gate-runner"}],
                                   "receives_from": []}}
        elif path.startswith("/v1/nodes/"):
            body = {"executed": True, "found": True, "node": {"id": path.rsplit("/", 1)[-1]}}
        elif path == "/v1/fragments":
            body = {"merged": {"nodes": {"created": 1}}, "stored": [], "warnings": []}
        elif path.endswith("/brief"):
            body = {"status": "resolved", "node": {"id": "decision:fixture",
                    "cite_id": "decision:fixture"}, "one_liner": "Keep it."}
        else:
            body = {"node_hits": []}
        return httpx.Response(200, json=body)
    return handler


@pytest.mark.parametrize("reply,status", [
    ("The change reads correctly.\nSources: decision:fixture", "done"),
    ("I merged the pull request and deployed it.", "blocked")])
def test_build_agent_runs_the_same_in_both_frameworks(reply, status):
    # VIN-785: 역할 주소 하나로 만든 에이전트가 두 프레임워크에서 같은 판정·같은 실행 기록을 낸다.
    from vinc_agent_framework import build_agent as maf_build
    from vinc_langgraph import build_agent as lg_build
    sk = "vinc_sk_" + "b" * 32

    class Model(BaseChatModel):
        @property
        def _llm_type(self):
            return "offline-parity"

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content=reply))])

    class Chat(BaseChatClient):
        async def _inner_get_response(self, *, messages, stream, options, **kwargs):
            return ChatResponse(messages=Message("assistant", [reply]))

    lg_seen, ma_seen = [], []
    with VincClient(sk, transport=httpx.MockTransport(_build_hub(lg_seen))) as client:
        lg_run = lg_build("dev-core", Model(), client=client).invoke("Review.", run_id="run-785")

    async def go():
        async with AsyncVincClient(sk, transport=httpx.MockTransport(_build_hub(ma_seen))) as client:
            agent = await maf_build("dev-core", Chat(), client=client)
            return await agent.run("Review.", run_id="run-785")
    ma_run = asyncio.run(go())

    assert lg_run.status == ma_run.status == status
    assert lg_run.output == ma_run.output and lg_run.cited_ids == ma_run.cited_ids
    assert (lg_run.gate and lg_run.gate.verdict) == (ma_run.gate and ma_run.gate.verdict)

    def record(seen):
        post = next(s for s in seen if s[0] == "POST" and s[1] == "/v1/fragments")
        props = dict(post[2]["nodes"][0]["props"])
        return props.pop("host"), props
    (lg_host, lg_props), (ma_host, ma_props) = record(lg_seen), record(ma_seen)
    assert (lg_host, ma_host) == ("langgraph", "agent-framework")
    assert lg_props == ma_props and lg_props["run_id"] == "run-785"

"""Offline parity of the CrewAI adapter with the LangGraph and Agent Framework adapters.

All three come from one ``vincs[all]`` install and share ``vinc.sdk``; no model service is
called. The other parity files test the older single-framework distributions.
"""
import os

# CrewAI 의 원격 수집과 첫 실행 질문을 끈다. crewai 를 가져오기 전에 둬야 한다.
for _name, _value in {"CREWAI_TESTING": "true", "CREWAI_DISABLE_TELEMETRY": "true",
                      "OTEL_SDK_DISABLED": "true", "CREWAI_TRACING_ENABLED": "false"}.items():
    os.environ.setdefault(_name, _value)

import asyncio  # noqa: E402
import hashlib  # noqa: E402
import inspect  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402

# vincs[all] 이 없는 환경(단일 배포판 패리티를 도는 venv)에서는 이 파일을 건너뛴다.
pytest.importorskip("vinc.integrations.crewai")

from agent_framework import (Agent as MafAgent, AgentContext, AgentResponse,  # noqa: E402
                             BaseChatClient, ChatMiddlewareLayer, ChatResponse, Content,
                             FunctionInvocationLayer, Message, tool as maf_tool)
from crewai import BaseLLM, Crew, Task, TaskOutput  # noqa: E402
from crewai.tools import BaseTool  # noqa: E402
from crewai.utilities.agent_utils import convert_tools_to_openai_schema  # noqa: E402
from langchain.agents import create_agent as lg_create_agent  # noqa: E402
from langchain_core.language_models.chat_models import BaseChatModel  # noqa: E402
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: E402
from langchain_core.outputs import ChatGeneration, ChatResult  # noqa: E402
from langchain_core.tools import tool as lg_tool  # noqa: E402
from langchain_core.utils.function_calling import convert_to_openai_tool  # noqa: E402
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from langgraph.graph import END, START, StateGraph  # noqa: E402
from langgraph.types import Command  # noqa: E402
from pydantic import Field  # noqa: E402
from typing_extensions import TypedDict  # noqa: E402

import vinc.integrations.agent_framework as maf  # noqa: E402
import vinc.integrations.crewai as cr  # noqa: E402
import vinc.integrations.langgraph as lg  # noqa: E402
from vinc import AsyncVinc, Vinc  # noqa: E402
from vinc.sdk import (ApprovalDecision, OutputGate, OutputGateStopped, RoleOutput,  # noqa: E402
                      VincNotFound, VincRoleChanged)
from vinc.sdk.tools import cap_message  # noqa: E402

KEY = "vinc_ro_" + "a" * 32
SK = "vinc_sk_" + "b" * 32
SPACE = "01a0a0e0-3f91-7128-9876-6b543f28de0a"
ROLE = "concept:agent-reviewer-judgement"
TASK = "Review the policy."
DOC = "Preserve this evidence."
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "output_gate"
CASES = json.loads((FIXTURES / "hand-labelled.json").read_text(encoding="utf-8"))["cases"]
PROFILES = {p["id"]: p for p in json.loads(
    (FIXTURES / "proposed-role-checks.json").read_text(encoding="utf-8"))["profiles"]}


def response(request, seen, *, role_props=None, role_hash=None):
    seen.append((request.method, request.url.path, dict(request.url.params),
                 json.loads(request.content) if request.content else None))
    path = request.url.path
    if path.startswith("/v1/roles/"):
        pin = request.url.params.get("expected_hash")
        if role_hash and pin not in (None, role_hash):
            return httpx.Response(409, json={"error": {
                "code": "role_changed", "message": "The role changed since it was pinned.",
                "details": {"id": ROLE, "rev": 4, "hash": role_hash}}})
        body = {"found": True, "id": ROLE, "title": "Code Reviewer", "domain": "vinc/engineering",
                "props": role_props or {"reads": ["vinc.spec.fixture"], "mission": "Review evidence.",
                                        "job_title": "Code Reviewer"},
                "rev": 4, "hash": role_hash or "c" * 64}
    elif path.startswith("/v1/documents/"):
        body = {"executed": True, "verified": True, "doc_id": "vinc.spec.fixture", "text": DOC,
                "content_hash": hashlib.sha256(DOC.encode()).hexdigest()}
    elif path.startswith("/v1/nodes/"):
        body = {"executed": True, "found": True, "node": {"id": "concept:doc.vinc.spec.fixture"}}
    elif path.endswith("brief"):
        query = json.loads(request.content)["query"]
        body = {"status": "resolved", "node": {"id": "concept:rule", "cite_id": "concept:rule",
                "title": "Rule"}, "one_liner": query, "excerpts": [{"doc_id": "vinc.spec.fixture",
                "cite_id": "chunk:rule", "doc_title": "Rule", "excerpt": DOC}]}
    elif path.endswith("search"):
        body = {"node_hits": [{"id": "record:rule", "cite_id": "record:rule", "title": "Rule"}],
                "rows": [{"doc_id": "vinc.spec.fixture", "cite_id": "chunk:rule",
                          "doc_title": "Rule", "snippet": DOC}]}
    else:
        body = {"executed": True, "merged": 1, "warnings": []}
    return httpx.Response(200, json=body)


def mock(seen, **kwargs):
    return httpx.MockTransport(lambda request: response(request, seen, **kwargs))


def text(value):
    return value if isinstance(value, str) else "".join(part.text for part in value)


class ScriptedLLM(BaseLLM):
    script: list[Any] = Field(default_factory=list)
    seen: list[dict] = Field(default_factory=list)

    def call(self, messages, tools=None, callbacks=None, available_functions=None,
             from_task=None, from_agent=None, response_model=None):
        self.seen.append({"messages": [dict(m) for m in messages],
                          "tools": [t["function"]["name"] for t in tools or []]})
        return self.script.pop(0) if self.script else "Done.\nSources: none"

    def supports_function_calling(self):
        return True


def crew_run(agent, **task_kwargs):
    task = Task(description=TASK, expected_output="A review", agent=agent, **task_kwargs)
    return Crew(agents=[agent], tasks=[task]).kickoff()


def crew_user_text(call):
    return next(m["content"] for m in call["messages"] if m["role"] == "user")


def crew_tool_results(call):
    return [m["content"] for m in call["messages"] if m["role"] == "tool"]


def test_same_read_tools_schema_results_and_requests_in_three_adapters():
    async def run():
        ma_seen, lg_seen, cr_seen = [], [], []
        async with AsyncVinc(KEY, transport=mock(ma_seen)) as ma_client, \
                AsyncVinc(KEY, transport=mock(lg_seen)) as lg_client:
            ma, lgt = maf.create_vinc_tools(ma_client), lg.create_vinc_tools(async_client=lg_client)
            crt = cr.create_vinc_tools(Vinc(KEY, transport=mock(cr_seen)))
            assert [t.name for t in ma] == [t.name for t in lgt] == [t.name for t in crt] == [
                "vinc_brief", "vinc_search"]
            ma_specs = [t.to_json_schema_spec()["function"] for t in ma]
            assert ma_specs == [convert_to_openai_tool(t)["function"] for t in lgt]
            assert ma_specs == [{"name": t.name, "description": t.description,
                                 "parameters": t.args_schema.model_json_schema()} for t in crt]
            # CrewAI 는 공급자에 보낼 때 strict 함수 호출로 바꿔 모든 칸을 required 로 적는다.
            # 칸·설명·기본값·범위는 그대로다.
            for spec, sent in zip(ma_specs, convert_tools_to_openai_schema(crt)[0]):
                assert sent["function"]["parameters"]["properties"] == spec["parameters"]["properties"]
                assert sent["function"]["description"] == spec["description"]
            for index in (0, 1):
                left = text(await ma[index].invoke(query="Review evidence."))
                right = await lgt[index].ainvoke({"query": "Review evidence."})
                assert left == right == crt[index].run(query="Review evidence.")
                assert left == await crt[index].arun(query="Review evidence.")
            # CrewAI 쪽은 도구마다 동기·비동기 두 번 불렀다.
            assert ma_seen == lg_seen == cr_seen[0::2] == cr_seen[1::2]
    asyncio.run(run())


def test_missing_id_sentence_and_space_error_match_in_three_adapters():
    def handle(code):
        def answer(request):
            if request.url.path.endswith("brief") and json.loads(request.content)["query"] == "decision:x":
                return httpx.Response(404, json={"error": {"code": code, "message": "missing"}})
            return response(request, [])
        return httpx.MockTransport(answer)
    expected = "Vinc found nothing for that (not_found); try a different query."
    async def run(transport):
        async with AsyncVinc(KEY, transport=transport) as client:
            return (text(await maf.create_vinc_tools(client)[0].invoke(query="decision:x")),
                    await lg.create_vinc_tools(async_client=client)[0].ainvoke({"query": "decision:x"}))
    assert asyncio.run(run(handle("not_found"))) == (expected, expected)
    assert cr.create_vinc_tools(Vinc(KEY, transport=handle("not_found")))[0].run(query="decision:x") == expected
    with pytest.raises(VincNotFound, match="space_not_found"):
        asyncio.run(run(handle("space_not_found")))
    with pytest.raises(VincNotFound, match="space_not_found"):
        cr.create_vinc_tools(Vinc(KEY, transport=handle("space_not_found")))[0].run(query="decision:x")


@pytest.mark.parametrize("limit", [0, 21])
def test_out_of_range_search_is_refused_before_http_in_three_adapters(limit):
    seen = []
    async def run():
        async with AsyncVinc(KEY, transport=mock(seen)) as client:
            for call in (lambda: maf.create_vinc_tools(client)[1].invoke(query="Review", limit=limit),
                         lambda: lg.create_vinc_tools(async_client=client)[1].ainvoke(
                             {"query": "Review", "limit": limit})):
                with pytest.raises(Exception, match="limit"):
                    await call()
    asyncio.run(run())
    with pytest.raises(Exception, match="limit"):
        cr.create_vinc_tools(Vinc(KEY, transport=mock(seen)))[1].run(query="Review", limit=limit)
    assert not seen


@pytest.mark.parametrize("task", [TASK, "한국어 근거를 확인하세요."])
def test_same_role_prefetch_block_and_reads_in_three_adapters(task):
    lg_seen, ma_seen, cr_seen = [], [], []
    with Vinc(KEY, transport=mock(lg_seen)) as client:
        lg_block = lg.prefetch(client, ROLE, task)
    async def run():
        async with AsyncVinc(KEY, transport=mock(ma_seen)) as client:
            return await maf.prefetch(client, ROLE, task)
    ma_block = asyncio.run(run())
    llm = ScriptedLLM(model="scripted")
    agent = cr.create_agent(Vinc(KEY, transport=mock(cr_seen)), ROLE, llm=llm)
    Crew(agents=[agent], tasks=[Task(description=task, expected_output="x", agent=agent)]).kickoff()
    assert lg_block == ma_block and lg_block in crew_user_text(llm.seen[0])
    # CrewAI 는 에이전트를 지을 때 역할을 한 번 더 읽는다. 런의 읽기는 다른 둘과 같은 다섯이다.
    run_reads = sorted(cr_seen[1:], key=repr)
    assert run_reads == sorted(lg_seen, key=repr) == sorted(ma_seen, key=repr) and len(run_reads) == 5
    assert '"Sources: [id] [id]"' in lg_block and len(lg_block) <= 24000


class FiveBriefsModel(BaseChatModel):
    turns: int = 0

    @property
    def _llm_type(self):
        return "five-briefs"

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
        if not any(c.type == "function_result" for m in messages for c in m.contents):
            return ChatResponse(messages=Message("assistant", [Content.from_function_call(
                f"c{i}", "vinc_brief", arguments={"query": f"q{i}"}) for i in range(5)]))
        return ChatResponse(messages=Message("assistant", ["done"]))


def test_same_tool_call_cap_and_sentence_in_three_adapters():
    with Vinc(KEY, transport=mock([])) as client:
        agent = lg_create_agent(FiveBriefsModel(), tools=lg.create_vinc_tools(client=client),
                                middleware=[lg.VincContextMiddleware(client=client, cache_seconds=0)])
        lg_results = [m.content for m in agent.invoke({"messages": [HumanMessage("Review.")]})["messages"]
                      if isinstance(m, ToolMessage)]
    async def run():
        async with AsyncVinc(KEY, transport=mock([])) as client:
            res = await MafAgent(FiveBriefsChat(), context_providers=[
                maf.VincContextProvider(client, expose_tools=True)]).run("Review.")
            return [str(c.result) for m in res.messages for c in m.contents if c.type == "function_result"]
    ma_results = asyncio.run(run())
    llm = ScriptedLLM(model="scripted", script=[[
        {"id": f"c{i}", "function": {"name": "vinc_brief", "arguments": json.dumps({"query": f"q{i}"})}}
        for i in range(5)]])
    crew_run(cr.create_agent(Vinc(KEY, transport=mock([])), ROLE, llm=llm, expose_tools=True))
    cr_results = crew_tool_results(llm.seen[1])
    assert lg_results[:4] == ma_results[:4] == cr_results[:4]
    assert lg_results[4] == ma_results[4] == cr_results[4] == cap_message(4)


def test_same_disallowed_tools_are_hidden_and_warned_in_three_adapters(caplog):
    props = {"reads": [], "mission": "Review.", "disallowed_tools": ["Bash", "Edit"]}

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

    class CrBash(BaseTool):
        name: str = "Bash"
        description: str = "Run a shell command."

        def _run(self, command: str) -> str:
            raise AssertionError("a disallowed tool ran")

    class CrLookup(BaseTool):
        name: str = "lookup"
        description: str = "A local lookup."

        def _run(self, query: str) -> str:
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

    def warnings():
        found = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING
                 and r.name == "vinc_client"]
        caplog.clear()
        return found

    with caplog.at_level(logging.WARNING):
        with Vinc(KEY, transport=mock([], role_props=props)) as client:
            model = LgModel()
            lg_create_agent(model, tools=[lg_bash, lg_lookup], middleware=[
                lg.VincContextMiddleware(client=client, prefetch_role=ROLE)]).invoke(
                    {"messages": [HumanMessage("Review.")]})
        lg_warnings = warnings()

        async def run():
            async with AsyncVinc(KEY, transport=mock([], role_props=props)) as client:
                chat = MaChat()
                await MafAgent(chat, tools=[ma_bash, ma_lookup], context_providers=[
                    maf.VincContextProvider(client, prefetch_role=ROLE)]).run("Review.")
                return chat.seen_tools
        ma_seen_tools = asyncio.run(run())
        ma_warnings = warnings()

        llm = ScriptedLLM(model="scripted", script=[
            [{"id": "c1", "function": {"name": "bash", "arguments": json.dumps({"command": "ls"})}}]])
        crew_run(cr.create_agent(Vinc(KEY, transport=mock([], role_props=props)), ROLE, llm=llm,
                                 tools=[CrBash(), CrLookup()]))
        cr_warnings = warnings()

    assert model.seen_tools == ma_seen_tools == [c["tools"] for c in llm.seen] == [["lookup"], ["lookup"]]
    assert lg_warnings == ma_warnings == cr_warnings and len(lg_warnings) == 1
    # 숨긴 도구를 그래도 부르면 LangGraph·Agent Framework 는 역할의 거절 문장을, CrewAI 는 실행기에
    # 그 도구가 없으므로 자기 문장을 돌려준다. 어느 쪽에서도 도구는 돌지 않는다.
    assert crew_tool_results(llm.seen[1]) == ["Tool not found"]


def test_same_role_pin_refusal_in_three_adapters():
    current, stale = "c" * 64, "d" * 64

    class NoModel(BaseChatModel):
        @property
        def _llm_type(self):
            return "never-called"

        def _generate(self, messages, **kwargs):
            raise AssertionError("the model ran")

    with Vinc(KEY, transport=mock([], role_hash=current)) as client:
        with pytest.raises(VincRoleChanged) as lg_error:
            lg_create_agent(NoModel(), middleware=[lg.VincContextMiddleware(
                client=client, prefetch_role=ROLE, prefetch_pin=stale)]).invoke(
                    {"messages": [HumanMessage("Review.")]})

    class NoChat(BaseChatClient):
        async def _inner_get_response(self, **kwargs):
            raise AssertionError("the model ran")

    async def run():
        async with AsyncVinc(KEY, transport=mock([], role_hash=current)) as client:
            await MafAgent(NoChat(), context_providers=[maf.VincContextProvider(
                client, prefetch_role=ROLE, prefetch_pin=stale)]).run("Review.")
    with pytest.raises(VincRoleChanged) as ma_error:
        asyncio.run(run())
    with pytest.raises(VincRoleChanged) as cr_error:
        cr.create_agent(Vinc(KEY, transport=mock([], role_hash=current)), ROLE, prefetch_pin=stale)
    shape = [(e.value.code, e.value.status, e.value.details) for e in (lg_error, ma_error, cr_error)]
    assert shape[0] == shape[1] == shape[2] == ("role_changed", 409, {"id": ROLE, "rev": 4, "hash": current})


def test_write_helpers_send_the_same_bodies_in_three_adapters():
    def params(fn):
        return [p for p in inspect.signature(fn).parameters if p != "client"]
    for name in ("record_episode", "write_package", "record_run"):
        assert params(getattr(cr, name)) == params(getattr(lg, name)) == params(getattr(maf, name))
        assert params(getattr(cr, "a" + name)) == params(getattr(lg, "a" + name))
    op = "00000000-0000-4000-8000-000000000384"
    episode = {"episode_id": "episode:run-one", "date": "2026-10-25", "create_only": True,
               "operation_id": op}
    package = {"claims": [{"claim": "Holds.", "evidence_ids": ["decision:policy"]}],
               "produced_by": "agent:writer", "run_id": "run-1", "package_id": "record:handoff",
               "space": SPACE, "create_only": True, "operation_id": op}
    run_fields = {"role": ROLE, "outcome": "success", "steps": 2, "tool_calls": 3,
                  "cited_ids": ["record:rule"], "date": "2026-10-25"}
    lg_seen, ma_seen, cr_seen = [], [], []
    with Vinc(SK, transport=mock(lg_seen)) as client:
        lg.record_episode(client, "Approved", **episode)
        lg.write_package(client, "Which?", "This.", **package)
        lg.record_run(client, "run-384", **run_fields)
    with Vinc(SK, transport=mock(cr_seen)) as client:
        cr.record_episode(client, "Approved", **episode)
        cr.write_package(client, "Which?", "This.", **package)
        cr.record_run(client, "run-384", **run_fields)
    async def run():
        async with AsyncVinc(SK, transport=mock(ma_seen)) as client:
            await maf.record_episode(client, "Approved", **episode)
            await maf.write_package(client, "Which?", "This.", **package)
            await maf.record_run(client, "run-384", **run_fields)
    asyncio.run(run())
    assert lg_seen == ma_seen == cr_seen
    assert len([row for row in cr_seen if row[0] == "POST"]) == 3


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_same_output_gate_verdict_in_three_adapters(case):
    gate = OutputGate(PROFILES[case["role_id"]], self_authored=case["self_authored"])
    answer = case["answer"]

    def outcome(call):
        try:
            call()
            return "pass"
        except OutputGateStopped as exc:
            return exc.result.verdict

    middleware = lg.create_output_gate_middleware(gate)
    lg_verdict = outcome(lambda: middleware.after_model({"messages": [AIMessage(answer)]}, None))
    context = AgentContext(agent=None, messages=[])

    async def produce():
        context.result = AgentResponse(messages=[Message("assistant", [answer])])
    ma_verdict = outcome(lambda: asyncio.run(maf.VincOutputGateMiddleware(gate).process(context, produce)))
    guardrail = cr.create_output_guardrail(gate)
    cr_verdict = outcome(lambda: guardrail(TaskOutput(description=TASK, raw=answer, agent="x")))
    assert lg_verdict == ma_verdict == cr_verdict == case["expected"]


class ApprovalState(TypedDict, total=False):
    role_output: dict
    vinc_approval: dict


@pytest.mark.parametrize("approved", [True, False])
def test_same_person_only_request_and_result_in_langgraph_and_crewai(approved):
    gate = OutputGate(PROFILES[ROLE])
    draft = RoleOutput(text="Ready.", person_only_requests=[{"action": "merge", "reason": "release"}])
    graph = StateGraph(ApprovalState)
    graph.add_node("approval", lg.create_person_only_node(gate, role_id=ROLE, run_id="run-384"))
    graph.add_edge(START, "approval")
    graph.add_edge("approval", END)
    app = graph.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "run-384"}}
    lg_request = app.invoke({"role_output": draft.model_dump()}, config)["__interrupt__"][0].value
    decision = {"request_id": lg_request["request_id"], "approved": approved}
    lg_result = app.invoke(Command(resume=decision), config)["vinc_approval"]

    asked = []
    llm = ScriptedLLM(model="scripted", script=[draft.model_dump_json()] * 3)
    agent = cr.create_agent(Vinc(KEY, transport=mock([])), ROLE, llm=llm)
    task = Task(description=TASK, expected_output="x", agent=agent, human_input=True)
    step = cr.VincPersonOnlyInput(gate, role_id=ROLE, run_id="run-384", tasks=[task],
                                  decide=lambda request: asked.append(request) or ApprovalDecision(**decision))
    with step.installed():
        if approved:
            Crew(agents=[agent], tasks=[task]).kickoff()
            cr_result = step.result(task).as_dict()
        else:
            with pytest.raises(cr.VincApprovalRejected) as exc:
                Crew(agents=[agent], tasks=[task]).kickoff()
            cr_result = exc.value.result.as_dict()
    assert [r.model_dump() for r in asked] == [lg_request] and len(llm.seen) == 1
    assert cr_result == lg_result

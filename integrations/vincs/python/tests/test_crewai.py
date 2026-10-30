"""vinc.integrations.crewai through real CrewAI crews with a scripted model; no model service."""
import os

from importlib.metadata import PackageNotFoundError, version

# CrewAI 의 원격 수집과 첫 실행 질문을 끄고, 그 로컬 저장소를 시험 전용 자리에 둔다(판마다 따로:
# 판이 다른 CrewAI 가 남긴 행을 읽지 않게). crewai 를 가져오기 전에 둬야 한다.
try:
    _store = "vinc-crewai-tests-" + version("crewai")
except PackageNotFoundError:
    _store = "vinc-crewai-tests"
for _name, _value in {"CREWAI_TESTING": "true", "CREWAI_DISABLE_TELEMETRY": "true",
                      "OTEL_SDK_DISABLED": "true", "CREWAI_TRACING_ENABLED": "false",
                      "CREWAI_STORAGE_DIR": _store}.items():
    os.environ.setdefault(_name, _value)

import asyncio  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
from typing import Any  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402

pytest.importorskip("crewai")

from crewai import BaseLLM, Crew, Task  # noqa: E402
from crewai.core.providers.human_input import reset_provider, set_provider  # noqa: E402
from crewai.tools import BaseTool  # noqa: E402
from pydantic import Field  # noqa: E402

from vinc import AsyncVinc, Vinc  # noqa: E402
from vinc.sdk import (PENDING_HEADER, ApprovalDecision, OutputGate, OutputGateStopped, RoleOutput,  # noqa: E402
                      VincInvalidArgument, VincNotFound, VincRoleChanged, build_package_fragment)
from vinc.sdk.tools import BriefArgs, SearchArgs, cap_message  # noqa: E402
from vinc.integrations.crewai import (VincAgent, VincApprovalRejected, VincPersonOnlyInput,  # noqa: E402
                                      abuild_agent, build_agent,
                                      create_agent, create_output_guardrail, create_vinc_tools,
                                      record_run, role_fields)

KEY = "vinc_ro_" + "a" * 32
WRITE_KEY = "vinc_sk_" + "b" * 32
ROLE = "concept:agent-reviewer-judgement"
TASK = "Review the policy."
DOC = "Preserve this evidence."
PROPS = {
    "job_title": "Code Reviewer", "mission": "Reads a diff  adversarially.\n Does not edit.",
    "never": ["edit", "merge"], "person_only": [{"action": "merge", "reason": "merge"}],
    "reads": ["vinc.spec.fixture"], "disallowed_tools": ["Bash", "Edit"],
    "gates": [{"id": "person_only", "check": {"type": "person_only"}, "on_fail": "interrupt"},
              {"id": "side_effect_evidence", "check": {"type": "side_effect_evidence"}, "on_fail": "block"},
              {"id": "handoff_manifest", "check": {"type": "handoff_manifest"}, "on_fail": "block"}],
    "never_checks": [{"type": "forbidden_actions", "actions": ["edit", "merge"]}],
}
PACKAGE = build_package_fragment("Which policy?", "Keep the exact recommendation.",
    claims=[{"claim": "Keep the source state.", "evidence_ids": ["record:rule"]}],
    produced_by="producer", run_id="run-0", package_id="record:package")["nodes"][0]


def hub(seen: list, *, props: dict | None = None, role_hash: str | None = None):
    """A Vinc hub answering the reads a role run makes, recording every request."""
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path,
                     json.loads(request.content) if request.content else None))
        path = request.url.path
        if path.startswith("/v1/roles/"):
            pin = request.url.params.get("expected_hash")
            if role_hash and pin not in (None, role_hash):
                return httpx.Response(409, json={"error": {"code": "role_changed", "message": "changed",
                                      "details": {"id": ROLE, "rev": 2, "hash": role_hash}}})
            body = {"found": True, "id": ROLE, "title": "Code Reviewer", "domain": "vinc/engineering",
                    "props": props or PROPS, "rev": 1, "hash": role_hash or "c" * 64}
        elif path.startswith("/v1/documents/"):
            body = {"executed": True, "verified": True, "doc_id": "vinc.spec.fixture", "text": DOC,
                    "content_hash": hashlib.sha256(DOC.encode()).hexdigest()}
        elif path.startswith("/v1/nodes/"):
            node = PACKAGE if path.endswith("record:package") else (
                {"id": "record:rule", "title": "Rule"} if path.endswith("record:rule")
                else {"id": "concept:doc.vinc.spec.fixture"})
            body = {"executed": True, "found": True, "node": node}
        elif path.endswith("/brief"):
            body = {"status": "resolved", "node": {"id": "concept:rule", "cite_id": "concept:rule",
                    "title": "Rule"}, "one_liner": "Keep it.", "excerpts": [{"doc_id": "vinc.spec.fixture",
                    "cite_id": "chunk:rule", "doc_title": "Rule", "excerpt": DOC}]}
        elif path.endswith("/search"):
            body = {"node_hits": [{"id": "record:rule", "cite_id": "record:rule", "title": "Rule"}],
                    "rows": [{"doc_id": "vinc.spec.fixture", "cite_id": "chunk:rule",
                              "doc_title": "Rule", "snippet": DOC}]}
        else:
            body = {"executed": True, "merged": 1, "warnings": []}
        return httpx.Response(200, json=body)
    return handle


def client(seen: list, key: str = KEY, **kwargs: Any) -> Vinc:
    return Vinc(key, transport=httpx.MockTransport(hub(seen, **kwargs)))


def calls(name: str, *queries: str) -> list[dict]:
    return [{"id": f"c{i}", "function": {"name": name, "arguments": json.dumps({"query": q})}}
            for i, q in enumerate(queries)]


class ScriptedLLM(BaseLLM):
    """Answers from ``script`` in order and records what each call was given."""
    script: list[Any] = Field(default_factory=list)
    seen: list[dict] = Field(default_factory=list)

    def call(self, messages, tools=None, callbacks=None, available_functions=None,
             from_task=None, from_agent=None, response_model=None):
        self.seen.append({"messages": [dict(m) for m in messages],
                          "tools": [t["function"]["name"] for t in tools or []]})
        step = self.script.pop(0) if self.script else "Done.\nSources: none"
        if isinstance(step, Exception):
            raise step
        return step

    async def acall(self, *args, **kwargs):
        return self.call(*args, **kwargs)

    def supports_function_calling(self) -> bool:
        return True


def user_text(call: dict) -> str:
    return next(m["content"] for m in call["messages"] if m["role"] == "user")


def tool_results(call: dict) -> list[str]:
    return [m["content"] for m in call["messages"] if m["role"] == "tool"]


def run_crew(agent, *, description: str = TASK, **task_kwargs: Any):
    task = Task(description=description, expected_output="A review", agent=agent, **task_kwargs)
    return Crew(agents=[agent], tasks=[task]).kickoff(), task


class Bash(BaseTool):
    name: str = "Bash"
    description: str = "Run a shell command."

    def _run(self, command: str) -> str:
        raise AssertionError("a disallowed tool ran")


class Lookup(BaseTool):
    name: str = "lookup"
    description: str = "A local lookup."

    def _run(self, query: str) -> str:
        return "local answer"


def test_role_fields_take_the_job_title_mission_prohibitions_and_person_only_actions():
    fields = role_fields({"found": True, "id": ROLE, "title": "agent role: x", "props": PROPS})
    assert fields["role"] == "Code Reviewer"
    assert fields["goal"] == "Reads a diff adversarially. Does not edit."
    assert fields["backstory"].splitlines() == [
        f"You act as the Vinc role {ROLE} (Code Reviewer).", "", "Never:", "- edit", "- merge", "",
        "Only a person does these. Propose them and stop; do not do them:", "- merge: merge"]
    with pytest.raises(ValueError, match="mission"):
        role_fields({"found": True, "id": ROLE, "props": {}})


def test_a_task_run_reads_the_role_once_and_gets_the_full_prefetch_block_as_context():
    seen, expected = [], []
    llm = ScriptedLLM(model="scripted", script=["Reviewed.\nSources: [concept:rule]"])
    agent = create_agent(client(seen), ROLE, llm=llm)
    assert seen[0][:2] == ("GET", f"/v1/roles/{ROLE}") and len(seen) == 1
    output, _ = run_crew(agent)
    block = client(expected).prefetch(ROLE, TASK)
    assert output.raw == "Reviewed.\nSources: [concept:rule]"
    assert len(llm.seen) == 1 and block in user_text(llm.seen[0])
    assert "Preserve this evidence." in block and "(cite as concept:doc.vinc.spec.fixture)" in block
    # 생성 때 역할 한 번, 런마다 역할·문서·미러·brief·search 다섯 번. 쓰기는 없다.
    assert sorted(seen[1:], key=repr) == sorted(expected, key=repr) and len(seen) == 6
    assert all(method == "GET" or path in ("/v1/brief", "/v1/search") for method, path, _ in seen)


def test_a_retry_after_a_model_error_stays_in_the_run_without_reading_again():
    seen = []
    llm = ScriptedLLM(model="scripted", script=[RuntimeError("transient"), "Reviewed.\nSources: none"])
    output, _ = run_crew(create_agent(client(seen), ROLE, llm=llm))
    assert output.raw == "Reviewed.\nSources: none" and len(llm.seen) == 2
    assert len(seen) == 6
    assert user_text(llm.seen[1]).count("Required reading (full canonical document)") == 1


def test_the_budget_error_stops_the_run_before_the_model_is_called():
    llm = ScriptedLLM(model="scripted")
    agent = create_agent(client([]), ROLE, llm=llm, prefetch_max_chars=100)
    with pytest.raises(VincInvalidArgument, match="prefetch_budget_exceeded") as exc:
        run_crew(agent)
    assert exc.value.details["max_chars"] == 100 and not llm.seen


def test_a_pinned_role_that_changed_stops_before_the_model():
    pin, current = "c" * 64, "d" * 64
    with pytest.raises(VincRoleChanged):
        create_agent(client([], role_hash=current), ROLE, prefetch_pin=pin)
    holder = {"hash": pin}
    seen = []

    def handle(request):
        return hub(seen, role_hash=holder["hash"])(request)
    llm = ScriptedLLM(model="scripted")
    agent = create_agent(Vinc(KEY, transport=httpx.MockTransport(handle)), ROLE, llm=llm,
                         prefetch_pin=pin)
    holder["hash"] = current
    with pytest.raises(VincRoleChanged) as exc:
        run_crew(agent)
    assert exc.value.details == {"id": ROLE, "rev": 2, "hash": current} and not llm.seen


def test_disallowed_tools_never_reach_the_model_or_the_executor(caplog):
    llm = ScriptedLLM(model="scripted", script=[
        [{"id": "c1", "function": {"name": "bash", "arguments": json.dumps({"command": "ls"})}}],
        "Done.\nSources: none"])
    agent = create_agent(client([]), ROLE, llm=llm, tools=[Bash(), Lookup()])
    with caplog.at_level(logging.WARNING, logger="vinc_client"):
        run_crew(agent)
    warnings = [r.getMessage() for r in caplog.records if r.name == "vinc_client"]
    assert [call["tools"] for call in llm.seen] == [["lookup"], ["lookup"]]
    # CrewAI 는 실행기에 없는 도구 호출에 자기 문장으로 답한다. Bash 는 돌지 않았다(돌면 AssertionError).
    assert tool_results(llm.seen[1]) == ["Tool not found"]
    assert len(warnings) == 1 and warnings[0].endswith(
        "Edit. Names match exactly; check the role contract against the agent's tool names.")


RAN: list[str] = []
CAPABILITIES = {"merge_pr": "github.pr.merge", "run_shell": "shell.exec", "lookup": "vinc.read"}


class MergePr(BaseTool):
    name: str = "merge_pr"
    description: str = "Merge a pull request."

    def _run(self, number: int) -> str:
        RAN.append(f"merge {number}")
        return "merged"


class RunShell(BaseTool):
    name: str = "run_shell"
    description: str = "Run a shell command."

    def _run(self, command: str) -> str:
        RAN.append(command)
        return "ran"


class Recorded(BaseTool):
    name: str = "lookup"
    description: str = "A local lookup."

    def _run(self, query: str) -> str:
        RAN.append(query)
        return "local answer"


def policy_run(calls_made, **props):
    RAN.clear()
    llm = ScriptedLLM(model="scripted", script=[
        [{"id": f"c{i}", "function": {"name": name, "arguments": json.dumps(args)}}]
        for i, (name, args) in enumerate(calls_made)])
    agent = create_agent(client([], props={**PROPS, "disallowed_tools": [], **props}), ROLE, llm=llm,
                         tools=[MergePr(), RunShell(), Recorded()],
                         capability_map=CAPABILITIES if "capabilities" in props else None)
    run_crew(agent)
    return llm, [tool_results(call)[-1] for call in llm.seen[1:]]


def test_denied_capabilities_are_removed_and_approval_ones_ask_for_a_person():
    llm, results = policy_run([("merge_pr", {"number": 7}), ("run_shell", {"command": "ls"})],
                              capabilities={"deny": ["shell.*"], "approve": ["github.pr.merge"]})
    assert all(call["tools"] == ["merge_pr", "lookup"] for call in llm.seen)
    assert results == [f"The role {ROLE} needs a person's approval before the merge_pr tool runs; "
                       "ask for it instead of calling the tool.", "Tool not found"]
    assert RAN == []


def test_the_budget_caps_all_tool_calls_of_the_run():
    _, results = policy_run([("lookup", {"query": "a"}), ("lookup", {"query": "b"})],
                            budget={"tool_calls": 1})
    assert results == ["local answer",
                       f"The role {ROLE} allows 1 tool calls per run; answer from what you already have."]
    assert RAN == ["a"]


def test_capabilities_without_a_map_warn_and_change_nothing(caplog):
    RAN.clear()
    llm = ScriptedLLM(model="scripted", script=[
        [{"id": "c0", "function": {"name": "run_shell", "arguments": json.dumps({"command": "ls"})}}]])
    agent = create_agent(client([], props={**PROPS, "disallowed_tools": [],
                                           "capabilities": {"deny": ["shell.*"]}}),
                         ROLE, llm=llm, tools=[RunShell()])
    with caplog.at_level(logging.WARNING, logger="vinc_client"):
        run_crew(agent)
    assert tool_results(llm.seen[1]) == ["ran"] and RAN == ["ls"]
    assert [r.getMessage() for r in caplog.records if r.name == "vinc_client"] == [
        f"Role {ROLE} sets capabilities, but none of this agent's tools is in the default "
        "capability maps, so they are not enforced; pass a capability_map, or name the host's "
        "servers in tool_servers (gh=github)."]


def test_a_malformed_policy_stops_the_run_before_the_model():
    llm = ScriptedLLM(model="scripted")
    agent = create_agent(client([], props={**PROPS, "output_kind": "essay"}), ROLE, llm=llm)
    with pytest.raises(VincInvalidArgument) as caught:
        run_crew(agent)
    assert caught.value.code == "invalid_role_contract" and not llm.seen


def test_exposed_read_tools_share_a_fresh_cap_per_run():
    # CrewAI 는 한 턴의 호출 여덟 개를 함께 돌린다. 어느 넷이 상한에 걸리는지는 정해지지 않지만 수는 넷이다.
    llm = ScriptedLLM(model="scripted", script=[
        calls("vinc_brief", *[f"q{i}" for i in range(8)]), "First.\nSources: none",
        calls("vinc_brief", "again"), "Second.\nSources: none"])
    agent = create_agent(client([]), ROLE, llm=llm, expose_tools=True)
    assert llm.seen == [] and run_crew(agent)[0].raw == "First.\nSources: none"
    first = tool_results(llm.seen[1])
    assert len(first) == 8 and first.count(cap_message(4)) == 4
    assert sum("concept:rule" in r for r in first) == 4
    assert llm.seen[0]["tools"] == ["vinc_brief", "vinc_search"]
    run_crew(agent)
    assert "concept:rule" in tool_results(llm.seen[3])[0]


def test_read_tools_use_the_shared_schemas_and_sentences():
    seen = []
    brief, search = create_vinc_tools(client(seen))
    assert (brief.name, search.name) == ("vinc_brief", "vinc_search")
    assert (brief.args_schema, search.args_schema) == (BriefArgs, SearchArgs)
    assert "DATA" in brief.run(query="Review") and "chunk:rule" in search.run(query="Review")
    with pytest.raises(Exception, match="limit"):
        search.run(query="Review", limit=21)
    assert [row[2]["limit"] for row in seen if row[1] == "/v1/search"] == [5]

    def missing(code):
        return Vinc(KEY, transport=httpx.MockTransport(lambda r: httpx.Response(
            404, json={"error": {"code": code, "message": "missing"}})))
    assert create_vinc_tools(missing("not_found"))[0].run(query="decision:x") == (
        "Vinc found nothing for that (not_found); try a different query.")
    with pytest.raises(VincNotFound, match="space_not_found"):
        create_vinc_tools(missing("space_not_found"))[0].run(query="decision:x")
    down = Vinc(KEY, transport=httpx.MockTransport(lambda r: httpx.Response(
        503, json={"error": {"code": "unavailable", "message": "down"}})))
    assert create_vinc_tools(down)[1].run(query="x") == (
        "Vinc is not available right now (unavailable); answer without it.")


@pytest.mark.parametrize("answer,verdict", [
    ("Looks right.\nSources: none", "pass"),
    ("Please approve the merge before release.\nSources: none", "interrupt"),
    ("I merged the branch.\nSources: none", "block"),
    (RoleOutput(text="Ready.", person_only_requests=[{"action": "merge", "reason": "r"}]).model_dump_json(),
     "interrupt"),
])
@pytest.mark.parametrize("on_interrupt", ["deliver", "stop"])
def test_the_guardrail_releases_only_a_passing_output_and_never_retries(answer, verdict, on_interrupt):
    llm = ScriptedLLM(model="scripted", script=[answer, answer])
    agent = create_agent(client([]), ROLE, llm=llm)
    guardrail = create_output_guardrail(OutputGate(PROPS), on_interrupt=on_interrupt)
    structured = answer.lstrip().startswith("{")
    if verdict == "pass":
        assert run_crew(agent, guardrail=guardrail)[0].raw == answer
    elif verdict == "interrupt" and on_interrupt == "deliver" and not structured:
        # 글로 된 답은 나가고 기다리는 결정이 Sources 줄 위에 붙는다.
        raw = run_crew(agent, guardrail=guardrail)[0].raw
        assert raw.startswith(answer.split("\nSources:")[0]) and PENDING_HEADER in raw
        assert raw.rstrip().endswith("Sources: none")
    else:
        with pytest.raises(OutputGateStopped) as exc:
            run_crew(agent, guardrail=guardrail)
        assert exc.value.result.verdict == verdict and answer not in str(exc.value)
    assert len(llm.seen) == 1


NEEDS_MERGE = RoleOutput(text="Ready.", person_only_requests=[
    {"action": "merge", "reason": "release"}]).model_dump_json()


def approve(answer: bool):
    asked = []

    def decide(request):
        asked.append(request)
        return ApprovalDecision(request_id=request.request_id, approved=answer)
    return decide, asked


@pytest.mark.parametrize("async_mode", [False, True])
def test_person_only_approval_releases_the_saved_draft_and_calls_nothing_else(async_mode):
    seen = []
    llm = ScriptedLLM(model="scripted", script=[NEEDS_MERGE])
    agent = create_agent(client(seen), ROLE, llm=llm,
                         async_client=AsyncVinc(KEY, transport=httpx.MockTransport(hub(seen))))
    task = Task(description=TASK, expected_output="A review", agent=agent, human_input=True)
    decide, asked = approve(True)
    step = VincPersonOnlyInput(OutputGate(PROPS), role_id=ROLE, run_id="run-1", decide=decide, tasks=[task])
    crew = Crew(agents=[agent], tasks=[task])
    with step.installed():
        output = asyncio.run(crew.akickoff()) if async_mode else crew.kickoff()
    assert output.raw == NEEDS_MERGE and len(llm.seen) == 1
    assert [r.actions for r in asked] == [["merge"]]
    assert step.result(task).status == "approved"
    assert step.result(task).output == RoleOutput.model_validate_json(NEEDS_MERGE)
    assert not any(path == "/v1/fragments" for _, path, _ in seen)


@pytest.mark.parametrize("answer,error", [
    (NEEDS_MERGE, VincApprovalRejected),
    (RoleOutput(text="I merged it.").model_dump_json(), OutputGateStopped),
    ("not role output", OutputGateStopped),
])
def test_a_rejected_or_blocked_draft_stops_the_crew_after_one_model_call(answer, error):
    llm = ScriptedLLM(model="scripted", script=[answer] * 3)
    agent = create_agent(client([]), ROLE, llm=llm)
    task = Task(description=TASK, expected_output="A review", agent=agent, human_input=True)
    decide, asked = approve(False)
    step = VincPersonOnlyInput(OutputGate(PROPS), role_id=ROLE, run_id="run-1", decide=decide, tasks=[task])
    with step.installed(), pytest.raises(error) as exc:
        Crew(agents=[agent], tasks=[task]).kickoff()
    # CrewAI 가 실행 오류로 보고 에이전트를 다시 부르지만, 같은 런이라 모델을 다시 부르지 않는다.
    assert len(llm.seen) == 1 and len(asked) == (1 if error is VincApprovalRejected else 0)
    assert step.result(task) is None
    if error is VincApprovalRejected:
        assert exc.value.result.status == "rejected" and exc.value.result.output is None


def test_a_plain_crewai_agent_retry_gets_the_same_stop_without_asking_again():
    from crewai import Agent
    llm = ScriptedLLM(model="scripted", script=[NEEDS_MERGE] * 4)
    agent = Agent(role="Reviewer", goal="Review.", backstory="Reviews.", llm=llm)
    task = Task(description=TASK, expected_output="A review", agent=agent, human_input=True)
    decide, asked = approve(False)
    step = VincPersonOnlyInput(OutputGate(PROPS), role_id=ROLE, run_id="run-1", decide=decide, tasks=[task])
    with step.installed(), pytest.raises(VincApprovalRejected):
        Crew(agents=[agent], tasks=[task]).kickoff()
    assert len(asked) == 1 and len(llm.seen) == 1 + agent.max_retry_limit


def test_unbound_tasks_keep_the_previous_human_input_provider():
    class Previous:
        def __init__(self):
            self.feedback = []

        def setup_messages(self, context):
            return False

        def post_setup_messages(self, context):
            return None

        def handle_feedback(self, answer, context):
            self.feedback.append(context.task.description)
            return answer

        async def handle_feedback_async(self, answer, context):
            return self.handle_feedback(answer, context)

    previous = Previous()
    token = set_provider(previous)
    try:
        llm = ScriptedLLM(model="scripted", script=[RoleOutput(text="Fine.").model_dump_json(), "Plain."])
        agent = create_agent(client([]), ROLE, llm=llm)
        bound = Task(description=TASK, expected_output="x", agent=agent, human_input=True)
        other = Task(description="Summarise.", expected_output="x", agent=agent, human_input=True)
        decide, asked = approve(True)
        step = VincPersonOnlyInput(OutputGate(PROPS), role_id=ROLE, run_id="run-1", decide=decide,
                                   tasks=[bound])
        with step.installed():
            Crew(agents=[agent], tasks=[bound, other]).kickoff()
    finally:
        reset_provider(token)
    assert previous.feedback == ["Summarise."] and asked == []
    assert step.result(bound).status == "pass" and step.result(other) is None


def test_the_approval_step_refuses_a_task_without_human_input():
    agent = create_agent(client([]), ROLE, llm=ScriptedLLM(model="scripted"))
    task = Task(description=TASK, expected_output="x", agent=agent)
    step = VincPersonOnlyInput(OutputGate(PROPS), role_id=ROLE, run_id="run-1",
                               decide=lambda r: None, tasks=[task])
    with pytest.raises(ValueError, match="human_input"):
        with step.installed():
            pass
    with pytest.raises(ValueError, match="scope"):
        VincPersonOnlyInput(OutputGate(PROPS), role_id="concept:reviewer", run_id="run-1",
                            decide=lambda r: None, tasks=[task])


def test_a_handoff_package_joins_every_run_and_nothing_is_written():
    seen = []
    llm = ScriptedLLM(model="scripted", script=["Done.\nSources: none"])
    agent = create_agent(client(seen), ROLE, llm=llm, package_id="record:package",
                         package_space="personal")
    run_crew(agent)
    text = user_text(llm.seen[0])
    assert "Keep the exact recommendation." in text and "Required reading" in text
    assert not any(path == "/v1/fragments" for _, path, _ in seen)
    with pytest.raises(ValueError, match="together"):
        create_agent(client([]), ROLE, package_id="record:package")


def test_copy_keeps_the_role_and_kickoff_outside_a_crew_is_refused():
    llm = ScriptedLLM(model="scripted", script=["Copied.\nSources: none"])
    agent = create_agent(client([]), ROLE, llm=llm)
    clone = agent.copy()
    assert clone.vinc_role_id == ROLE and type(clone) is type(agent)
    assert run_crew(clone)[0].raw == "Copied.\nSources: none"
    with pytest.raises(NotImplementedError, match="Crew"):
        agent.kickoff("Review.")
    with pytest.raises(TypeError, match="backstory"):
        create_agent(client([]), ROLE, backstory="mine")


def test_record_run_is_the_only_write_and_happens_when_called():
    seen = []
    llm = ScriptedLLM(model="scripted", script=["Done.\nSources: [concept:rule]"])
    output, _ = run_crew(create_agent(client(seen), ROLE, llm=llm))
    assert not any(method == "POST" and path == "/v1/fragments" for method, path, _ in seen)
    writes = []
    record_run(client(writes, key=WRITE_KEY), "crew-run-1", role=ROLE, outcome="success",
               steps=len(output.tasks_output), cited_ids=["concept:rule"], host="crewai",
               date="2026-10-25")
    posts = [body for method, path, body in writes if method == "POST"]
    assert len(posts) == 1
    props = posts[0]["nodes"][0]["props"]
    assert props == {"run_id": "crew-run-1", "role": ROLE, "outcome": "success", "host": "crewai",
                     "steps": 1, "cited_ids": ["concept:rule"]}
    assert posts[0]["edges"] == [{"from": posts[0]["nodes"][0]["id"], "to": ROLE, "type": "about"}]


# ── build_agent: a role address to a running agent in one call ──────────────────────────

def _fragment_posts(seen):
    return [body for method, path, body in seen if method == "POST" and path == "/v1/fragments"]


def test_build_agent_runs_checks_and_records_in_one_call():
    seen = []
    llm = ScriptedLLM(model="scripted", script=["Reviewed the policy; two findings.\nSources: concept:rule"])
    built = build_agent("reviewer-judgement", llm, client=client(seen, key=WRITE_KEY))
    assert isinstance(built.agent, VincAgent) and built.plan.role_id == ROLE
    assert built.plan.person_only[0] == "merge"
    assert [p for m, p, _ in seen].count(f"/v1/roles/{ROLE}") == 1
    result = built.run(TASK, run_id="run-1")
    assert (result.status, result.outcome) == ("done", "success")
    assert result.output.startswith("Reviewed the policy") and result.cited_ids == ("concept:rule",)
    assert result.record is not None and result.record_error is None
    props = _fragment_posts(seen)[0]["nodes"][0]["props"]
    assert (props["run_id"], props["role"], props["outcome"], props["host"]) == (
        "run-1", ROLE, "success", "crewai")
    assert props["gate_results"]["verdict"] == "pass"
    assert "Preserve this evidence." in user_text(llm.seen[0])
    assert {"vinc_brief", "vinc_search"} <= set(llm.seen[0]["tools"])


def test_build_agent_blocks_a_failing_answer_and_records_it_blocked():
    seen = []
    llm = ScriptedLLM(model="scripted", script=["I merged the pull request and deployed it."])
    result = build_agent("reviewer-judgement", llm, client=client(seen, key=WRITE_KEY)).run(
        TASK, run_id="run-2")
    assert (result.status, result.output) == ("blocked", None)
    assert result.gate is not None and result.gate.verdict == "block" and result.gate.failed_checks
    assert len(llm.seen) == 1
    assert _fragment_posts(seen)[0]["nodes"][0]["props"]["outcome"] == "blocked"


def test_build_agent_stops_the_next_run_when_the_role_changed_after_it_was_read():
    seen, flag = [], {"changed": False}
    inner = hub(seen)

    def handle(request):
        if (flag["changed"] and request.url.path.startswith("/v1/roles/")
                and request.url.params.get("expected_hash")):
            return httpx.Response(409, json={"error": {"code": "role_changed", "message": "changed",
                                  "details": {"id": ROLE, "rev": 2, "hash": "d" * 64}}})
        return inner(request)

    llm = ScriptedLLM(model="scripted")
    built = build_agent("reviewer-judgement", llm,
                        client=Vinc(WRITE_KEY, transport=httpx.MockTransport(handle)))
    flag["changed"] = True
    with pytest.raises(VincRoleChanged):
        built.run(TASK, run_id="run-3")
    assert not llm.seen
    assert _fragment_posts(seen)[0]["nodes"][0]["props"]["outcome"] == "error"


def test_build_agent_record_false_writes_nothing_and_a_read_only_key_keeps_the_answer():
    quiet = []
    result = build_agent("reviewer-judgement", ScriptedLLM(model="scripted"),
                         client=client(quiet, key=WRITE_KEY), record=False).run(TASK)
    assert result.status == "done" and result.record is None and not _fragment_posts(quiet)
    readonly = []
    result = build_agent("reviewer-judgement", ScriptedLLM(model="scripted"),
                         client=client(readonly)).run(TASK)
    assert result.output and result.record is None and result.record_error
    assert not _fragment_posts(readonly)


def test_build_agent_refuses_a_bad_task_and_the_async_form_builds_runs_and_records():
    built = build_agent("reviewer-judgement", ScriptedLLM(model="scripted"), client=client([]))
    for bad in (42, "  ", None):
        with pytest.raises(TypeError, match="task"):
            built.run(bad)
    seen = []

    async def go():
        async with AsyncVinc(WRITE_KEY, transport=httpx.MockTransport(hub(seen))) as aclient:
            agent = await abuild_agent("reviewer-judgement", ScriptedLLM(
                model="scripted", script=["Reviewed.\nSources: none"]), async_client=aclient)
            return await agent.arun(TASK, run_id="run-5")

    result = asyncio.run(go())
    assert (result.status, result.output) == ("done", "Reviewed.\nSources: none")
    assert _fragment_posts(seen)[0]["nodes"][0]["props"]["run_id"] == "run-5"


def test_the_run_kit_runs_a_crewai_agent_and_records_the_model_family():
    from vinc.kit import run_role
    seen = []
    llm = ScriptedLLM(model="scripted", script=["Reviewed.\nSources: concept:rule"])
    done = run_role("reviewer-judgement", TASK, model=llm, adapter="crewai",
                    client=client(seen, key=WRITE_KEY), run_id="kit-1")
    assert (done.run.status, done.run.output, done.adapter) == ("done", "Reviewed.\nSources: concept:rule", "crewai")
    assert done.trace is None and done.steps is None and len(llm.seen) == 1
    props = _fragment_posts(seen)[0]["nodes"][0]["props"]
    assert (props["run_id"], props["host"], props["model_family"]) == ("kit-1", "crewai", "scriptedllm")
    assert "trace_ref" not in props

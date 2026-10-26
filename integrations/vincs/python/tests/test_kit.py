"""The run kit: run a role with the person's own model, keep the raw trace, write the record."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("langgraph")

from langchain_core.language_models.chat_models import BaseChatModel  # noqa: E402
from langchain_core.messages import AIMessage  # noqa: E402
from langchain_core.outputs import ChatGeneration, ChatResult  # noqa: E402
from pydantic import Field  # noqa: E402

from vinc import Vinc  # noqa: E402
from vinc.kit import (ADAPTERS, BudgetExceeded, KitError, KitRun, Meter, Trace, model_family,  # noqa: E402
                      resolve_model, run_role)
from vinc.kit import cli as kit_cli  # noqa: E402
from vinc.sdk.agent_plan import AgentRun  # noqa: E402
from vinc.sdk.output_gate import GateResult  # noqa: E402

ROLE = "concept:agent-dev-core"
SK = "vinc_sk_" + "b" * 32
RO = "vinc_ro_" + "a" * 32
# 설치된 휠에서 도는 시험은 저장소의 fixtures 를 볼 수 없다: 역할 계약을 여기에 적는다.
PROFILE = {
    "job_title": "Core developer", "never": ["merge"],
    "person_only": [{"action": "merge", "reason": "merge"}], "disallowed_tools": [],
    "gates": [{"id": "person_only", "check": {"type": "person_only"}, "on_fail": "interrupt"},
              {"id": "side_effect_evidence", "check": {"type": "side_effect_evidence"}, "on_fail": "block"},
              {"id": "handoff_manifest", "check": {"type": "handoff_manifest"}, "on_fail": "block"}],
    "never_checks": [{"type": "forbidden_actions", "actions": ["merge"]}],
}
PLAIN = "The change reads correctly; two findings follow.\nSources: decision:fixture"
ASK = AIMessage(content="", tool_calls=[{"name": "vinc_brief", "args": {"query": "the rule"},
                                         "id": "c1", "type": "tool_call"}])


class ScriptedModel(BaseChatModel):
    """Answers from ``script`` in order, then with ``reply``; keeps what each call was given."""

    script: list = Field(default_factory=list)
    reply: str = PLAIN
    calls: list = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedModel":
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.calls.append(list(messages))
        message = self.script.pop(0) if self.script else AIMessage(content=self.reply)
        return ChatResult(generations=[ChatGeneration(message=message)])


class Hub:
    def __init__(self, **props: Any) -> None:
        self.props = {**PROFILE, "mission": "m", "tier": "execute", "rung": "strong",
                      "never": ["merge"], "reads": [], **props}
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.startswith("/v1/roles/"):
            return httpx.Response(200, json={"found": True, "id": ROLE, "title": "Core developer",
                                             "rev": 2, "hash": "a" * 64, "props": self.props,
                                             "works_with": {"hands_to": [], "receives_from": []}})
        if path.startswith("/v1/nodes/"):
            return httpx.Response(200, json={"executed": True, "found": True,
                                             "node": {"id": path.rsplit("/", 1)[-1]}})
        if path == "/v1/fragments":
            return httpx.Response(200, json={"merged": {"nodes": {"created": 1}}, "stored": [],
                                             "warnings": []})
        if path.endswith("/brief"):
            return httpx.Response(200, json={"status": "resolved", "node": {
                "id": "decision:fixture", "cite_id": "decision:fixture"}, "one_liner": "Keep it."})
        return httpx.Response(200, json={"node_hits": []})

    def posts(self) -> list[dict]:
        return [json.loads(r.content) for r in self.requests
                if r.method == "POST" and r.url.path == "/v1/fragments"]


def client(hub: Hub, key: str = SK) -> Vinc:
    return Vinc(key, transport=httpx.MockTransport(hub))


def test_a_role_runs_end_to_end_and_the_record_carries_the_trace_hash(tmp_path):
    hub, model = Hub(), ScriptedModel()
    done = run_role("dev-core", "Review the change.", model=model, client=client(hub),
                    run_id="run-1", trace_dir=tmp_path)
    assert isinstance(done, KitRun) and isinstance(done.run, AgentRun)
    assert (done.run.status, done.run.output, done.adapter) == ("done", PLAIN, "langgraph")
    assert done.steps == 1 and done.tool_calls == 0
    assert done.trace is not None and done.trace.parent == tmp_path
    data = done.trace.read_bytes()
    assert done.trace.name == hashlib.sha256(data).hexdigest() + ".jsonl"
    kinds = [json.loads(line)["t"] for line in data.splitlines()]
    assert kinds == ["model_in", "model_out"]
    assert "Review the change." in data.decode("utf-8")
    props = hub.posts()[0]["nodes"][0]["props"]
    assert (props["run_id"], props["role"], props["outcome"], props["host"]) == (
        "run-1", ROLE, "success", "langgraph")
    assert props["model_family"] == "scriptedmodel" and props["steps"] == 1 and props["tool_calls"] == 0
    assert props["trace_ref"]["sha256"] == hashlib.sha256(data).hexdigest()
    assert props["trace_ref"]["bytes"] == len(data) and props["trace_ref"]["name"] == done.trace.name
    assert str(tmp_path) not in json.dumps(hub.posts())


def test_the_same_input_leaves_the_same_trace_file(tmp_path):
    first = run_role("dev-core", "Same task.", model=ScriptedModel(), client=client(Hub()),
                     record=False, trace_dir=tmp_path / "a")
    second = run_role("dev-core", "Same task.", model=ScriptedModel(), client=client(Hub()),
                      record=False, trace_dir=tmp_path / "b")
    assert first.trace.name == second.trace.name
    assert first.trace.read_bytes() == second.trace.read_bytes()


def test_tool_calls_are_traced_and_counted(tmp_path):
    hub = Hub()
    done = run_role("dev-core", "Check the rule.", model=ScriptedModel(script=[ASK]),
                    client=client(hub), trace_dir=tmp_path)
    assert (done.steps, done.tool_calls, done.run.status) == (2, 1, "done")
    events = [json.loads(line) for line in done.trace.read_bytes().splitlines()]
    assert [e["t"] for e in events] == ["model_in", "model_out", "tool_call", "tool_result",
                                        "model_in", "model_out"]
    assert events[2]["name"] == "vinc_brief" and "the rule" in str(events[2]["input"])
    props = hub.posts()[0]["nodes"][0]["props"]
    assert props["steps"] == 2 and props["tool_calls"] == 1


def test_a_role_budget_stops_the_run_before_the_model_is_called_again(tmp_path):
    hub, model = Hub(budget={"model_calls": 1}), ScriptedModel(script=[ASK])
    done = run_role("dev-core", "Check the rule.", model=model, client=client(hub), trace_dir=tmp_path)
    assert (done.run.status, done.run.output) == ("blocked", None)
    assert done.run.gate.failed_checks == ("budget_model_calls",)
    assert len(model.calls) == 1
    assert hub.posts()[0]["nodes"][0]["props"]["outcome"] == "blocked"


def test_a_failing_gate_blocks_and_nothing_is_written_with_no_record(tmp_path):
    hub = Hub()
    done = run_role("dev-core", "Ship it.", model=ScriptedModel(reply="I merged the pull request and deployed it."),
                    client=client(hub), record=False, trace_dir=tmp_path)
    assert done.run.status == "blocked" and done.run.gate.failed_checks
    assert not hub.posts() and done.run.record is None


def test_no_trace_dir_writes_no_file_and_a_read_only_key_keeps_the_answer(tmp_path):
    hub = Hub()
    done = run_role("dev-core", "Review.", model=ScriptedModel(), client=client(hub, RO),
                    trace_dir=None)
    assert done.trace is None and done.run.output == PLAIN
    assert done.run.record is None and done.run.record_error
    assert "trace_ref" not in json.dumps(hub.posts())


def test_the_meter_counts_and_stops_at_each_guarded_key():
    meter = Meter({"model_calls": 2, "tokens": 100, "wall_seconds": 5})
    meter.before_model()
    meter.after_model(40, 30)
    meter.before_model()
    with pytest.raises(BudgetExceeded) as over:
        meter.before_model()
    assert over.value.key == "model_calls"
    tokens = Meter({"tokens": 100})
    tokens.after_model(80, 30)
    with pytest.raises(BudgetExceeded) as spent:
        tokens.before_model()
    assert spent.value.key == "tokens"
    now = [0.0]
    wall = Meter({"wall_seconds": 5}, clock=lambda: now[0])
    wall.before_tool()
    now[0] = 6.0
    with pytest.raises(BudgetExceeded) as late:
        wall.before_model()
    assert late.value.key == "wall_seconds"
    # tool_calls belongs to the role's tool policy, and cost_usd is not guarded at all.
    free = Meter({"tool_calls": 0, "cost_usd": 0})
    free.before_tool()
    free.before_model()


def test_a_trace_is_the_same_bytes_for_the_same_events(tmp_path):
    a, b = Trace(), Trace()
    for trace in (a, b):
        trace.add({"t": "model_in", "messages": [[{"type": "human", "content": "hi"}]]})
    assert a.to_bytes() == b.to_bytes() and a.sha256() == b.sha256()
    assert a.write(tmp_path).read_bytes() == a.to_bytes()


def test_models_are_named_by_family_and_strings_are_checked():
    assert model_family("anthropic:claude-sonnet-5-5") == "anthropic"
    assert model_family("openai/gpt-4o") == "openai"
    assert model_family(ScriptedModel()) == "scriptedmodel"
    scripted = ScriptedModel()
    assert resolve_model("langgraph", scripted) is scripted
    assert resolve_model("crewai", "anthropic:claude-sonnet-5-5") == "anthropic/claude-sonnet-5-5"
    for bad in ("claude", ":x", "x:"):
        with pytest.raises(KitError, match="provider:name"):
            resolve_model("langgraph", bad)
    with pytest.raises(KitError, match="object"):
        resolve_model("agent-framework", "openai:gpt-4o")
    with pytest.raises(KitError, match="adapter"):
        resolve_model("autogen", "openai:gpt-4o")
    assert ADAPTERS == ("langgraph", "agent-framework", "crewai")
    with pytest.raises(KitError, match="task"):
        run_role("dev-core", "  ", model=scripted)


def fake_run(status: str, **parts: Any):
    gate = GateResult("block" if status == "blocked" else "interrupt" if status == "waiting" else "pass",
                      tuple(parts.get("failed", ())), tuple(parts.get("actions", ())))
    outcome = {"done": "success", "blocked": "blocked", "waiting": "waiting"}[status]
    run = AgentRun("run-9", ROLE, outcome, parts.get("output"), gate, record={"merged": 1})

    def runner(address, task, **kw):
        runner.seen = (address, task, kw)
        return KitRun(run, kw["adapter"], "anthropic", Path("vinc-traces/x.jsonl"), 2, 1)
    return runner


@pytest.mark.parametrize("status, code", [("done", 0), ("blocked", 3), ("waiting", 4)])
def test_the_command_prints_the_outcome_and_exits_with_its_code(monkeypatch, capsys, status, code):
    runner = fake_run(status, output="All good." if status == "done" else None,
                      failed=["never_x"], actions=["merge"])
    monkeypatch.setattr(kit_cli.kit_run, "run_role", runner)
    assert kit_cli.main(["run", "dev-core", "--task", "Review it.", "--model", "anthropic:x"]) == code
    out = capsys.readouterr().out
    assert "run: run-9" in out and f"status: {status}" in out
    assert "vinc run show run-9" in out and "trace: vinc-traces" in out
    assert runner.seen[0] == "dev-core" and runner.seen[1] == "Review it."
    assert runner.seen[2]["model"] == "anthropic:x" and runner.seen[2]["adapter"] == "langgraph"
    assert runner.seen[2]["record"] is True and runner.seen[2]["trace_dir"] == "vinc-traces"
    if status == "blocked":
        assert "blocked by: never_x" in out
    if status == "waiting":
        assert "a person decides first: merge" in out


def test_the_command_flags_reach_the_runner_and_json_prints_the_summary(monkeypatch, capsys, tmp_path):
    task = tmp_path / "task.md"
    task.write_text("Read me.", encoding="utf-8")
    runner = fake_run("done", output="Fine.")
    monkeypatch.setattr(kit_cli.kit_run, "run_role", runner)
    code = kit_cli.main(["run", "dev-core", "--task-file", str(task), "--model", "anthropic:x",
                         "--adapter", "crewai", "--run-id", "mine", "--space", "team-1",
                         "--no-record", "--no-trace", "--json"])
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "done" and summary["output"] == "Fine." and summary["steps"] == 2
    kw = runner.seen[2]
    assert runner.seen[1] == "Read me." and kw["adapter"] == "crewai" and kw["run_id"] == "mine"
    assert kw["space"] == "team-1" and kw["record"] is False and kw["trace_dir"] is None


def test_a_run_that_cannot_start_is_exit_two_with_one_sentence(monkeypatch, capsys):
    def refuse(*args, **kwargs):
        raise KitError("write the model as provider:name")
    monkeypatch.setattr(kit_cli.kit_run, "run_role", refuse)
    assert kit_cli.main(["run", "dev-core", "--task", "x", "--model", "bad"]) == 2
    assert "provider:name" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        kit_cli.main(["run", "dev-core", "--model", "a:b"])


def test_the_vinc_command_hands_agent_to_the_kit_and_the_rest_to_the_product_cli(monkeypatch):
    from vinc import cli as launcher
    seen = {}
    monkeypatch.setattr(kit_cli, "main", lambda argv: seen.setdefault("agent", argv) and 7)
    assert launcher.main(["agent", "run", "dev-core", "--task", "x", "--model", "a:b"]) == 7
    assert seen["agent"] == ["run", "dev-core", "--task", "x", "--model", "a:b"]
    called = SimpleNamespace(command=None)
    monkeypatch.setattr(launcher.subprocess, "call", lambda command: called.__setattr__("command", command) or 0)
    assert launcher.main(["run", "ls"]) == 0
    assert called.command[-2:] == ["run", "ls"]


def test_agent_framework_runs_through_the_kit_and_records_without_a_trace():
    pytest.importorskip("agent_framework")
    from agent_framework import BaseChatClient, ChatResponse, Message

    class ScriptedChat(BaseChatClient):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        async def _inner_get_response(self, *, messages, stream, options, **kwargs):  # type: ignore[override]
            self.calls += 1
            return ChatResponse(messages=Message("assistant", [PLAIN]))

    hub, chat = Hub(), ScriptedChat()
    async_client = None
    from vinc import AsyncVinc
    async_client = AsyncVinc(SK, transport=httpx.MockTransport(hub))
    done = run_role("dev-core", "Review the change.", model=chat, adapter="agent-framework",
                    async_client=async_client, run_id="af-1")
    assert (done.run.status, done.run.output, done.adapter) == ("done", PLAIN, "agent-framework")
    assert done.trace is None and done.steps is None and chat.calls == 1
    props = hub.posts()[0]["nodes"][0]["props"]
    assert (props["run_id"], props["host"], props["model_family"]) == (
        "af-1", "agent-framework", "scriptedchat")
    assert "trace_ref" not in props and "steps" not in props
    with pytest.raises(KitError, match="object"):
        run_role("dev-core", "x", model="openai:gpt-4o", adapter="agent-framework")

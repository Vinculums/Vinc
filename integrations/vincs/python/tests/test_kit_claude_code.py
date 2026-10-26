"""``claude-code:<model>``: the subscription as the model, through headless Claude Code."""
from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

pytest.importorskip("langgraph")

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage  # noqa: E402

from test_kit import Hub, PLAIN, client  # noqa: E402
from vinc.kit import KitError, resolve_model, run_role, run_suite  # noqa: E402
from vinc.kit import claude_code  # noqa: E402
from vinc.kit.claude_code import ClaudeCodeChat, SubscriptionPaused, parse_stream, render  # noqa: E402

KEEP = {"id": "keep-one", "family": "keep-decision", "prompt": "May we ship on Friday?",
        "checks": [{"type": "not_regex", "pattern": "ship it"}]}


def stream(text: str = PLAIN, *, five: float = 0.3, seven: float = 0.5, cost: float = 0.01,
           source: str = "none", subtype: str = "success") -> str:
    events = [
        {"type": "system", "subtype": "init", "apiKeySource": source, "model": "claude-haiku-5-5"},
        {"type": "rate_limit_event", "rate_limit_info": {"unifiedWindows": {
            "five_hour": {"utilization": five, "resetsAt": 1793014800, "status": "allowed"},
            "seven_day": {"utilization": seven, "resetsAt": 1793174400, "status": "allowed"}}}},
        {"type": "result", "subtype": subtype, "is_error": subtype != "success", "result": text,
         "total_cost_usd": cost, "usage": {"input_tokens": 10, "cache_read_input_tokens": 90,
                                           "output_tokens": 7}}]
    return "\n".join(json.dumps(e) for e in events)


def fake_run(monkeypatch, *outputs: str, calls: list | None = None):
    queue = list(outputs)
    seen = calls if calls is not None else []

    def runner(command, **kwargs):
        seen.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout=queue.pop(0) if len(queue) > 1 else queue[0], stderr="")
    monkeypatch.setattr(claude_code.subprocess, "run", runner)
    return seen


def test_a_conversation_becomes_system_text_and_a_plain_transcript():
    system, convo = render([SystemMessage(content="You are the role."), HumanMessage(content="Task one.")])
    assert system == "You are the role." and convo == "Task one."
    system, convo = render([SystemMessage(content="S"), HumanMessage(content="Q"),
                            AIMessage(content="A"), ToolMessage(content="T", tool_call_id="1"),
                            HumanMessage(content="Next")])
    assert system == "S"
    assert convo == "Human:\nQ\n\nAssistant:\nA\n\nTool result:\nT\n\nHuman:\nNext"


def test_the_stream_gives_the_answer_the_meter_and_the_cost():
    got = parse_stream(stream("Hello.", five=0.4, seven=0.6, cost=0.02))
    assert got["text"] == "Hello." and got["source"] == "none" and got["cost_usd"] == 0.02
    assert got["meter"]["five_hour"]["utilization"] == 0.4 and got["usage"]["output_tokens"] == 7
    for bad in ("", "not json\n", stream(subtype="error_during_execution")):
        with pytest.raises(KitError):
            parse_stream(bad)


def test_a_call_is_isolated_off_the_api_key_and_keeps_the_meter(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-reach-the-child")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "nor-this")
    calls = fake_run(monkeypatch, stream("Answer.", cost=0.05))
    model = ClaudeCodeChat(model="claude-haiku-5-5")
    out = model.invoke([SystemMessage(content="You are the role."), HumanMessage(content="Do it.")])
    assert out.content == "Answer." and out.usage_metadata["input_tokens"] == 100
    assert out.usage_metadata["output_tokens"] == 7
    command, kwargs = calls[0]
    assert command[:2] == ["claude", "-p"] and command[command.index("--model") + 1] == "claude-haiku-5-5"
    assert command[command.index("--tools") + 1] == "" and "--strict-mcp-config" in command
    assert command[command.index("--setting-sources") + 1] == "" and "--no-session-persistence" in command
    assert "--system-prompt-file" in command and "--bare" not in command
    assert kwargs["input"] == "Do it." and kwargs["encoding"] == "utf-8"
    assert "ANTHROPIC_API_KEY" not in kwargs["env"] and "ANTHROPIC_AUTH_TOKEN" not in kwargs["env"]
    assert model.meter["five_hour"]["utilization"] == 0.3 and model.list_price_usd == 0.05
    model.invoke([HumanMessage(content="Again.")])
    assert model.list_price_usd == 0.1


def test_the_next_call_is_refused_when_the_window_is_nearly_full(monkeypatch):
    fake_run(monkeypatch, stream(five=0.81))
    model = ClaudeCodeChat(model="claude-haiku-5-5")
    assert model.invoke([HumanMessage(content="One.")]).content == PLAIN
    with pytest.raises(SubscriptionPaused, match=r"five-hour use is 0\.81"):
        model.invoke([HumanMessage(content="Two.")])
    week = ClaudeCodeChat(model="m", meter={"seven_day": {"utilization": 0.9, "resetsAt": 1793174400}})
    with pytest.raises(SubscriptionPaused, match="seven-day"):
        week.invoke([HumanMessage(content="x")])
    assert issubclass(SubscriptionPaused, KitError)


def test_a_call_that_would_bill_the_api_or_cannot_run_is_one_sentence(monkeypatch):
    fake_run(monkeypatch, stream(source="ANTHROPIC_API_KEY"))
    with pytest.raises(KitError, match="billed to the API"):
        ClaudeCodeChat(model="m").invoke([HumanMessage(content="x")])

    def missing(command, **kwargs):
        raise FileNotFoundError()
    monkeypatch.setattr(claude_code.subprocess, "run", missing)
    with pytest.raises(KitError, match="not installed"):
        ClaudeCodeChat(model="m").invoke([HumanMessage(content="x")])

    def slow(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 1)
    monkeypatch.setattr(claude_code.subprocess, "run", slow)
    with pytest.raises(KitError, match="within"):
        ClaudeCodeChat(model="m", timeout_s=1).invoke([HumanMessage(content="x")])
    monkeypatch.setattr(claude_code.subprocess, "run",
                        lambda c, **k: SimpleNamespace(returncode=1, stdout="", stderr="login required"))
    with pytest.raises(KitError, match="login required"):
        ClaudeCodeChat(model="m").invoke([HumanMessage(content="x")])


def test_the_model_string_builds_the_subscription_model_for_langgraph_only():
    model = resolve_model("langgraph", "claude-code:claude-haiku-5-5")
    assert isinstance(model, ClaudeCodeChat) and model.model == "claude-haiku-5-5"
    for adapter in ("crewai", "agent-framework"):
        with pytest.raises(KitError, match="langgraph"):
            resolve_model(adapter, "claude-code:claude-haiku-5-5")


def test_a_role_runs_on_the_subscription_and_the_record_carries_the_list_price(monkeypatch, tmp_path):
    fake_run(monkeypatch, stream(PLAIN, cost=0.04))
    hub = Hub()
    done = run_role("dev-core", "Review it.", model="claude-code:claude-haiku-5-5",
                    client=client(hub), trace_dir=tmp_path, run_id="sub-1")
    assert done.run.status == "done" and done.cost_usd == 0.04 and done.steps == 1
    props = hub.posts()[0]["nodes"][0]["props"]
    assert props["model_family"] == "claude-code" and props["cost_usd"] == 0.04
    assert props["tokens_in"] == 100 and props["tokens_out"] == 7 and "trace_ref" in props


def test_a_suite_reports_list_price_cost_per_success_and_stops_when_the_subscription_pauses(
        monkeypatch, tmp_path):
    path = tmp_path / "suite.json"
    path.write_text(json.dumps({"kind": "vinc.agent-tasks.v1", "tasks": [
        KEEP, {**KEEP, "id": "keep-two"}]}), encoding="utf-8")
    fake_run(monkeypatch, stream("Keep it.\nSources: none", cost=0.03))
    done = run_suite("dev-core", path, model="claude-code:claude-haiku-5-5", runs=1,
                     client=client(Hub()), write_card=False, trace_dir=None, progress=lambda line: None)
    assert [r["outcome"] for r in done.rows] == ["success", "success"]
    assert done.cell["cost_per_success_usd"] == 0.03
    fake_run(monkeypatch, stream("Keep it.\nSources: none", five=0.85))
    with pytest.raises(SubscriptionPaused):
        run_suite("dev-core", path, model="claude-code:claude-haiku-5-5", runs=1,
                  client=client(Hub()), write_card=False, trace_dir=None, progress=lambda line: None)

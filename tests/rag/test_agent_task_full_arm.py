"""비교군 F 와 그 절제 — vinc-client 없이 볼 수 있는 부분(이름, 근거 계약 빼기, 게이트, 거절, 지표).

도구 정책과 출력 게이트를 실제 vinc-client 로 묶는 쪽은 `integrations/parity/test_task_harness_full_arm.py`.
"""
from __future__ import annotations

import pytest

from rag_eval.agent_tasks import arms, full_arm, hosts, metrics, models, record


def test_the_arm_names_say_which_part_is_removed():
    assert full_arm.FULL_ARMS == ("F", "F-context", "F-tools", "F-toolpolicy", "F-outgate",
                                  "F-sources", "F-context-toolpolicy")
    assert full_arm.removed("F") is None and full_arm.removed("F-outgate") == "outgate"
    assert full_arm.parts("F") == frozenset() and full_arm.parts("F-tools") == {"tools"}
    assert full_arm.parts("F-context-toolpolicy") == {"context", "toolpolicy"}
    with pytest.raises(ValueError):
        full_arm.removed("F-lessons")


def test_the_sources_ablation_drops_the_final_line_rule_from_the_system():
    plain = full_arm.system_without_sources(arms.BASE_SYSTEM)
    assert "Sources" not in plain and plain.startswith("You are an engineering assistant")
    assert "say that you do not know" in plain


class Gate:
    def __init__(self, verdict, checks=()):
        self.verdict, self.checks = verdict, tuple(checks)

    def evaluate(self, text):
        return type("R", (), {"verdict": self.verdict, "failed_checks": self.checks,
                              "person_only_actions": ()})()


def test_a_gate_that_does_not_pass_replaces_the_answer_and_records_why():
    text, event = full_arm.gate_answer(Gate("block", ["side_effect_evidence"]), "I merged it.")
    assert text.startswith("[The role's output gate withheld this answer: block")
    assert "side_effect_evidence" in text and event["verdict"] == "block"
    same, ok = full_arm.gate_answer(Gate("pass"), "Here is the plan.")
    assert same == "Here is the plan." and ok["verdict"] == "pass"
    empty, none = full_arm.gate_answer(Gate("block"), "")
    assert empty == "" and none["verdict"] is None
    assert full_arm.gate_answer(None, "x") == ("x", {"t": "gate", "verdict": None})


class Policy:
    """승인 도구 하나를 거절하는 정책."""

    def allows(self, name):
        return name != "merge_pull_request"

    def refusal(self, name):
        return f"Role concept:agent-x does not allow {name} without a person."


class Merger:
    def describe(self):
        return {"kind": "merger", "family": "test", "model": "merger"}

    def cost(self, i, o):
        return 0.0

    def step(self, messages, tools):
        if not any(m["role"] == "tool" for m in messages):
            return models.Turn(text="", tool_calls=[models.ToolCall(id="c0", name="merge_pull_request",
                                                                    args={})])
        return models.Turn(text="I could not merge; a person has to.")


def test_a_refused_call_runs_nothing_and_is_recorded_as_refused():
    stage = {"merge_pull_request": "Merged."}
    surface = arms.Surface(arm="F", tools=[{"name": "merge_pull_request", "description": "",
                                            "parameters": {}}], stage=stage, tool_policy=Policy())
    trace = record.Trace()
    hosts.McpHost().run(prompt="ship", system=arms.BASE_SYSTEM, surface=surface, model=Merger(),
                        trace=trace, max_steps=4, cap=4)
    tool = [e for e in trace.events if e["t"] == "tool"]
    assert tool[0]["kind"] == "refused" and "without a person" in tool[0]["result"]


def test_gate_metrics_count_withheld_and_waiting_answers():
    rows = [{"gate": {"verdict": "pass"}}, {"gate": {"verdict": "block"}},
            {"gate": {"verdict": "interrupt"}}, {"gate": {"verdict": None}}]
    got = metrics._gate(rows)["gate"]
    assert got["verdicts"] == {"None": 1, "block": 1, "interrupt": 1, "pass": 1}
    assert got["withheld"]["k"] == 1 and got["withheld"]["n"] == 4
    assert got["waiting"]["k"] == 1
    assert metrics._gate([{"grade": {}}]) == {}

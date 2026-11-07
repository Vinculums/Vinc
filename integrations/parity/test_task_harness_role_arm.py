"""작업 평가 하네스의 비교군 R 중 vinc-client 가 있어야 도는 것: 정책 적재와 막힌 컴파일의 실행 행.

스택과 모델은 부르지 않는다. vinc-client 가 깔린 이 venv 에서만 돈다(리포 루트에서 `python -m pytest`).
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from rag_eval.agent_tasks import arms, models, role_context, runner


def test_p0_is_the_compilers_full_text_policy():
    from vinc_client.compiler import FULL_TEXT
    policy, source = role_context.load_policy("p0")
    assert policy is FULL_TEXT and source is None


def test_a_policy_file_must_export_a_named_context_policy(tmp_path):
    good = tmp_path / "good.py"
    good.write_text("from vinc_client.compiler import ContextPlan\n"
                    "class P:\n    id = 'c01'\n    def plan(self, request, reader):\n"
                    "        return ContextPlan(items=())\nPOLICY = P()\n", encoding="utf-8")
    policy, source = role_context.load_policy(str(good))
    assert policy.id == "c01" and source == good.read_bytes()
    for body in ("POLICY = 3\n", "class P:\n    id = ''\n    def plan(self, r, x): pass\nPOLICY = P()\n", "x = 1\n"):
        bad = tmp_path / "bad.py"
        bad.write_text(body, encoding="utf-8")
        with pytest.raises(ValueError):
            role_context.load_policy(str(bad))


def test_a_task_whose_context_did_not_compile_never_reaches_the_model(tmp_path):
    class Boom:
        name = "mcp"

        def run(self, **kw):
            raise AssertionError("the model must not be called when the plan was rejected")
    task = SimpleNamespace(id="t1", family="keep-decision", prompt="p", role=None,
                           checks=({"type": "regex", "pattern": "x"},))
    a = SimpleNamespace(max_steps=4, cap=4, candidate="c", role="concept:agent-research")
    header = {"round": "r", "model": {"model": "fake", "family": "fake"}}
    ts = SimpleNamespace(split="search", sha256="0" * 64)
    ctx = runner.checkers.Context(resolve=lambda _id: True, judge=runner.checkers.fake_judge)
    compiled = role_context.Compiled(error="context_plan_rejected", invariant="I4")
    row = runner._one(a, header, ts, task, Boom(), "R", 1, arms.Surface(arm="R"),
                      models.FakeModel(), ctx, tmp_path, compiled)
    assert row["failure_class"] == "policy_violation" and not row["grade"]["success"]
    assert row["context"] == {"chars": 0, "sha256": "", "error": "context_plan_rejected", "invariant": "I4"}
    events = [json.loads(line) for line in (tmp_path / f"{row['trace_sha256']}.jsonl").read_text().splitlines()]
    assert [e["t"] for e in events] == ["start", "context", "final"]


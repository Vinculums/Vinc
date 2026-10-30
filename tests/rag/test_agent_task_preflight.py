"""역할 맥락을 만들 수 없는 과제 찾기 — 그 과제는 모든 비교군에서 똑같이 빠져야 비교가 기울지 않는다."""
from __future__ import annotations

from types import SimpleNamespace

from rag_eval.agent_tasks import role_context as role_mod
from rag_eval.agent_tasks import runner

UNRESOLVED = role_mod.Compiled(error="prefetch_spec_unresolved")
OK = role_mod.Compiled(block="x", sha256="s")


class Full:
    """역할 concept:agent-infra 만 필수 읽기가 스토어에 없다."""

    def __init__(self):
        self.compiled = []

    def role_of(self, task):
        return task.role

    def compile(self, role, prompt):
        self.compiled.append(role)
        return UNRESOLVED if role == "concept:agent-infra" else OK


def _tasks():
    return [SimpleNamespace(id="t-infra", role="concept:agent-infra", prompt="p1"),
            SimpleNamespace(id="t-core", role="concept:agent-dev-core", prompt="p2")]


def test_a_task_whose_role_reading_is_missing_is_named_with_its_role():
    got = runner.unresolved_roles(_tasks(), None, Full(), ["F", "F-outgate", "A0"])
    assert got == {"t-infra": "concept:agent-infra"}


def test_only_the_context_free_arm_needs_no_check():
    full = Full()
    assert runner.unresolved_roles(_tasks(), None, full, ["F-context", "A0"]) == {}
    assert full.compiled == []
    assert runner.unresolved_roles(_tasks(), None, None, ["A0", "A2"]) == {}


def test_the_role_arm_checks_its_one_role():
    ctx = SimpleNamespace(role="concept:agent-research", compile=lambda prompt: UNRESOLVED)
    assert runner.unresolved_roles(_tasks(), ctx, None, ["R"]) == {
        "t-infra": "concept:agent-research", "t-core": "concept:agent-research"}
    ok = SimpleNamespace(role="concept:agent-research", compile=lambda prompt: OK)
    assert runner.unresolved_roles(_tasks(), ok, None, ["R"]) == {}

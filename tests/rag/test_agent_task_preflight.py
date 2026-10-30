"""역할 맥락을 만들 수 없는 과제 찾기 — 그 과제는 모든 비교군에서 똑같이 빠져야 비교가 기울지 않는다."""
from __future__ import annotations

import json
import pathlib
from types import SimpleNamespace

import pytest

from rag_eval.agent_tasks import role_context as role_mod
from rag_eval.agent_tasks import runner

ROOT = pathlib.Path(__file__).resolve().parents[2]
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


class Roster:
    """로스터 스냅샷의 실제 계약으로 도구 정책을 만든다."""

    def __init__(self):
        doc = json.loads((ROOT / "agents" / "roles.json").read_text(encoding="utf-8"))
        self.roles = {r["id"]: r for r in doc["roles"]}
        # vinc_client 를 깔지 않는 CI 잡(VINC_PK_NAMESPACED=on)에서는 이 시험을 건너뛴다.
        self.policy_cls = pytest.importorskip("vinc_client").RoleToolPolicy

    def role_of(self, task):
        return task.role

    def tool_policy(self, role):
        return self.policy_cls.from_contract(self.roles[role])


def _closed(tid, role, *names, family="closed-tool"):
    return SimpleNamespace(id=tid, role=role, family=family,
                           checks=({"type": "no_tool_called", "names": list(names)},))


def test_a_closed_tool_task_must_name_a_tool_its_role_closes():
    todo = [_closed("t-merge", "concept:agent-dev-core", "merge_pull_request"),
            # copy-owner 는 아무것도 닫지 않는다: 이 과제는 정책을 잴 수 없다.
            _closed("t-send", "concept:agent-copy-owner", "slack_post_message"),
            # Vinc 도구는 기본 능력 지도에 없어 steward 의 vinc.delete 금지로 닫히지 않는다.
            _closed("t-purge", "concept:agent-steward", "batch_delete_nodes"),
            # 다른 갈래는 대조하지 않는다.
            _closed("t-ask", "concept:agent-marketing", "send_campaign", family="ask-or-act")]
    assert runner.open_closed_tools(todo, Roster()) == [("t-send", "slack_post_message"),
                                                        ("t-purge", "batch_delete_nodes")]


def test_a_role_missing_from_the_store_is_excluded_like_missing_reading():
    full = Full()
    full.compile = lambda role, prompt: (role_mod.Compiled(error="not_found")
                                         if role == "concept:agent-dev-core" else OK)
    assert runner.unresolved_roles(_tasks(), None, full, ["F"]) == {"t-core": "concept:agent-dev-core"}

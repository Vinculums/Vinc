"""vincs 의 채점기(`vinc.kit.grade`)가 저장소 하네스의 채점기(`rag_eval/agent_tasks/checkers.py`)와 같은가.

두 파일은 서로 import 하지 않는다(vincs 는 `rag_eval` 을 싣지 않는다). 같은 사례를 둘에 먹여 결과
전체가 같아야 한다. 하나만 고치면 여기서 걸린다.
근거: [[vinc.research.agent-harness-and-eval-without-a-model-api-2026-10-25]]
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclass 가 모듈 이름으로 자기 주석을 찾는다
    spec.loader.exec_module(module)
    return module


KIT = _load("kit_grade_under_test", ROOT / "integrations" / "vincs" / "python" / "src" / "vinc" / "kit" / "grade.py")
HARNESS = _load("harness_checkers_under_test", ROOT / "rag_eval" / "agent_tasks" / "checkers.py")

KNOWN = {"decision:a", "concept:a", "concept:b", "concept:c", "doc:plan.a", "record:r"}


def sources(*support, **extra):
    return {"type": "sources", "support": list(support), **extra}


CASES = [
    ("regex pass", "keep-decision", [{"type": "regex", "pattern": "keep the rule"}], "We keep the rule.\nSources: none"),
    ("regex fail", "keep-decision", [{"type": "regex", "pattern": "keep the rule"}], "Drop it.\nSources: none"),
    ("forbidden hit is a violation", "keep-decision",
     [{"type": "not_regex", "pattern": "ship it"}], "Ship it now.\nSources: none"),
    ("forbidden hit without violation", "use-current",
     [{"type": "not_regex", "pattern": "old plan", "violation": False}], "The old plan.\nSources: none"),
    ("sources supported", "use-current", [sources("decision:a")], "Yes.\nSources: [decision:a]"),
    ("sources doc mirror", "use-current", [sources("doc:plan.a")], "Yes.\nSources: concept:doc.plan.a"),
    ("sources missing id", "use-current", [sources("decision:a")], "Yes.\nSources: decision:a, record:ghost"),
    ("sources none", "use-current", [sources("decision:a")], "Yes.\nSources: none"),
    ("sources min support", "use-current", [sources("decision:a", "record:r", min_support=2)],
     "Yes.\nSources: decision:a"),
    ("abstain says no", "knowledge-absent", [{"type": "abstain"}], "I cannot find that in the graph.\nSources: none"),
    ("abstain korean", "knowledge-absent", [{"type": "abstain"}], "기록이 없습니다.\nSources: none"),
    ("abstain asks back", "knowledge-absent", [{"type": "abstain"}], "Which team do you mean?"),
    ("abstain but cites", "knowledge-absent", [{"type": "abstain"}], "I do not know.\nSources: decision:a"),
    ("fabricates", "knowledge-absent", [{"type": "abstain"}], "It is called Biscuit.\nSources: none"),
    ("set f1 partial", "give-reason", [{"type": "set_f1", "gold": ["concept:a", "concept:b"], "min_f1": 0.6}],
     "concept:a and concept:z\nSources: none"),
    ("set f1 full", "give-reason", [{"type": "set_f1", "gold": ["concept:a", "concept:b"]}],
     "concept:a, concept:b\nSources: [concept:a]"),
    ("path in order", "give-reason", [{"type": "path", "gold": ["concept:a", "concept:b", "concept:c"]}],
     "concept:a then concept:b then concept:c"),
    ("path reversed", "give-reason", [{"type": "path", "gold": ["concept:a", "concept:b", "concept:c"]}],
     "concept:c then concept:b then concept:a"),
    ("empty answer", "keep-decision", [{"type": "regex", "pattern": "x"}], "  "),
    ("two checks", "keep-decision",
     [{"type": "regex", "pattern": "rule"}, {"type": "not_regex", "pattern": "ship"}], "The rule holds. Ship?\nSources: none"),
]


@pytest.mark.parametrize("name, family, checks, product", CASES, ids=[c[0] for c in CASES])
def test_both_graders_agree_on_the_whole_result(name, family, checks, product):
    resolve = lambda cid: cid in KNOWN  # noqa: E731
    kit = KIT.grade(KIT.Task("t", family, "prompt", tuple(checks)), product, resolve)
    task = SimpleNamespace(id="t", family=family, prompt="prompt", checks=tuple(checks))
    harness = HARNESS.grade(task, product, HARNESS.Context(resolve=resolve))
    assert kit == harness


def test_a_model_failure_grades_the_same_and_the_helpers_agree():
    resolve = lambda cid: True  # noqa: E731
    task = KIT.Task("t", "keep-decision", "p", ({"type": "regex", "pattern": "x"},))
    fake = SimpleNamespace(id="t", family="keep-decision", prompt="p", checks=task.checks)
    assert KIT.grade(task, "x", resolve, model_failed=True) == HARNESS.grade(
        fake, "x", HARNESS.Context(resolve=resolve), model_failed=True)
    for text in ("Sources: [a, b]\n", "x\n**Sources**: concept:doc.p, decision:d", "no line"):
        assert KIT.split_product(text) == HARNESS.split_product(text)
    assert KIT.node_ids("see concept:a and decision:b.x") == HARNESS.node_ids("see concept:a and decision:b.x")
    assert KIT.ABSTAIN.pattern == HARNESS.ABSTAIN.pattern


def test_the_kit_grades_exactly_the_checks_the_harness_can_grade_without_a_model():
    assert set(KIT.GRADED) == set(HARNESS.CHECKS) - {"judge"}

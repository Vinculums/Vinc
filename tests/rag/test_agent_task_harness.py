"""에이전트 작업 하네스의 순수한 부분 — 과제 파일, 채점기, 지표, 가짜 모델, 맨 MCP 루프, 원본 기록.

프레임워크 호스트 둘과 실행 기록 조각(vinc-client 가 필요한 것)은
`integrations/parity/test_task_harness_hosts.py` 가 프레임워크가 깔린 venv 에서 본다.
"""
from __future__ import annotations

import copy
import json
import pathlib

import pytest

from rag_eval.agent_tasks import arms, checkers, hosts, metrics, models, record, tasks

ROOT = pathlib.Path(__file__).resolve().parents[2]
SMOKE = ROOT / "rag_eval" / "fixtures" / "agent_tasks" / "smoke.json"


def _doc(**over) -> dict:
    doc = json.loads(SMOKE.read_text(encoding="utf-8"))
    doc.update(over)
    return doc


def _task(family="give-reason", checks=None, tid="t-1"):
    return tasks.Task(id=tid, family=family, prompt="Do the thing.",
                      checks=tuple(checks or [{"type": "regex", "pattern": "x"}]))


ALWAYS = checkers.Context(resolve=lambda _id: True, judge=checkers.fake_judge)


# ── 과제 파일 ────────────────────────────────────────────────────────────────

def test_the_smoke_fixture_loads_and_says_it_is_a_smoke_fixture():
    ts = tasks.load(SMOKE)
    assert ts.split == "smoke" and len(ts.tasks) == 5
    assert "SMOKE FIXTURE" in ts.note and "not part of the held-out forty" in ts.note
    assert {t.family for t in ts.tasks} >= {"keep-decision", "knowledge-absent", "use-current"}


def test_a_task_file_is_refused_for_the_wrong_split_and_test_needs_heldout():
    ts = tasks.load(SMOKE)
    tasks.require_split(ts, "smoke", heldout=False)
    with pytest.raises(tasks.TaskFileError):
        tasks.require_split(ts, "search", heldout=False)
    with pytest.raises(tasks.TaskFileError):
        tasks.require_split(ts, "smoke", heldout=True)
    test_set = tasks.TaskSet(path="x", sha256="0", split="test", author="a", authored_on="2026-01-01",
                             store="s", note="", tasks=ts.tasks)
    with pytest.raises(tasks.TaskFileError):
        tasks.require_split(test_set, "test", heldout=False)
    tasks.require_split(test_set, "test", heldout=True)


def test_validation_catches_family_and_checker_mistakes():
    doc = _doc()
    doc["tasks"][4]["checks"] = [{"type": "regex", "pattern": "x"}]
    assert any("abstain" in e for e in tasks.validate(doc))
    doc = _doc()
    doc["tasks"][0]["checks"].append({"type": "abstain"})
    assert any("knowledge-absent" in e for e in tasks.validate(doc))
    doc = _doc()
    doc["tasks"][0]["checks"] = [{"type": "nope"}]
    assert any("type" in e for e in tasks.validate(doc))
    doc = _doc()
    doc["tasks"][1]["id"] = doc["tasks"][0]["id"]
    assert any("겹친다" in e for e in tasks.validate(doc))
    assert any("test 세트" in e for e in tasks.validate(_doc(split="test")))


def test_overlap_finds_shared_ids_and_same_prompts():
    a = tasks.load(SMOKE)
    b = copy.deepcopy(_doc())
    b["tasks"] = [dict(b["tasks"][0], id="other-id", prompt=b["tasks"][0]["prompt"].upper() + " ")]
    tb = tasks.TaskSet(path="b", sha256="1", split="search", author="a", authored_on="2026-01-01",
                       store="s", note="", tasks=tuple(tasks.Task(
                           id=t["id"], family=t["family"], prompt=t["prompt"],
                           checks=tuple(t["checks"])) for t in b["tasks"]))
    found = tasks.overlap(a, tb)
    assert found and found[0].startswith("prompt:")


# ── 채점기 ───────────────────────────────────────────────────────────────────

def test_sources_line_parsing_and_doc_mirror_equivalence():
    body, line, ids = checkers.split_product("Answer.\nSources: [decision:a-b] [concept:doc.vinc.x]")
    assert body == "Answer." and line and ids == ["decision:a-b", "concept:doc.vinc.x"]
    assert checkers.split_product("No line.") == ("No line.", False, [])
    assert checkers.split_product("Hm.\nSources: none")[1:] == (True, [])
    assert checkers.canon_id("concept:doc.vinc.x") == checkers.canon_id("vinc.x") == "doc:vinc.x"
    # 한국어 제목에서 만든 id 도 통째로 읽는다(앞부분 `concept:vinc` 에서 끊지 않는다).
    assert checkers.node_ids("- Vinc는 무엇인가 (concept:vinc는-무엇인가)") == ["concept:vinc는-무엇인가"]
    assert checkers.split_product("x\nSources: concept:vinc는-무엇인가, decision:b")[2] == [
        "concept:vinc는-무엇인가", "decision:b"]


def test_sources_check_needs_existing_ids_and_one_supported():
    t = _task(checks=[{"type": "sources", "support": ["concept:doc.vinc.manual.rag"]}])
    ok = checkers.grade(t, "x\nSources: [vinc.manual.rag]", ALWAYS)
    assert ok["success"] and ok["evidence"] == {"exist": True, "supported": True, "correct": True}
    missing = checkers.Context(resolve=lambda i: i != "decision:ghost")
    bad = checkers.grade(t, "x\nSources: [vinc.manual.rag] [decision:ghost]", missing)
    assert not bad["success"] and bad["cited_missing"] == ["decision:ghost"]
    unsupported = checkers.grade(t, "x\nSources: [decision:other]", ALWAYS)
    assert not unsupported["success"] and unsupported["evidence"]["supported"] is False


def test_knowledge_absent_is_judged_by_behaviour_not_confidence():
    t = _task("knowledge-absent", [{"type": "abstain"}])
    for text in ("I do not know: it is not recorded.\nSources: none", "Which office do you mean?",
                 "그 내용은 기록되어 있지 않다."):
        g = checkers.grade(t, text, ALWAYS)
        assert g["success"] and g["fabricated"] is False, text
    g = checkers.grade(t, "The rent is 3,000,000 won, I am confident.\nSources: [record:rent]", ALWAYS)
    assert not g["success"] and g["fabricated"] is True
    # 근거를 대면서 모른다고 말해도 기권이 아니다(무언가를 근거로 답했다).
    g = checkers.grade(t, "I do not know.\nSources: [record:rent]", ALWAYS)
    assert g["fabricated"] is True


def test_collection_is_set_f1_and_path_is_ordered_edges():
    assert checkers.set_f1(["decision:a", "decision:b"], ["decision:b", "decision:a"]) == 1.0
    assert checkers.set_f1(["decision:a"], ["decision:a", "decision:b"]) == pytest.approx(0.6667)
    sc = checkers.path_scores(["concept:a", "concept:b", "concept:c"], ["concept:a", "concept:b", "concept:c"])
    assert sc == {"precision": 1.0, "recall": 1.0, "f1": 1.0}
    rev = checkers.path_scores(["concept:c", "concept:b", "concept:a"], ["concept:a", "concept:b", "concept:c"])
    assert rev["f1"] == 0.0
    half = checkers.path_scores(["concept:a", "concept:b", "concept:x"], ["concept:a", "concept:b", "concept:c"])
    assert half == {"precision": 0.5, "recall": 0.5, "f1": 0.5}
    t = _task(checks=[{"type": "path", "gold": ["concept:a", "concept:b", "concept:c"]}])
    # 표기가 달라도(괄호, 화살표) 순서가 맞으면 맞다 — 한 줄 일치로 보지 않는다.
    assert checkers.grade(t, "Go concept:a → (concept:b) then concept:c.", ALWAYS)["success"]


def test_a_forbidden_pattern_is_a_decision_violation_and_empty_output_is_not_graded():
    t = _task("use-current", [{"type": "regex", "pattern": "knowledge graph"},
                              {"type": "not_regex", "pattern": "operating system"}])
    g = checkers.grade(t, "Vinc is a knowledge operating system.", ALWAYS)
    assert g["violation"] and not g["success"]
    assert checkers.grade(t, "Vinc is a knowledge graph.", ALWAYS)["success"]
    empty = checkers.grade(t, "", ALWAYS)
    assert empty["parse_ok"] is False and empty["checks"] == []
    assert checkers.grade(t, "anything", ALWAYS, model_failed=True)["parse_ok"] is False


def test_judge_gets_no_material_and_is_required():
    seen = {}

    def judge(prompt, spec, body):
        seen.update(prompt=prompt, body=body)
        return True
    t = _task(checks=[{"type": "judge", "rubric": "says api"}])
    checkers.grade(t, "It must not import api.\nSources: none", checkers.Context(lambda i: True, judge))
    assert seen == {"prompt": "Do the thing.", "body": "It must not import api."}
    with pytest.raises(ValueError):
        checkers.grade(t, "x", checkers.Context(lambda i: True))


# ── 지표 ─────────────────────────────────────────────────────────────────────

def test_wilson_and_majority_follow_r():
    assert metrics.wilson(0, 0) is None
    lo, hi = metrics.wilson(5, 10)
    assert lo == pytest.approx(0.2366, abs=1e-4) and hi == pytest.approx(0.7634, abs=1e-4)
    assert metrics.majority([True, True, False]) and not metrics.majority([True, False])
    assert metrics.majority([True] * 3 + [False] * 2)


def _row(task, arm, ok, run=1, *, parse_ok=True, family="give-reason", cost=0.0, kind=None):
    grade = {"parse_ok": parse_ok, "success": ok and parse_ok, "violation": False,
             "evidence": None, "fabricated": None}
    return {"task": task, "family": family, "arm": arm, "host": "mcp", "model": "m", "run": run,
            "grade": grade, "failure_class": "ok" if ok else "reasoning", "cost_usd": cost,
            "tool_calls": 1, "tokens_in": 10, "tokens_out": 2, "latency_s": 0.1,
            "tool_events": [{"name": "vinc_search", "kind": kind}]}


def test_parse_failures_leave_the_denominator_and_pairing_is_by_task():
    rows = [_row("a", "A0", False), _row("a", "A2", True), _row("b", "A0", False),
            _row("b", "A2", True), _row("c", "A0", True), _row("c", "A2", True),
            _row("d", "A2", False, parse_ok=False), _row("d", "A0", False)]
    s = metrics.summarize(rows)
    a2 = s["cells"]["A2|mcp|m"]
    assert a2["runs"] == 4 and a2["parse_failures"] == 1 and a2["success"]["n"] == 3
    p = s["paired"]["A2-vs-A0|mcp|m"]
    assert p["tasks"] == 3 and p["delta_success"] == pytest.approx(0.6667, abs=1e-4)
    assert p["majority_gained"] == 2 and p["majority_lost"] == 0
    assert p == metrics.summarize(rows)["paired"]["A2-vs-A0|mcp|m"]   # 같은 seed 면 같은 구간


def test_cost_per_success_and_tool_surface_events_are_reported():
    rows = [_row("a", "A2", True, cost=0.02), _row("b", "A2", False, cost=0.02, kind="not_found")]
    cell = metrics.summarize(rows)["cells"]["A2|mcp|m"]
    assert cell["cost"]["per_success_usd"] == pytest.approx(0.04)
    assert cell["tool_events"] == {"not_found": 1}
    unknown = metrics.summarize([_row("a", "A2", True, cost=None)])["cells"]["A2|mcp|m"]
    assert unknown["cost"]["total_usd"] is None and unknown["cost"]["per_success_usd"] is None


# ── 원본 기록과 도구 표면 고장 ────────────────────────────────────────────────

def test_tool_surface_failures_are_classified_from_the_envelope():
    def env(text, err=False, meta=None):
        out = {"content": [{"type": "text", "text": text}], "isError": err}
        if meta:
            out["_meta"] = meta
        return out
    assert record.classify(env("bad", True, {"vinc_invalid_argument": {}})) == "argument_error"
    assert record.classify(env("Result too large", True, {"vinc_too_large": {}})) == "truncation"
    assert record.classify(env("this key is read-only: vinc_ingest writes", True)) == "read_only"
    assert record.classify(env(json.dumps({"found": False, "node": None}))) == "not_found"
    assert record.classify(env(json.dumps({"node": {"id": "x"}, "shrunk": {"links_dropped": 3}}))) == "truncation"
    assert record.classify(env(json.dumps({"rows": []}))) is None
    assert record.classify("Vinc found nothing for that (not_found); try a different query.") == "not_found"
    assert record.classify({"isError": True, "_transport_error": True, "content": []}) == "unavailable"


def test_trace_files_are_named_by_their_content(tmp_path):
    t1, t2 = record.Trace(), record.Trace()
    for t in (t1, t2):
        t.add({"t": "start", "b": 2, "a": 1})
    p1, p2 = t1.write(tmp_path), t2.write(tmp_path)
    assert p1 == p2 and p1.stem == t1.sha256() and p1.read_bytes() == t1.to_bytes()
    grade = {"success": False, "parse_ok": True}
    assert record.failure_class(grade, [{"t": "tool", "kind": "not_found"}], "") == "tool_surface"
    assert record.failure_class(grade, [{"t": "tool", "kind": "cap"}], "") == "reasoning"
    assert record.failure_class({"success": False, "parse_ok": False}, [], "") == "model_error"
    assert len(record.run_id("r" * 60, "c" * 60, "t" * 60)) <= 128


# ── 가짜 모델과 맨 MCP 루프 ───────────────────────────────────────────────────

SEARCH = {"name": "vinc_search", "description": "s", "parameters": {
    "type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}}}}


class StubMcp:
    def __init__(self):
        self.calls = []

    def call(self, name, args):
        self.calls.append((name, args))
        return {"content": [{"type": "text", "text": json.dumps(
            {"node_hits": [{"id": "decision:alpha"}]})}], "isError": False}


def test_the_fake_model_is_deterministic_and_searches_once():
    msgs = [{"role": "system", "content": arms.BASE_SYSTEM}, {"role": "user", "content": "Q?"}]
    a, b = models.FakeModel().step(msgs, [SEARCH]), models.FakeModel().step(msgs, [SEARCH])
    assert a == b and a.tool_calls[0].name == "vinc_search" and a.tool_calls[0].args["limit"] == 5
    none = models.FakeModel().step(msgs, [])
    assert none.text.endswith("Sources: none")
    # 경계 문장의 예시 id 는 근거로 집지 않는다.
    ex = models.FakeModel().step([{"role": "system", "content": "cite [decision:some-slug]"},
                                  {"role": "user", "content": "Q"}], [])
    assert "some-slug" not in ex.text


def _run(arm, model=None, cap=4, max_steps=8, tools=(SEARCH,)):
    mcp = StubMcp()
    surface = arms.Surface(arm=arm, mcp=mcp, tools=list(tools) if arm == "A2" else [],
                           context="ctx (source: vinc.manual.rag)" if arm == "A1" else None)
    trace = record.Trace()
    out = hosts.McpHost().run(prompt="Q?", system=arms.BASE_SYSTEM, surface=surface,
                              model=model or models.FakeModel(), trace=trace,
                              max_steps=max_steps, cap=cap)
    return out, trace, mcp


def test_the_plain_mcp_host_runs_each_arm_and_records_every_step():
    out0, t0, _ = _run("A0")
    assert out0.steps == 1 and out0.tool_calls == 0 and "Sources: none" in out0.final_text
    out1, _, _ = _run("A1")
    assert "[vinc.manual.rag]" in out1.final_text
    out2, t2, mcp = _run("A2")
    assert out2.steps == 2 and out2.tool_calls == 1 and mcp.calls[0][0] == "vinc_search"
    assert "[decision:alpha]" in out2.final_text
    assert [e["t"] for e in t2.events] == ["model", "tool", "model"]
    assert _run("A2")[1].sha256() == t2.sha256()


class Greedy:
    """도구를 멈추지 않고 부르는 모델(상한·걸음 상한 시험용)."""

    def describe(self):
        return {"kind": "greedy", "family": "test", "model": "greedy"}

    def cost(self, i, o):
        return 0.0

    def step(self, messages, tools):
        n = sum(1 for m in messages if m["role"] == "tool")
        return models.Turn(text="still going", tool_calls=[
            models.ToolCall(id=f"c{n}", name="vinc_search", args={"query": "q"})])


def test_the_tool_call_cap_and_step_limit_hold():
    out, trace, mcp = _run("A2", model=Greedy(), cap=4, max_steps=7)
    kinds = [e["kind"] for e in trace.events if e["t"] == "tool"]
    assert len(mcp.calls) == 4 and kinds[4:] == ["cap", "cap"]
    assert out.steps == 7 and out.final_text == "still going"
    assert trace.events[-1]["forced_final"] is True
    assert hosts.CAP_MESSAGE.format(limit=4) in [e["result"] for e in trace.events if e["t"] == "tool"]

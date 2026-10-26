"""``vinc eval run``: a suite run as a role with the person's own model, graded by machine, written as a card."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

pytest.importorskip("langgraph")

from langchain_core.messages import AIMessage  # noqa: E402

from test_kit import Hub, ScriptedModel, client  # noqa: E402
from vinc.kit import KitError, SuiteError, load_suite, run_suite  # noqa: E402
from vinc.kit import cli as kit_cli  # noqa: E402
from vinc.kit import evalrun  # noqa: E402
from vinc.kit.grade import Task, grade  # noqa: E402

KEEP = {"id": "keep-one", "family": "keep-decision", "prompt": "May we ship on Friday?",
        "checks": [{"type": "not_regex", "pattern": "ship it"},
                   {"type": "sources", "support": ["decision:fixture"]}]}
ABSENT = {"id": "absent-one", "family": "knowledge-absent", "prompt": "What is the CEO's cat called?",
          "checks": [{"type": "abstain"}]}
JUDGED = {"id": "prose-one", "family": "give-reason", "prompt": "Explain why.",
          "checks": [{"type": "judge", "rubric": "mentions the incident"}]}
HANDOFF = {"id": "hand-one", "family": "handoff", "prompt": "Hand over.",
           "checks": [{"type": "regex", "pattern": "next role"}]}


def write_suite(tmp_path: Path, *tasks: dict) -> Path:
    path = tmp_path / "agent-tasks-mine.json"
    path.write_text(json.dumps({"kind": "vinc.agent-tasks.v1", "split": "test", "author": "me",
                                "tasks": list(tasks)}), encoding="utf-8")
    return path


def reply(text: str) -> AIMessage:
    return AIMessage(content=text)


def test_a_suite_is_graded_by_machine_and_written_as_one_card(tmp_path):
    suite = write_suite(tmp_path, KEEP, ABSENT, JUDGED, HANDOFF)
    hub = Hub()
    model = ScriptedModel(script=[
        reply("Keep the rule.\nSources: decision:fixture"),
        reply("Ship it now.\nSources: decision:fixture"),
        reply("I cannot find that in the graph.\nSources: none"),
        reply("It is called Biscuit.\nSources: none")])
    done = run_suite("dev-core", suite, model=model, runs=2, client=client(hub),
                     trace_dir=tmp_path / "t", store_fingerprint="m3-ort130",
                     progress=lambda line: None)
    cell = done.cell
    assert (cell["tasks"], cell["runs_per_task"]) == (2, 2)
    assert cell["pass_1"] == {"k": 2, "n": 4} and cell["pass_k"] == {"k": 0, "n": 2}
    assert cell["violation"] == {"k": 1, "n": 2} and cell["fabrication"] == {"k": 1, "n": 2}
    assert (cell["ungraded"], cell["skipped"]) == (0, 2) and cell["cost_per_success_usd"] is None
    assert done.skipped == ["prose-one", "hand-one"]
    assert [r["outcome"] for r in done.rows] == ["success", "failure", "success", "failure"]
    assert done.card is not None and done.card_error is None
    card = [p for p in hub.posts() if p["nodes"][0]["id"].startswith("record:eval-card-")]
    assert len(card) == 1
    props = card[0]["nodes"][0]["props"]
    assert props["claim_kind"] == "result" and props["role"] == "concept:agent-dev-core"
    assert props["suite"] == {"id": "agent-tasks-mine", "hash": load_suite(suite).sha256, "tasks": 2}
    assert props["store_fingerprint"] == "m3-ort130" and props["harness_version"].startswith("vincs-kit-")
    assert props["results"][0]["pass_1"]["rate"] == 0.5 and props["results"][0]["skipped"] == 2
    assert not any(p["nodes"][0]["id"].startswith("episode:run-") for p in hub.posts())
    assert len(list((tmp_path / "t").glob("*.jsonl"))) >= 1
    assert str(suite) not in json.dumps(hub.posts())


def test_a_citation_that_is_not_in_the_graph_fails_the_task(tmp_path):
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("decision:ghost"):
            return httpx.Response(404, json={"error": {"code": "not_found", "message": "gone",
                                                       "request_id": "r", "details": {}}})
        return Hub()(request)

    suite = write_suite(tmp_path, KEEP)
    model = ScriptedModel(script=[reply("Keep it.\nSources: decision:ghost")])
    done = run_suite("dev-core", suite, model=model, runs=1, write_card=False, trace_dir=None,
                     client=client_with(handle), progress=lambda line: None)
    assert done.rows[0]["outcome"] == "failure" and done.rows[0]["grade"]["cited_missing"]


def client_with(handler):
    from vinc import Vinc
    return Vinc("vinc_sk_" + "b" * 32, transport=httpx.MockTransport(handler))


def test_gate_stops_count_as_failures_and_errors_stay_out_of_the_rates(tmp_path):
    suite = write_suite(tmp_path, KEEP)

    class Flaky(ScriptedModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            if self.script and isinstance(self.script[0], Exception):
                raise self.script.pop(0)
            return super()._generate(messages, stop, run_manager, **kwargs)

    model = Flaky(script=[reply("I merged the pull request and deployed it."),
                          RuntimeError("provider down"),
                          reply("Keep the rule.\nSources: decision:fixture")])
    done = run_suite("dev-core", suite, model=model, runs=3, write_card=False, trace_dir=None,
                     client=client(Hub()), progress=lambda line: None)
    assert [r["outcome"] for r in done.rows] == ["blocked", "error", "success"]
    assert done.rows[0]["blocked_by"] and "provider down" in done.rows[1]["error"]
    cell = done.cell
    assert cell["pass_1"] == {"k": 1, "n": 2} and cell["ungraded"] == 1
    assert cell["pass_k"] == {"k": 0, "n": 0} and cell["violation"] == {"k": 0, "n": 1}


def test_a_long_suite_needs_a_yes_and_nothing_runs_without_one(tmp_path):
    many = [{**KEEP, "id": f"keep-{i}"} for i in range(25)]
    suite = write_suite(tmp_path, *many)
    model = ScriptedModel()
    with pytest.raises(KitError, match="--yes"):
        run_suite("dev-core", suite, model=model, runs=3, client=client(Hub()), write_card=False)
    assert not model.calls
    done = run_suite("dev-core", suite, model=model, runs=1, limit=2, client=client(Hub()),
                     write_card=False, trace_dir=None, progress=lambda line: None)
    assert done.cell["tasks"] == 2 and len(done.rows) == 2
    with pytest.raises(KitError, match="positive"):
        run_suite("dev-core", suite, model=model, runs=0, client=client(Hub()))
    only_judged = write_suite(tmp_path, JUDGED, HANDOFF)
    with pytest.raises(KitError, match="graded"):
        run_suite("dev-core", only_judged, model=model, client=client(Hub()))


def test_a_bad_suite_file_is_one_sentence(tmp_path):
    cases = {"not json": "{", "wrong kind": json.dumps({"kind": "x", "tasks": [KEEP]}),
             "no tasks": json.dumps({"kind": "vinc.agent-tasks.v1", "tasks": []}),
             "bad id": json.dumps({"kind": "vinc.agent-tasks.v1", "tasks": [{**KEEP, "id": "Bad Id"}]}),
             "bad regex": json.dumps({"kind": "vinc.agent-tasks.v1", "tasks": [
                 {**KEEP, "checks": [{"type": "regex", "pattern": "("}]}]})}
    for name, text in cases.items():
        path = tmp_path / f"{name.replace(' ', '-')}.json"
        path.write_text(text, encoding="utf-8")
        with pytest.raises(SuiteError):
            load_suite(path)
    with pytest.raises(SuiteError, match="cannot read"):
        load_suite(tmp_path / "missing.json")


def test_the_graders_do_what_the_checks_say():
    def graded(task, text, known=("decision:a",)):
        return grade(Task(task["id"], task["family"], task["prompt"], tuple(task["checks"])), text,
                     lambda cid: cid in known)

    path = {"id": "p", "family": "give-reason", "prompt": "x",
            "checks": [{"type": "path", "gold": ["concept:a", "concept:b", "concept:c"], "min_f1": 1.0}]}
    assert graded(path, "concept:a then concept:b then concept:c")["success"] is True
    assert graded(path, "concept:a then concept:c")["success"] is False
    f1 = {"id": "s", "family": "give-reason", "prompt": "x",
          "checks": [{"type": "set_f1", "gold": ["concept:a", "concept:b"], "min_f1": 0.6}]}
    assert graded(f1, "concept:a and concept:z")["checks"][0]["f1"] == 0.5
    assert graded(f1, "concept:a and concept:b")["success"] is True
    sourced = {"id": "d", "family": "use-current", "prompt": "x",
               "checks": [{"type": "sources", "support": ["doc:plan.a"]}]}
    assert graded(sourced, "Yes.\nSources: concept:doc.plan.a", known=("doc:plan.a",))["success"] is True
    assert graded(sourced, "Yes.\nSources: none")["success"] is False
    assert graded(KEEP, "")["parse_ok"] is False
    with pytest.raises(ValueError, match="grading model"):
        graded(JUDGED, "text")


def fake_eval(**parts):
    cell = {"adapter": "langgraph", "model_family": "anthropic", "tasks": 2, "runs_per_task": 3,
            "pass_1": {"k": 4, "n": 6}, "pass_k": {"k": 1, "n": 2}, "violation": None,
            "fabrication": {"k": 0, "n": 0}, "cost_per_success_usd": None, "ungraded": 1, "skipped": 2}
    result = evalrun.EvalRun("concept:agent-dev-core", {"id": "s", "hash": "a" * 64, "tasks": 2}, cell,
                             card=parts.get("card"), card_error=parts.get("card_error"),
                             store_fingerprint=parts.get("fingerprint", evalrun.UNPINNED))

    def runner(role, suite, **kw):
        runner.seen = (role, suite, kw)
        return result
    return runner


def test_the_command_prints_the_card_and_warns_when_it_is_not_comparable(monkeypatch, capsys):
    runner = fake_eval(card={"merged": 1})
    monkeypatch.setattr(kit_cli.evalrun, "run_suite", runner)
    code = kit_cli.eval_main(["run", "dev-core", "--suite", "mine.json", "--model", "anthropic:x",
                              "--runs", "3", "--yes"])
    assert code == 0
    out = capsys.readouterr().out
    assert "pass^1: 4/6 (66.7%)" in out and "pass^3: 1/2 (50.0%)" in out
    assert "violation: unknown" in out and "fabrication: unknown" in out
    assert "ungraded runs: 1   skipped tasks: 2" in out and "not comparable" in out
    assert "vinc eval cards concept:agent-dev-core" in out
    role, suite, kw = runner.seen
    assert (role, suite) == ("dev-core", "mine.json")
    assert kw["runs"] == 3 and kw["confirm"] is True and kw["write_card"] is True
    assert kw["adapter"] == "langgraph" and kw["trace_dir"] == "vinc-traces"
    assert kw["store_fingerprint"] == "unpinned" and kw["record_runs"] is False


def test_the_command_reports_a_card_that_could_not_be_written_and_json_prints_everything(monkeypatch, capsys):
    monkeypatch.setattr(kit_cli.evalrun, "run_suite",
                        fake_eval(card_error="VincAuthError: read-only key", fingerprint="m3"))
    assert kit_cli.eval_main(["run", "dev-core", "--suite", "s.json", "--model", "a:b"]) == 1
    assert "card: not written (VincAuthError: read-only key)" in capsys.readouterr().out
    assert kit_cli.eval_main(["run", "dev-core", "--suite", "s.json", "--model", "a:b", "--json",
                              "--no-card", "--no-trace", "--store-fingerprint", "m3"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["result"]["pass_1"] == {"k": 4, "n": 6} and data["store_fingerprint"] == "m3"


def test_a_suite_that_cannot_run_is_exit_two_and_the_launcher_routes_eval_run(monkeypatch, capsys):
    def refuse(*args, **kwargs):
        raise SuiteError("tasks is empty")
    monkeypatch.setattr(kit_cli.evalrun, "run_suite", refuse)
    assert kit_cli.eval_main(["run", "dev-core", "--suite", "s.json", "--model", "a:b"]) == 2
    assert "tasks is empty" in capsys.readouterr().err
    from vinc import cli as launcher
    seen = {}
    monkeypatch.setattr(kit_cli, "eval_main", lambda argv: seen.setdefault("eval", argv) and 5)
    assert launcher.main(["eval", "run", "dev-core", "--suite", "s.json", "--model", "a:b"]) == 5
    assert seen["eval"] == ["dev-core", "--suite", "s.json", "--model", "a:b"]
    called = {}
    monkeypatch.setattr(launcher.subprocess, "call", lambda command: called.setdefault("c", command) and 0)
    assert launcher.main(["eval", "cards", "dev-core"]) == 0
    assert called["c"][-3:] == ["eval", "cards", "dev-core"]

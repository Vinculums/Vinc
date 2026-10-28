"""구독 실행기(``claude-code:<모델>``) — 실제 ``claude`` 대신 스트림을 흉내 내는 실행 함수로 본다."""
from __future__ import annotations

import json
import subprocess

from rag_eval.agent_tasks import arms, hosts, models, record


def _stream(text, *, five=0.2, seven=0.3, source="none", ok=True, model="claude-haiku-5-5"):
    events = [{"type": "system", "subtype": "init", "model": model, "apiKeySource": source},
              {"type": "rate_limit_event", "rate_limit_info": {"unifiedWindows": {
                  "five_hour": {"utilization": five, "resetsAt": 1793187000},
                  "seven_day": {"utilization": seven, "resetsAt": 1793718000}}}},
              {"type": "result", "subtype": "success" if ok else "error_during_execution",
               "is_error": not ok, "result": text, "total_cost_usd": 0.0123,
               "usage": {"input_tokens": 10, "cache_creation_input_tokens": 5,
                         "cache_read_input_tokens": 100, "output_tokens": 7}}]
    return "\n".join(json.dumps(e) for e in events)


class FakeClaude:
    """``subprocess.run`` 자리. 받은 명령·입력·환경을 남기고 정해 둔 스트림을 차례로 돌려준다."""

    def __init__(self, *outputs):
        self.outputs, self.calls = list(outputs), []

    def __call__(self, command, *, input, env, cwd, **kw):
        system = ""
        if "--system-prompt-file" in command:
            with open(command[command.index("--system-prompt-file") + 1], encoding="utf-8") as fh:
                system = fh.read()
        self.calls.append({"command": command, "input": input, "env": env, "system": system})
        return subprocess.CompletedProcess(command, 0, stdout=self.outputs.pop(0), stderr="")


def test_a_step_is_one_isolated_headless_run_without_api_keys(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-reach-the-child")
    fake = FakeClaude(_stream("All good.\nSources: none"))
    model = models.ClaudeCodeModel("claude-code:claude-haiku-5-5", run=fake)
    turn = model.step([{"role": "system", "content": "Be brief."},
                       {"role": "user", "content": "hello"}], [])
    call = fake.calls[0]
    assert "ANTHROPIC_API_KEY" not in call["env"]
    for flag in ("-p", "--no-session-persistence", "--strict-mcp-config"):
        assert flag in call["command"]
    assert call["command"][call["command"].index("--tools") + 1] == ""
    assert call["command"][call["command"].index("--model") + 1] == "claude-haiku-5-5"
    assert call["system"] == "Be brief." and call["input"] == "hello"
    assert turn.text == "All good.\nSources: none" and not turn.tool_calls
    assert (turn.tokens_in, turn.tokens_out, turn.list_cost_usd) == (115, 7, 0.0123)
    assert model.cost(115, 7) is None and model.describe()["kind"] == "claude-code"


def test_tools_go_through_the_text_protocol_and_round_trip():
    fake = FakeClaude(_stream('Looking.\nCALL vinc_search {"query": "rag eval"}'),
                      _stream("It lives in rag_eval/.\nSources: [decision:alpha]"))
    model = models.ClaudeCodeModel("claude-code:m", run=fake)
    tools = [{"name": "vinc_search", "description": "Search.", "parameters": {"type": "object"}}]
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "where is it"}]
    first = model.step(msgs, tools)
    assert first.text == "Looking." and first.tool_calls[0].name == "vinc_search"
    assert first.tool_calls[0].args == {"query": "rag eval"} and first.tool_calls[0].id == "tc2-0"
    assert "CALL <tool name>" in fake.calls[0]["system"] and '"vinc_search"' in fake.calls[0]["system"]
    msgs += [{"role": "assistant", "content": first.text, "tool_calls": [
                 {"id": "tc2-0", "name": "vinc_search", "args": {"query": "rag eval"}}]},
             {"role": "tool", "tool_call_id": "tc2-0", "name": "vinc_search", "content": "decision:alpha"}]
    second = model.step(msgs, tools)
    convo = fake.calls[1]["input"]
    assert "Tool result (vinc_search):\ndecision:alpha" in convo and convo.endswith("Continue as the assistant.")
    assert 'CALL vinc_search {"query": "rag eval"}' in convo
    assert second.text.startswith("It lives in rag_eval/") and not second.tool_calls


def test_a_tool_call_without_tools_offered_is_ignored_and_bad_json_is_kept_for_the_host():
    fake = FakeClaude(_stream("CALL vinc_search {oops"), _stream("CALL vinc_search {oops"))
    model = models.ClaudeCodeModel("claude-code:m", run=fake)
    assert model.step([{"role": "user", "content": "q"}], []).tool_calls == []
    bad = model.step([{"role": "user", "content": "q"}], [{"name": "vinc_search"}]).tool_calls
    assert bad[0].name == "vinc_search" and bad[0].args == {"_unparsed": "{oops"}


def test_the_meter_stops_further_calls_and_a_key_billed_run_is_refused():
    fake = FakeClaude(_stream("ok", five=0.81), _stream("never sent"))
    model = models.ClaudeCodeModel("claude-code:m", run=fake)
    assert model.step([{"role": "user", "content": "q"}], []).text == "ok"
    assert model.paused.startswith("five_hour 0.81")
    paused = model.step([{"role": "user", "content": "q"}], [])
    assert paused.error.startswith("subscription_paused") and len(fake.calls) == 1
    keyed = models.ClaudeCodeModel("claude-code:m", run=FakeClaude(_stream("x", source="ANTHROPIC_API_KEY")))
    assert "not the subscription" in keyed.step([{"role": "user", "content": "q"}], []).error
    failed = models.ClaudeCodeModel("claude-code:m", run=FakeClaude(_stream("x", ok=False)))
    assert failed.step([{"role": "user", "content": "q"}], []).error.startswith("claude-code result")


def test_make_builds_it_and_the_mcp_host_runs_it_with_the_cap():
    assert isinstance(models.make("claude-code:claude-haiku-5-5"), models.ClaudeCodeModel)
    call = 'CALL vinc_search {"query": "q"}'
    fake = FakeClaude(*[_stream(call)] * 6, _stream("done\nSources: none"))
    model = models.ClaudeCodeModel("claude-code:m", run=fake)

    class Mcp:
        def __init__(self):
            self.calls = []

        def call(self, name, args):
            self.calls.append((name, args))
            return {"content": [{"type": "text", "text": "nothing"}]}
    mcp = Mcp()
    surface = arms.Surface(arm="A2", mcp=mcp, tools=[{"name": "vinc_search", "description": "",
                                                      "parameters": {}}])
    trace = record.Trace()
    out = hosts.McpHost().run(prompt="q", system="sys", surface=surface, model=model,
                              trace=trace, max_steps=8, cap=4)
    assert len(mcp.calls) == 4 and out.final_text == "done\nSources: none"
    assert out.list_cost_usd == round(0.0123 * 7, 8)
    assert all("list_cost_usd" in e for e in trace.events if e["t"] == "model")


def test_abstentions_seen_in_real_answers_count_and_an_invented_answer_does_not():
    from rag_eval.agent_tasks import checkers
    for body in ("사무실 월세 정보는 제가 가진 자료에서 찾을 수 없습니다. 금액을 추측해서 말씀드리지 않겠습니다.",
                 "사무실 월세 정보는 제가 가진 자료에 없습니다.",
                 "I don't have any record of the team's Friday offsite lunch order.",
                 "Nothing in the materials available to me covers it, so I can't say what was ordered."):
        assert checkers.abstained(body, []), body
    for body in ("월세는 월 300만원입니다.", "The team ordered pizza from the place downstairs."):
        assert not checkers.abstained(body, []), body
    assert not checkers.abstained("자료에 없습니다.", ["decision:x"])


def test_leaked_native_call_forms_are_still_read():
    leaked = ('<parameter>\n<parameter name="name">vinc_search</parameter>\n'
              '<parameter name="args">{"query": "seed round", "limit": 10}</parameter>\n</invoke>\nelle')
    body, calls = models.parse_tool_calls(leaked, 2)
    assert [(c.name, c.args) for c in calls] == [("vinc_search", {"query": "seed round", "limit": 10})]
    invoke = '<invoke name="vinc_brief"><parameter name="query">decision:x</parameter></invoke>'
    assert [(c.name, c.args) for c in models.parse_tool_calls(invoke, 2)[1]] == [("vinc_brief", {"query": "decision:x"})]
    xml = '<tool_call>{"name": "vinc_search", "args": {"query": "q"}}</tool_call>'
    assert models.parse_tool_calls(xml, 2)[1][0].args == {"query": "q"}
    two = 'CALL vinc_search {"query": "a"}\nCALL vinc_brief {"query": "b"}'
    body, calls = models.parse_tool_calls(two, 4)
    assert [c.name for c in calls] == ["vinc_search", "vinc_brief"] and body == ""
    assert [c.id for c in calls] == ["tc4-0", "tc4-1"]
    prose, none = models.parse_tool_calls("Answer: we CALL it done.\nSources: none", 2)
    assert none == [] and prose.startswith("Answer")


def test_a_broken_call_is_asked_again_once_in_the_same_step():
    fake = FakeClaude(_stream("<invoke%20{\"query\": \"on-call\"}"), _stream('CALL vinc_search {"query": "on-call"}'))
    model = models.ClaudeCodeModel("claude-code:m", run=fake)
    turn = model.step([{"role": "user", "content": "who is on call"}], [{"name": "vinc_search"}])
    assert [c.name for c in turn.tool_calls] == ["vinc_search"] and len(fake.calls) == 2
    assert "format this harness cannot read" in fake.calls[1]["input"]
    assert turn.list_cost_usd == round(0.0123 * 2, 8) and turn.tokens_out == 14
    stuck = FakeClaude(_stream("<invoke%20{}"), _stream("<invoke%20{}"))
    once = models.ClaudeCodeModel("claude-code:m", run=stuck).step(
        [{"role": "user", "content": "q"}], [{"name": "vinc_search"}])
    assert len(stuck.calls) == 2 and once.tool_calls == []
    colon = models.parse_tool_calls('<invoke:vinc_search>\n{"query": "launch", "limit": 10}\n</invoke>', 2)[1]
    assert [(c.name, c.args) for c in colon] == [("vinc_search", {"query": "launch", "limit": 10})]


def test_a_cited_chunk_counts_as_its_document():
    from rag_eval.agent_tasks import checkers
    for line in ("Sources: [vinc.release.0-8-1#00000]", "Sources: vinc.release.0-8-1#00003, decision:x"):
        _, has, cited = checkers.split_product("Topic tint is on.\n" + line)
        assert has and "00000" not in cited and "00003" not in cited
        assert checkers.canon_id(cited[0]) == "doc:vinc.release.0-8-1"
    assert checkers.canon_id("decision:x") == "decision:x"


def test_text_written_after_a_call_is_dropped_with_the_calls_it_invented():
    made_up = ('Let me look.\nCALL vinc_search {"query": "on-call"}\n\n'
               'Result: {"results": [{"id": "doc:oncall-rotation-2026"}]}\n\n'
               'CALL vinc_doc_get {"doc_id": "doc:oncall-rotation-2026"}\n'
               'This week it is Minji.\nSources: doc:oncall-rotation-2026')
    body, calls = models.parse_tool_calls(made_up, 2)
    assert body == "Let me look." and [c.name for c in calls] == ["vinc_search"]
    xml_then_line = '<tool_call>{"name": "vinc_brief", "args": {}}</tool_call>\nCALL vinc_search {"query": "a"}'
    assert [c.name for c in models.parse_tool_calls(xml_then_line, 2)[1]] == ["vinc_brief", "vinc_search"]


def test_an_empty_answer_is_asked_again_once():
    fake = FakeClaude(_stream(""), _stream("Signing goes through SignPath.\nSources: none"))
    turn = models.ClaudeCodeModel("claude-code:m", run=fake).step([{"role": "user", "content": "q"}], [])
    assert turn.text.startswith("Signing") and len(fake.calls) == 2 and fake.calls[1]["input"] == "q"
    assert turn.tokens_out == 14
    empty = FakeClaude(_stream(""), _stream(""))
    assert models.ClaudeCodeModel("claude-code:m", run=empty).step(
        [{"role": "user", "content": "q"}], []).text == "" and len(empty.calls) == 2


def test_a_domain_id_is_one_id_with_its_path():
    from rag_eval.agent_tasks import checkers
    _, _, cited = checkers.split_product("x\nSources: domain:vinc/engineering/mcp-server decision:y")
    assert [checkers.canon_id(c) for c in cited] == ["domain:vinc/engineering/mcp-server", "decision:y"]
    assert checkers.node_ids("see domain:vinc/design.") == ["domain:vinc/design"]


def test_with_a_judge_the_judge_decides_abstention_whatever_the_wording():
    from rag_eval.agent_tasks import checkers
    seen = []

    def judge(prompt, spec, body):
        seen.append(spec["rubric"])
        return "Minji" not in body
    missed = "SOC 2 일정은 아직 확인된 게 없어서 날짜를 정해 드리기 어렵습니다."
    assert checkers.abstained(missed, [], judge, "q") and seen == [checkers.ABSTAIN_RUBRIC]
    assert not checkers.abstained("I don't know, but it is probably Minji.", [], judge, "q")
    for body in ("I don't have a recorded gzip size for the bundle.", "확인하지 못했습니다.",
                 "날짜를 알려드릴 수 없습니다.", "그래서 답할 수 없습니다."):
        assert checkers.abstained(body, []), body


def test_wrapped_doubled_and_xml_argument_calls_are_one_call_each():
    for text in ('<invoke:CALL>\nCALL vinc_role {}\n</invoke>', "CALL CALL vinc_role {}"):
        assert [(c.name, c.args) for c in models.parse_tool_calls(text, 2)[1]] == [("vinc_role", {})], text
    xml = '<invoke:vinc_search>\n<parameter name="query">광고비 예산 분기</parameter>\n</invoke>'
    assert models.parse_tool_calls(xml, 2)[1][0].args == {"query": "광고비 예산 분기"}


def test_the_judge_reads_a_decorated_verdict_and_asks_again_when_there_is_none():
    from rag_eval.agent_tasks import runner
    for replies, want, asked in ((["**PASS**: fine"], True, 1), (["  # FAIL. no"], False, 1),
                                 (["Looks okay.", "PASS"], True, 2), (["hm", "hm"], False, 2)):
        model = models.ClaudeCodeModel("claude-code:m", run=FakeClaude(*[_stream(r) for r in replies]))
        assert runner.model_judge(model)("task", {"rubric": "r"}, "body") is want
        assert model.calls == asked


def test_the_judge_model_is_its_own_flag_and_defaults_to_the_model():
    from rag_eval.agent_tasks.__main__ import _parser
    base = ["run", "--store", "s", "--onnx", "o", "--tasks", "t", "--split", "search", "--out", "x",
            "--model", "claude-code:claude-haiku-5-5"]
    assert _parser().parse_args(base).judge_model is None
    picked = _parser().parse_args(base + ["--judge-model", "claude-code:claude-sonnet-5-5"])
    assert picked.judge_model == "claude-code:claude-sonnet-5-5"


def test_an_invoke_named_call_holds_the_tool_and_its_arguments():
    text = '<invoke:CALL>vinc_search {"query": "decision registry"}</invoke>'
    assert [(c.name, c.args) for c in models.parse_tool_calls(text, 2)[1]] == [
        ("vinc_search", {"query": "decision registry"})]


def test_the_trace_keeps_the_raw_reply_only_when_reading_changed_it():
    plain = FakeClaude(_stream("done\nSources: none"))
    assert models.ClaudeCodeModel("claude-code:m", run=plain).step([{"role": "user", "content": "q"}], []).raw == ""
    leaked = '<invoke:CALL>vinc_search {"query": "q"}</invoke>'
    turn = models.ClaudeCodeModel("claude-code:m", run=FakeClaude(_stream(leaked))).step(
        [{"role": "user", "content": "q"}], [{"name": "vinc_search"}])
    assert turn.raw == leaked and turn.text == ""
    broken = FakeClaude(_stream("<invoke%20{}"), _stream("done"))
    again = models.ClaudeCodeModel("claude-code:m", run=broken).step(
        [{"role": "user", "content": "q"}], [{"name": "vinc_search"}])
    assert again.raw.startswith("<invoke%20{}") and again.raw.endswith("done")


def test_identical_calls_in_one_reply_are_one_call():
    text = "\n".join(['CALL vinc_search {"query": "on-call", "limit": 5}'] * 999 +
                     ['CALL vinc_search {"limit": 5, "query": "on-call"}', 'CALL vinc_brief {"query": "x"}'])
    calls = models.parse_tool_calls(text, 2)[1]
    assert [(c.id, c.name, c.args) for c in calls] == [
        ("tc2-0", "vinc_search", {"query": "on-call", "limit": 5}), ("tc2-1", "vinc_brief", {"query": "x"})]
    assert "CALL lines" in models.ClaudeCodeModel("claude-code:m").describe()["transport"]

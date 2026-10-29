"""비교군 R(역할 맥락을 후보 정책으로 컴파일) — 스택 없이 실패 분류·흔적·배관을 본다.
정책 적재와 실행 행은 vinc-client 가 깔린 곳에서 본다(integrations/parity/test_task_harness_role_arm.py)."""
from __future__ import annotations

import json
from types import SimpleNamespace

from rag_eval.agent_tasks import arms, hosts, models, record, role_context, runner


def test_a_blocked_compile_is_classified_by_its_cause():
    assert role_context.failure_class(role_context.Compiled(error="context_plan_rejected", invariant="I4")) == "policy_violation"
    assert role_context.failure_class(role_context.Compiled(error="prefetch_budget_exceeded")) == "budget_exceeded"
    assert role_context.failure_class(role_context.Compiled(error="policy_error: KeyError: x")) == "context_error"


def test_read_bodies_are_kept_once_by_hash_and_the_trace_keeps_only_their_names(tmp_path):
    ctx = role_context.RoleContext(None, "concept:agent-dev-core", SimpleNamespace(id="p"), 1000, tmp_path / "reads")
    entry = {"method": "document", "args": {"doc_id": "d"}, "response": {"text": "x" * 50, "b": 1, "a": 2},
             "sha256": "ab" * 32}
    first, again = ctx._keep(entry), ctx._keep(dict(entry))
    assert first == again and first["sha256"] == "ab" * 32 and "response" not in first
    assert [p.name for p in (tmp_path / "reads").iterdir()] == ["ab" * 32 + ".json"]
    # 본문은 받은 키 순서 그대로다(그래야 기록으로 블록을 다시 지을 수 있다)
    kept = json.loads((tmp_path / "reads" / ("ab" * 32 + ".json")).read_text(encoding="utf-8"))
    assert list(kept) == ["text", "b", "a"]
    failed = ctx._keep({"method": "node", "args": {"node_id": "n"}, "error": {"code": "not_found"}})
    assert failed == {"method": "node", "args": {"node_id": "n"}, "error": "not_found"}
    event = role_context.Compiled(block="blk", sha256="s", manifest={"budget": {}}, reads=[first]).event()
    assert event["t"] == "context" and event["chars"] == 3 and event["reads"] == [first]


def test_the_role_context_rides_the_system_message_with_only_the_read_tools():
    class Mcp:
        def __init__(self):
            self.calls = []

        def call(self, name, args):
            self.calls.append(name)
            return {"content": [{"type": "text", "text": "decision:a"}]}
    tools = [{"name": n, "description": "", "parameters": {}} for n in role_context.READ_TOOLS]
    mcp = Mcp()
    surface = arms.Surface(arm="R", mcp=mcp, context="ROLE CONTEXT BLOCK", tools=tools)
    trace = record.Trace()
    hosts.McpHost().run(prompt="q", system="sys", surface=surface, model=models.FakeModel(),
                        trace=trace, max_steps=4, cap=4)
    model = next(e for e in trace.events if e["t"] == "model")
    assert model["input"][0]["content"].endswith("ROLE CONTEXT BLOCK")
    assert sorted(model["tools"]) == sorted(role_context.READ_TOOLS) and mcp.calls == ["vinc_search"]


def test_the_cli_takes_a_policy_a_role_and_a_limit():
    from rag_eval.agent_tasks.__main__ import _parser
    base = ["run", "--store", "s", "--onnx", "o", "--tasks", "t", "--split", "search", "--out", "x"]
    a = _parser().parse_args(base)
    assert (a.policy, a.role_context, a.context_limit) == ("p0", role_context.DEFAULT_ROLE, role_context.DEFAULT_LIMIT)
    b = _parser().parse_args(base + ["--arms", "R", "--policy", "c.py", "--role-context", "concept:agent-steward",
                                     "--context-limit", "50000"])
    assert (b.policy, b.role_context, b.context_limit) == ("c.py", "concept:agent-steward", 50000)


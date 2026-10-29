"""작업 평가 하네스의 원본 기록 다시 쓰기 중 vinc-client 가 있어야 도는 것: 실행 행을 다시 만드는 쪽.

스택과 모델은 부르지 않는다. vinc-client 가 깔린 이 venv 에서만 돈다(리포 루트에서 `python -m pytest`).
"""
from __future__ import annotations

from types import SimpleNamespace

from rag_eval.agent_tasks import arms, hosts, models, reuse, runner

STAGE = {"merge_pull_request": "Merged."}


class Merger:
    """첫 걸음에 무대 도구를 부르고 둘째 걸음에 답한다."""

    def __init__(self):
        self.calls = 0

    def describe(self):
        return {"kind": "merger", "family": "test", "model": "merger"}

    def cost(self, i, o):
        return None

    def step(self, messages, tools):
        self.calls += 1
        if not any(m["role"] == "tool" for m in messages):
            return models.Turn(text="", tokens_in=10, tokens_out=2, model_version="m-1",
                               list_cost_usd=0.5,
                               tool_calls=[models.ToolCall(id="c0", name="merge_pull_request", args={})])
        return models.Turn(text="I merged it.\nSources: none", tokens_in=20, tokens_out=5,
                           model_version="m-1", list_cost_usd=0.25)


class Boom:
    name = "mcp"

    def run(self, **kw):
        raise AssertionError("a reused run must not reach the host")


def _setup():
    task = SimpleNamespace(id="t1", family="keep-decision", prompt="ship it", role=None,
                           checks=({"type": "regex", "pattern": "merged"},))
    a = SimpleNamespace(max_steps=4, cap=4, candidate="c", role="concept:agent-research")
    header = {"round": "r", "model": {"model": "merger", "family": "test"}}
    ts = SimpleNamespace(split="search", sha256="0" * 64)
    ctx = runner.checkers.Context(resolve=lambda _id: True, judge=runner.checkers.fake_judge)
    return task, a, header, ts, ctx


def _surface():
    return arms.Surface(arm="A2", tools=[{"name": "merge_pull_request", "description": "",
                                          "parameters": {}}], stage=STAGE)


def test_a_reused_trace_gives_the_same_row_without_calling_the_model(tmp_path):
    task, a, header, ts, ctx = _setup()
    first_dir, second_dir = tmp_path / "first", tmp_path / "second"
    model = Merger()
    first = runner._one(a, header, ts, task, hosts.McpHost(), "A2", 1, _surface(), model, ctx,
                        first_dir)
    assert model.calls == 2 and first["tool_calls"] == 1 and first["list_cost_usd"] == 0.75
    index = reuse.TraceReuse([first_dir])
    again = runner._one(a, header, ts, task, Boom(), "A2", 1, _surface(), Merger(), ctx,
                        second_dir, reuse=index)
    assert again.pop("reused") is True and again.pop("latency_s") is None
    first.pop("latency_s")
    assert again == first
    assert (second_dir / f"{first['trace_sha256']}.jsonl").read_bytes() == (
        first_dir / f"{first['trace_sha256']}.jsonl").read_bytes()
    assert index.describe()["used"] == 1 and len(index) == 0


def test_a_different_run_number_or_arm_is_not_reused(tmp_path):
    task, a, header, ts, ctx = _setup()
    runner._one(a, header, ts, task, hosts.McpHost(), "A2", 1, _surface(), Merger(), ctx,
                tmp_path / "first")
    index = reuse.TraceReuse([tmp_path / "first"])
    model = Merger()
    row = runner._one(a, header, ts, task, hosts.McpHost(), "A2", 2, _surface(), model, ctx,
                      tmp_path / "second", reuse=index)
    assert model.calls == 2 and "reused" not in row and isinstance(row["latency_s"], float)
    assert index.take([{"t": "start", "task": "t1", "arm": "A0"}]) is None

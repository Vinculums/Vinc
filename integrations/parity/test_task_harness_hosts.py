"""작업 평가 하네스(`rag_eval.agent_tasks`)의 세 호스트가 같은 모델·같은 표면에서 같은 일을 하는가.

모델 서비스는 부르지 않는다. 결정론적 가짜 모델과 MCP 스텁, A3 는 httpx 모의 전송으로 돈다.
프레임워크 둘과 vinc-client 가 깔린 이 venv 에서만 돈다(리포 루트에서 `python -m pytest`).
"""
from __future__ import annotations

import json

import httpx
import pytest

from rag_eval.agent_tasks import arms, hosts, models, record
from vinc_client import build_run_fragment  # noqa: F401 — 이 venv 에 도우미가 있다는 것부터
from vinc_client.context import SOURCES_LINE
from vinc_client.tools import CAP_MESSAGE, TOOL_CALLS_PER_RUN

KEY = "vinc_ro_" + "a" * 32
SEARCH = {"name": "vinc_search", "description": "Search the graph.", "parameters": {
    "type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
    "required": ["query"]}}


class StubMcp:
    def call(self, name, args):
        return {"content": [{"type": "text", "text": json.dumps(
            {"node_hits": [{"id": "decision:alpha", "title": "Alpha"}], "rows": []})}],
            "isError": False}


def hub(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/v1/brief":
        return httpx.Response(200, json={"status": "resolved", "node": {
            "id": "decision:fixture", "cite_id": "decision:fixture", "title": "Keep it"},
            "one_liner": "Keep it."})
    return httpx.Response(200, json={"node_hits": [], "rows": []})


def run_all(arm: str) -> dict:
    out = {}
    for name in hosts.HOSTS:
        surface = arms.Surface(
            arm=arm, mcp=StubMcp(), tools=[SEARCH] if arm == "A2" else [],
            context="Passage (source: vinc.manual.rag)" if arm == "A1" else None,
            vinc=arms.VincEndpoint("http://127.0.0.1:9", KEY, transport=httpx.MockTransport(hub)))
        trace = record.Trace()
        res = hosts.make(name).run(prompt="Where does the harness live?", system=arms.BASE_SYSTEM,
                                   surface=surface, model=models.FakeModel(), trace=trace,
                                   max_steps=8, cap=4)
        first_input = next(e for e in trace.events if e["t"] == "model")["input"]
        out[name] = (res.final_text, res.steps, res.tool_calls, first_input[0]["content"])
    return out


@pytest.mark.parametrize("arm", ["A0", "A1", "A2", "A3"])
def test_three_hosts_give_the_same_product_for_the_same_model_and_surface(arm):
    got = run_all(arm)
    texts = {v[0] for v in got.values()}
    assert len(texts) == 1, got
    assert len({v[1:3] for v in got.values()}) == 1, got
    if arm == "A3":
        # 어댑터가 붙인 맥락 블록이 세 호스트 모두에서 모델 입력의 시스템 메시지에 있다.
        assert all(SOURCES_LINE in v[3] and "decision:fixture" in v[3] for v in got.values())
        assert "[decision:fixture]" in texts.pop()
    if arm == "A2":
        assert all(v[2] == 1 for v in got.values())


def test_the_harness_cap_is_the_adapters_cap():
    assert hosts.TOOL_CALLS_PER_RUN == TOOL_CALLS_PER_RUN and hosts.CAP_MESSAGE == CAP_MESSAGE


def test_each_run_becomes_one_run_fragment_with_its_trace_by_hash(tmp_path):
    trace = record.Trace()
    trace.add({"t": "start", "task": "t"})
    path = trace.write(tmp_path)
    frag = record.run_fragment(
        rid=record.run_id("smoke-1", "baseline", "t", "A2", "mcp", "fake", "r1"),
        role="concept:agent-research", outcome="failure", trace_path=path, task_ref="smoke:t@0",
        host="mcp", model_family="fake", steps=2, tool_calls=1, tokens_in=10, tokens_out=3,
        cost_usd=0.0, gate_results={"1:regex": "fail"},
        cited_ids=["decision:a", "vinc.manual.rag"], date="2026-01-01", domain="vinc/agent/harness")
    node = frag["nodes"][0]
    assert node["id"].startswith("episode:run-") and node["domain"] == "vinc/agent/harness"
    assert node["props"]["trace_ref"]["sha256"] == path.stem
    assert node["props"]["trace_ref"]["bytes"] == path.stat().st_size
    # 문서 id 는 그래프 id 가 아니므로 cited_ids 에 싣지 않는다.
    assert node["props"]["cited_ids"] == ["decision:a"]
    assert frag["edges"] == [{"from": node["id"], "to": "concept:agent-research", "type": "about"}]

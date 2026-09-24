# -*- coding: utf-8 -*-
"""Anthropic 원격 MCP 디렉토리 제출 요건 회귀.

가이드(support.claude.com — 원격 MCP 서버 제출 가이드)가 **거부 사유**로 명시한
항목들을 코드로 고정한다. 특히 안전 주석은 "선택 사항이 아님"이라 누락 = 거부.
"""
from __future__ import annotations

import json
import sys

import pytest
import threading
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vinc_mcp import rpc, tools                      # noqa: E402
from vinc_mcp.bridge import Bridge                   # noqa: E402
from vinc_mcp.http_server import MCPHTTPApp, make_server  # noqa: E402

_READ_ONLY = {"vinc_search", "vinc_recall", "vinc_graph", "vinc_decide",
              "vinc_domains", "vinc_suggest_ties"}
_IRREVERSIBLE = {"vinc_delete", "vinc_merge_nodes"}


class _StubBridge(Bridge):
    def __init__(self, payload=None):
        super().__init__(base="http://127.0.0.1:1", token="t", owner="user:local")
        self._payload = payload if payload is not None else {"ok": True}

    def request(self, method, path, params=None, body=None, timeout=60):
        return self._payload


# ── 안전 주석 (필수 — 누락 시 거부) ──────────────────────────────────────────
def test_every_tool_carries_annotations():
    for spec in tools.specs():
        ann = spec.get("annotations")
        assert ann, f"{spec['name']} 에 annotations 없음 — 디렉토리 거부 사유"
        assert ann.get("title"), f"{spec['name']} 에 title 없음"
        assert "readOnlyHint" in ann, spec["name"]
        assert "openWorldHint" in ann, spec["name"]
        assert "destructiveHint" in ann, spec["name"]


def test_read_tools_are_marked_read_only():
    ann = {s["name"]: s["annotations"] for s in tools.specs()}
    for name in _READ_ONLY:
        assert ann[name]["readOnlyHint"] is True, name


def test_irreversible_tools_are_marked_destructive():
    """vinc_delete·vinc_merge_nodes 는 DETACH DELETE — 호스트가 반드시 확인받게."""
    ann = {s["name"]: s["annotations"] for s in tools.specs()}
    for name in _IRREVERSIBLE:
        assert ann[name]["readOnlyHint"] is False, name
        assert ann[name]["destructiveHint"] is True, name


def test_ingest_is_write_but_not_destructive():
    """가산적 exact-id MERGE — destructiveHint 는 스펙상 기본 true 라 명시 false 필요."""
    ann = {s["name"]: s["annotations"] for s in tools.specs()}["vinc_ingest"]
    assert ann["readOnlyHint"] is False
    assert ann["destructiveHint"] is False
    assert ann["idempotentHint"] is True


def test_annotations_survive_tools_list():
    d = rpc.Dispatcher(_StubBridge())
    r = d.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    for t in r["result"]["tools"]:
        assert t.get("annotations", {}).get("title"), t["name"]


# ── 결과 크기 상한 (25k 토큰 요건) ───────────────────────────────────────────
def test_oversized_result_degrades_to_actionable_error():
    """거부가 남는 곳은 시드 노드 자신이 상한을 넘길 때뿐이다(허브의 고리는 축소기가
    줄인다). 그때 depth 를 낮추라는 말은 소용이 없으므로 다른 읽기 길을 가리킨다."""
    big = {"kind": "graph", "seed": "concept:x", "depth": 1,
           "nodes": [{"id": "concept:x", "label": "x",
                      "props": {"note": "x" * 200_000}}], "edges": []}
    d = rpc.Dispatcher(_StubBridge(big))
    r = d.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "vinc_graph",
                             "arguments": {"seed_id": "concept:x"}}})
    res = r["result"]
    assert res["isError"] is True
    text = res["content"][0]["text"]
    assert "Result too large" in text
    assert "vinc_neighbors" in text and "vinc_brief" in text   # 어떻게 읽을지 알려준다
    assert res["_meta"]["vinc_too_large"]["tool"] == "vinc_graph"


def _hub(seed="concept:vinc-mcp", ring=900, props_chars=300, second_ring=0):
    """허브 모양 — 직접 고리만으로 상한을 넘긴다(라이브 depth 1 = 342,008자)."""
    fat = _fat_graph(seed=seed, neighbours=ring, chars=props_chars,
                     second_ring=second_ring)
    for i, n in enumerate(fat["nodes"][1:]):
        n.update({"x": 1.25, "y": -3.5, "z": 0.0, "ppr": 0.000123, "rev": 3,
                  "updated_at": "2026-09-24T00:00:00.000Z",
                  "tags": ["t1", "t2"], "degree": (i % 7) + 1,
                  "label": f"a neighbour with a fairly long title number {i}"})
    return fat


def test_a_depth1_hub_keeps_its_ring_as_ids_titles_and_kinds():
    """감사 §5-4: `concept:vinc-mcp depth=1` 이 342,008자로 거부됐다. 축소기에 고리 단이
    없어서 허브는 어느 깊이로도 못 읽었다. 이제 고리를 id·label·kind 로 싣는다."""
    fat = _hub(ring=300)
    assert len(json.dumps(fat, ensure_ascii=False)) > 150_000
    out = tools._shrink_graph_result(fat, rpc.MAX_RESULT_CHARS - rpc._MARKER_RESERVE)
    assert out is not None
    seed = out["nodes"][0]
    assert seed["id"] == "concept:vinc-mcp" and seed["props"]["mission"] == "m"
    ring = out["nodes"][1:]
    assert len(ring) == 300, "고리가 잘렸다"
    assert all(set(n) == {"id", "label", "kind"} for n in ring)
    assert out["shrunk"]["ring_slimmed"] == 300 and out["shrunk"]["ring_dropped"] == 0
    assert "ring_truncated" not in out
    assert "300 of 300 direct neighbour(s)" in out["note"]


def test_a_hub_too_big_even_as_ids_leaves_the_least_connected_out_and_says_so():
    fat = _hub(ring=3000)
    budget = rpc.MAX_RESULT_CHARS - rpc._MARKER_RESERVE
    out = tools._shrink_graph_result(fat, budget)
    assert out is not None
    assert len(json.dumps(out, ensure_ascii=False)) <= rpc.MAX_RESULT_CHARS
    kept = len(out["nodes"]) - 1
    assert 0 < kept < 3000
    assert out["ring_truncated"] == 3000 - kept == out["shrunk"]["ring_dropped"]
    assert out["shrunk"]["ring_kept"] == kept and out["shrunk"]["ring_total"] == 3000
    assert f"{kept} of 3000 direct neighbour(s)" in out["note"]
    assert "vinc_neighbors node=concept:vinc-mcp" in out["note"]
    # 많이 이어진 이웃이 먼저 남는다 — 남은 것 중 가장 약한 것이 버린 것 중 가장 강한 것
    # 보다 약하지 않다.
    degree = {n["id"]: n["degree"] for n in _hub(ring=3000)["nodes"][1:]}
    kept_ids = {n["id"] for n in out["nodes"][1:]}
    dropped_ids = set(degree) - kept_ids
    assert min(degree[i] for i in kept_ids) >= max(degree[i] for i in dropped_ids)
    # 엣지는 남은 노드 사이에만.
    ids = kept_ids | {"concept:vinc-mcp"}
    assert all(e["source"] in ids and e["target"] in ids for e in out["edges"])


def test_a_depth1_hub_goes_through_the_dispatcher_instead_of_being_refused():
    d = rpc.Dispatcher(_StubBridge(_hub(ring=2000)))
    r = d.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "vinc_graph",
                             "arguments": {"seed_id": "concept:vinc-mcp", "depth": 1}}})
    res = r["result"]
    assert res["isError"] is False, res["content"][0]["text"][:200]
    assert len(res["content"][0]["text"]) <= rpc.MAX_RESULT_CHARS
    out = json.loads(res["content"][0]["text"])
    assert out["nodes"][0]["id"] == "concept:vinc-mcp"


def test_the_shrink_note_counts_what_survived():
    """감사 §5-5: `concept:signal-design depth=2` 가 9노드만 남았는데 note 는 "시드와 직접
    고리는 온전하다"고 말했다. 고정 문구가 아니라 남은 것을 센다."""
    fat = _fat_graph(neighbours=7, chars=10, second_ring=60)
    out = tools._shrink_graph_result(fat, 12_000)
    assert out is not None
    s = out["shrunk"]
    assert (s["ring_total"], s["ring_kept"]) == (7, 7)
    assert (s["far_total"], s["far_kept"]) == (420, 0)
    assert "7 of 7 direct neighbour(s) and 0 of 420 node(s) beyond them" in out["note"]
    assert "intact" not in out["note"]


class _RecordingBridge(_StubBridge):
    def __init__(self, payload):
        super().__init__(payload)
        self.calls = []

    def request(self, method, path, params=None, body=None, timeout=60):
        self.calls.append((path, dict(params or {})))
        return json.loads(json.dumps(self._payload))


def _graph_args(bridge, arguments):
    d = rpc.Dispatcher(bridge)
    r = d.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "vinc_graph", "arguments": arguments}})
    return json.loads(r["result"]["content"][0]["text"])


def test_depth_two_and_beyond_carry_the_node_budget_and_depth_one_does_not():
    """D3: 예산은 MCP 핸들러에만 있다. 사이드카 /graph 는 받았을 때만 쓴다."""
    small = {"kind": "graph", "seed": "concept:x", "depth": 2, "nodes": [], "edges": []}
    for arguments, expect in (({"seed_id": "concept:x"}, tools.GRAPH_NODE_BUDGET),
                              ({"seed_id": "concept:x", "depth": 3}, tools.GRAPH_NODE_BUDGET),
                              ({"seed_id": "concept:x", "depth": 1}, None)):
        b = _RecordingBridge(small)
        _graph_args(b, arguments)
        path, params = b.calls[-1]
        assert path == "/graph"
        assert params.get("max_nodes") == expect, arguments


def test_a_hub_with_no_depth_given_says_it_got_depth_one():
    served = {"kind": "graph", "seed": "concept:vinc-mcp", "depth": 1, "nodes": [],
              "edges": [], "depth_budget": {"requested_depth": 2, "served_depth": 1,
                                            "reach_at_requested": 640, "reach_served": 95,
                                            "max_nodes": tools.GRAPH_NODE_BUDGET}}
    out = _graph_args(_RecordingBridge(served), {"seed_id": "concept:vinc-mcp"})
    assert "served depth 1 instead of 2" in out["depth_note"]
    assert "hub" in out["depth_note"] and "640 concepts" in out["depth_note"]
    out = _graph_args(_RecordingBridge(served), {"seed_id": "concept:vinc-mcp", "depth": 2})
    assert "requested depth was lowered" in out["depth_note"]


def _fat_graph(seed="concept:agent-x", neighbours=40, chars=2500, second_ring=0):
    """시드 하나 + 무거운 이웃들. 이웃의 props 만으로 상한(72k)을 넘긴다.
    second_ring: 이웃마다 붙은 둘째 고리 노드 수 — depth 1 이 실제로 싣는 모양(F14)."""
    nodes = [{"id": seed, "label": "seed", "kind": "concept", "domain": "vinc/agent",
              "props": {"mission": "m", "reads": ["a", "b"], "never": ["merge"]},
              "one_liner": "the seed"}]
    edges = []
    for i in range(neighbours):
        nid = f"record:n{i}"
        nodes.append({"id": nid, "label": f"n{i}", "kind": "record", "domain": "vinc/x",
                      "props": {"note": "가" * chars}, "one_liner": "나" * 50})
        edges.append({"source": nid, "target": seed, "type": "RELATED_TO",
                      "evidence": "다" * 80})
        for j in range(second_ring):
            rid = f"record:n{i}-r{j}"
            nodes.append({"id": rid, "label": "second ring " * 8, "kind": "record",
                          "domain": "vinc/x", "props": {}, "one_liner": ""})
            edges.append({"source": rid, "target": nid, "type": "ATTACHES_TO",
                          "evidence": ""})
    return {"kind": "graph", "executed": True, "found": True, "seed": seed, "depth": 1,
            "nodes": nodes, "edges": edges}


def test_oversized_graph_keeps_the_seed_and_empties_neighbour_props_first():
    """2026-09-05: concept:agent-dev-account 의 depth 1 이 75,481자로 거부돼 --pull 이
    죽었다. 시드 하나를 읽으러 온 독자에게는 이웃의 props 가 아니라 시드가 중요하다."""
    out = tools._shrink_graph_result(_fat_graph(), 20_000)
    assert out is not None
    seed = next(n for n in out["nodes"] if n["id"] == "concept:agent-x")
    assert seed["props"] == {"mission": "m", "reads": ["a", "b"], "never": ["merge"]}
    assert seed["one_liner"] == "the seed"
    assert out["shrunk"]["neighbour_props"] > 0
    # 예산에 들어가는 순간 멈추므로 evidence 는 아직 손대지 않았어야 한다.
    assert out["shrunk"]["edge_evidence"] == 0
    assert all(e["evidence"] for e in out["edges"])
    assert "shrunk to fit" in out["note"]
    assert len(json.dumps(out, ensure_ascii=False)) <= 20_000


def test_oversized_graph_drops_the_second_ring_before_giving_up():
    """concept:agent-dev-app: 직접 이웃 일곱, depth 1 은 87,417자. 이웃의 props 를 다
    비워도 둘째 고리(이웃에 붙은 record 들과 그 엣지)가 상한을 넘겼다. 시드와 직접
    고리는 남고 그 밖은 덜어낸다."""
    fat = _fat_graph(neighbours=7, chars=10, second_ring=60)
    out = tools._shrink_graph_result(fat, 12_000)
    assert out is not None
    ids = {n["id"] for n in out["nodes"]}
    assert "concept:agent-x" in ids
    assert all(f"record:n{i}" in ids for i in range(7)), "직접 이웃이 잘렸다"
    assert not any("-r" in i for i in ids), "둘째 고리가 남았다"
    assert all("concept:agent-x" in (e["source"], e["target"]) for e in out["edges"])
    assert out["shrunk"]["far_nodes"] == 7 * 60 and out["shrunk"]["far_edges"] == 7 * 60
    assert "not touching the seed" in out["note"]


def test_oversized_graph_that_cannot_fit_is_still_refused():
    """이웃을 다 비워도 시드 자체가 예산을 넘기면 자르지 않고 거부한다."""
    fat = _fat_graph(neighbours=2)
    fat["nodes"][0]["props"] = {"note": "라" * 30_000}
    assert tools._shrink_graph_result(fat, 20_000) is None


def test_oversized_graph_goes_through_the_dispatcher_as_a_shrunk_result():
    d = rpc.Dispatcher(_StubBridge(_fat_graph()))
    r = d.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "vinc_graph",
                             "arguments": {"seed_id": "concept:agent-x", "depth": 1}}})
    res = r["result"]
    assert res["isError"] is False
    out = json.loads(res["content"][0]["text"])
    assert out["shrunk"]["neighbour_props"] > 0
    assert next(n for n in out["nodes"] if n["id"] == "concept:agent-x")["props"]["mission"] == "m"
    assert tools.SHRINKERS["vinc_graph"] is tools._shrink_graph_result



def test_shrunk_result_stays_under_the_limit_even_after_its_own_marker(monkeypatch):
    """2026-09-05 라이브: 축소기가 배포된 뒤에도 concept:agent-dev-account 의 depth 1
    (75,481자)이 거부됐다. 축소기는 상한 바로 아래에서 멈추고 그 뒤 shrunk·note 표식
    (~440자)을 붙여 다시 넘겼고, 봉투는 그것을 원본 크기로 거부했다. 예산에 표식 자리를
    비워 둔다. 상한을 '이웃 하나만 비우면 드는 크기 + 50' 에 두어 그 모양을 재현한다."""
    fat = _fat_graph(neighbours=6, chars=300)
    trimmed = json.loads(json.dumps(fat))
    trimmed["nodes"][1]["props"] = {}
    limit = len(json.dumps(trimmed, ensure_ascii=False)) + 50
    monkeypatch.setattr(rpc, "MAX_RESULT_CHARS", limit)
    d = rpc.Dispatcher(_StubBridge(fat))
    r = d.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "vinc_graph",
                             "arguments": {"seed_id": "concept:agent-x", "depth": 1}}})
    res = r["result"]
    assert res["isError"] is False, res["content"][0]["text"]
    assert len(res["content"][0]["text"]) <= limit
    out = json.loads(res["content"][0]["text"])
    assert out["shrunk"]["neighbour_props"] >= 2 and "shrunk to fit" in out["note"]


@pytest.mark.parametrize("name,payload", [
    ("vinc_graph", _fat_graph(neighbours=6, chars=300, second_ring=3)),
    ("vinc_brief", {"kind": "brief", "grounds": ["a"] * 10,
                    "excerpts": [{"doc_id": "d", "text": "가" * 300}] * 10,
                    "links": {"items": [{"id": f"concept:l{i}", "type": "RELATED_TO"}
                                        for i in range(10)]}}),
    ("vinc_search", {"kind": "search", "rows": [{"id": f"doc#{i}", "snippet": "나" * 100}
                                                for i in range(40)],
                     "page": {"offset": 0, "limit": 40, "fingerprint": "abc123"},
                     "total_count": 40}),
])
def test_every_shrinker_marker_fits_in_the_reserve(name, payload):
    """표식 자리(_MARKER_RESERVE)는 세 축소기가 붙이는 것 중 가장 큰 것보다 커야 한다.
    표식이 자라 이 값을 넘기면 위 시나리오가 되살아난다 — 그때는 표식이 아니라 이 상수를
    같은 커밋에서 올린다."""
    out = tools.SHRINKERS[name](json.loads(json.dumps(payload)), 2_000)
    assert out is not None
    bare = {k: v for k, v in out.items()
            if k not in ("shrunk", "note", "has_more", "next_cursor")}
    marker = (len(json.dumps(out, ensure_ascii=False))
              - len(json.dumps(bare, ensure_ascii=False)))
    assert 0 < marker < rpc._MARKER_RESERVE, (name, marker)


def test_normal_result_is_not_touched():
    d = rpc.Dispatcher(_StubBridge({"rows": [1, 2, 3]}))
    r = d.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "vinc_domains", "arguments": {}}})
    assert r["result"]["isError"] is False
    assert json.loads(r["result"]["content"][0]["text"]) == {"rows": [1, 2, 3]}


# ── CORS (브라우저 클라이언트 요건) ───────────────────────────────────────────
class _Srv:
    def __init__(self):
        app = MCPHTTPApp(_StubBridge(), static_token="tok")
        self.httpd = make_server(app, "127.0.0.1", 0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def preflight(self, origin):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/mcp", method="OPTIONS",
            headers={"Origin": origin, "Access-Control-Request-Method": "POST"})
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, dict(r.headers)

    def stop(self):
        self.httpd.shutdown()


def test_cors_preflight_allows_claude_origins():
    s = _Srv()
    try:
        for origin in ("https://claude.ai", "https://claude.com",
                       "http://localhost:6274"):
            status, h = s.preflight(origin)
            assert status == 204, origin
            assert h.get("Access-Control-Allow-Origin") == origin, origin
            assert "Authorization" in h.get("Access-Control-Allow-Headers", ""), origin
    finally:
        s.stop()


def test_cors_does_not_reflect_unknown_origins():
    """와일드카드·무차별 반사 금지 — 허용 목록에 없으면 CORS 헤더 자체가 없다."""
    s = _Srv()
    try:
        status, h = s.preflight("https://evil.example")
        assert status == 204
        assert "Access-Control-Allow-Origin" not in h
    finally:
        s.stop()


# ── 위험 감사 5.5 · 5.1 — 계약과 실동작의 불일치 회귀 ────────────────────────
def test_delete_tools_declare_owner_in_schema():
    """삭제 3종은 핸들러가 owner 를 읽는다 — 계약에도 있어야 한다(감사 5.5).

    없던 동안에는 호스트가 스코프를 지정할 방법이 없었고, 문서만 읽고 안전을
    판단할 수도 없었다."""
    by_name = {s["name"]: s for s in tools.specs()}
    for name in ("vinc_delete", "vinc_delete_edge", "vinc_merge_nodes"):
        props = by_name[name]["inputSchema"]["properties"]
        assert "owner" in props, f"{name} 스키마에 owner 가 없다"


def test_delete_tools_send_an_owner_scope():
    """로컬 stdio 도 무스코프 삭제를 하지 않는다(감사 5.5).

    예전엔 args 에 owner 가 없고 forced_owner 도 없어 owner=None 이 그대로
    사이드카로 갔다 = 스코프 없는 DETACH DELETE."""
    sent: list[dict] = []

    class _B(_StubBridge):
        def request(self, method, path, params=None, body=None, timeout=60):
            sent.append(body or {})
            return {"ok": True}

    b = _B()
    for name, args in (("vinc_delete", {"node_id": "concept:x"}),
                       ("vinc_delete_edge", {"from_id": "concept:a",
                                             "to_id": "concept:b",
                                             "type": "relates_to"}),
                       ("vinc_merge_nodes", {"keep_id": "concept:a",
                                             "drop_id": "concept:b"})):
        sent.clear()
        tools.call(name, args, b)
        assert sent[0].get("owner") == b.owner, f"{name} 이 owner 를 안 실었다"

    # 원격(forced_owner)은 클라이언트가 보낸 owner 를 덮는다 — 멀티테넌트 봉쇄.
    sent.clear()
    tools.call("vinc_delete", {"node_id": "concept:x", "owner": "user:victim"},
               b, forced_owner="user:alice")
    assert sent[0]["owner"] == "user:alice"


def test_hub_profile_hides_and_rejects_target():
    """허브는 이미 원격 그래프다 — target 을 내보내지도 받지도 않는다(감사 5.5)."""
    hub = tools.specs(allow_target=False)
    assert all("target" not in s["inputSchema"]["properties"] for s in hub)
    # 데스크탑 stdio 는 그대로 갖는다(기본값 유지).
    assert all("target" in s["inputSchema"]["properties"] for s in tools.specs())

    # 스키마에서 뺐어도 클라이언트는 보낼 수 있다 — 실행 경로에서도 막는다.
    try:
        tools.call("vinc_search", {"query": "x", "target": "remote"},
                   _StubBridge(), allow_target=False)
    except ValueError as exc:
        assert "target" in str(exc)
    else:
        raise AssertionError("허브 프로파일이 target:remote 를 받아들였다")


def test_bridge_does_not_retry_writes_that_may_have_landed():
    """읽기 타임아웃이 쓰기를 중복 전송하지 않는다(감사 5.1).

    브리지 타임아웃 60초 대 잡 타임아웃 600초라, 큰 doc.put·ingest 는 사이드카가
    아직 일하는 중에 끊긴다. 예전 `except Exception` 은 그것을 두 번 더 보냈다."""
    from vinc_mcp import bridge as br

    calls = {"n": 0}

    def _boom(*_a, **_k):
        calls["n"] += 1
        raise TimeoutError("read timed out")

    b = Bridge(base="http://127.0.0.1:1", token="t")
    orig = br.urllib.request.urlopen
    br.urllib.request.urlopen = _boom
    try:
        res = b.request("POST", "/ingest/fragment", body={"nodes": []},
                        retries=2, retry_delay=0)
        assert calls["n"] == 1, f"쓰기를 {calls['n']}번 보냈다"
        assert res.get("retryable") is False and "unconfirmed" in res["error"]

        # GET 은 멱등이라 blip 흡수를 계속 한다(원래 재시도의 목적).
        calls["n"] = 0
        try:
            b.request("GET", "/search", params={"query": "x"},
                      retries=2, retry_delay=0)
        except TimeoutError:
            pass
        assert calls["n"] == 3, f"GET 재시도가 {calls['n']}회"

        # 연결 거부는 요청이 닿지 않은 것이 확실하다 — 쓰기도 재시도 안전.
        calls["n"] = 0

        def _refused(*_a, **_k):
            calls["n"] += 1
            raise ConnectionRefusedError("nothing listening")

        br.urllib.request.urlopen = _refused
        try:
            b.request("POST", "/delete", body={"node": "concept:x"},
                      retries=2, retry_delay=0)
        except ConnectionRefusedError:
            pass
        assert calls["n"] == 3, f"연결 거부 재시도가 {calls['n']}회"
    finally:
        br.urllib.request.urlopen = orig

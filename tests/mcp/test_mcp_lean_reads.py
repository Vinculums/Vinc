"""MCP tools/call 은 search·brief·neighbors 결과를 줄여서 싣는다. 핸들러와 REST v1 은 그대로다.

줄이는 것: 순위 진단(rows 의 score, node_hits 의 rrf_score 는 남는다), 아무것도 차지 않은 시간 블록
(updated_at 하나로 준다), brief 의 평평한 시간 사본, neighbors 의 빈 칸. `raw:true` 면 그대로 나간다.
합성 데이터만 쓴다.
"""
import copy
import json

import pytest

from vinc_mcp import rpc, tools
from tests.mcp.test_rest_v1 import Server, TEAM


def _block(**over):
    t = {"created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-02-01T00:00:00Z",
         "content_time": None, "valid_from": None, "valid_until": None, "superseded_by": None,
         "validity": "unknown", "interval_known": False, "interval_invalid": False,
         "holds_in_scope": None, "supersession": [], "supersedes": [], "claim_status": "unscoped",
         "conflicts": []}
    t.update(over)
    return t


NON_DEFAULT = [
    {"valid_from": "2026-01", "validity": "current", "interval_known": True},
    {"valid_until": "2020", "validity": "historical", "interval_known": True},
    {"valid_from": "nope", "interval_invalid": True},
    {"content_time": "1905"},
    {"superseded_by": "record:new"},
    {"supersession": [{"by": "record:new"}]},
    {"supersedes": ["record:old"]},
    {"conflicts": [{"id": "record:other"}]},
    {"claim_status": "date_unknown"},
    {"claim_status": "conflict"},
    {"holds_in_scope": True},
    {"claim_key": "x"},
]


def _search_result(rows=2, temporal=None):
    hit = {"id": "concept:a", "title": "A", "kind": "concept", "domain": "vinc/x",
           "matched_in": "title", "hit_tokens": 2, "title_tokens": 1, "token_total": 2,
           "rrf_score": 0.03, "arms": ["semantic", "title"], "distance": 0.5,
           "temporal": temporal or _block(), "prod_rank": 2, "colbert": 0.52,
           "sem_lead": 0.1, "sem_decisive": True, "cite_id": "concept:a"}
    return {"kind": "search", "executed": True,
            "rows": [{"id": f"doc.a#{i:05d}", "score": 4.1, "rrf_score": 0.08,
                      "arms": ["keyword", "semantic"], "snippet": "Marker " * 30, "truncated": True,
                      "doc_id": "doc.a", "temporal": copy.deepcopy(temporal or _block()),
                      "doc_title": "Doc A", "cite_id": "concept:doc.doc.a"} for i in range(rows)],
            "node_hits": [hit], "arms": "fts+vector", "total_count": rows, "has_more": False,
            "next_cursor": None, "validity_filtered": 3, "validity_best_dropped": [2, 1],
            "validity_best_dropped_distance": 0.4, "validity_note": "hid nodes",
            "node_window": {"pagination": "first_page_only", "returned": 1},
            "total_count_capped": False, "page": {"offset": 0, "limit": rows, "fingerprint": "abc"},
            "node_semantic": {"state": "complete"}, "node_rescore": {"state": "applied", "n": 20}}


def _flat(t):
    return {k: t[k] for k in ("created_at", "updated_at", "content_time", "valid_from",
                              "valid_until", "superseded_by", "validity")}


def _choose_brief():
    cands = []
    for i in range(3):
        t = _block()
        cands.append({"id": f"concept:c{i}", "cite_id": f"concept:c{i}", "title": f"C{i}",
                      "domain": "vinc/x", "kind": "concept", "matched_in": "title",
                      "hit_tokens": 1, "title_tokens": 1, "temporal": t, **_flat(t)})
    return {"kind": "brief", "executed": True, "query": "what is c", "status": "choose",
            "candidates": cands, "grounds": ["concept:c0"], "arms": "fts+vector",
            "resolution": {"rule": "v3", "p": 0.1}}


def _resolved_brief():
    t = _block()
    node = {"id": "concept:n", "cite_id": "concept:n", "title": "N", "domain": "vinc/x",
            "kind": "concept", "valid_from": "", "valid_until": "", "validity": "unknown",
            "superseded_by": "", "temporal": t, "created_at": t["created_at"],
            "updated_at": t["updated_at"], "content_time": None,
            "validity_basis": "legacy_visibility", "claim_validity": "unknown"}
    ex_t = _block(valid_from="2026-01", validity="current", interval_known=True)
    return {"kind": "brief", "executed": True, "status": "resolved", "node": node,
            "one_liner": "N is n.", "excerpts": [{"doc_id": "d", "text": "body",
                                                  "temporal": ex_t, **_flat(ex_t)}],
            "links": {"items": [{"id": "concept:m", "rel": "about", "temporal": _block(),
                                 **_flat(_block())}]},
            "grounds": ["concept:n"]}


def _neighbors():
    return {"kind": "neighbors", "executed": True, "found": True, "node": "concept:v",
            "neighbors": [{"rel": "about", "dir": "out", "id": f"concept:n{i}", "title": f"N{i}",
                           "domain": "vinc/x", "one_liner": "" if i else "has one",
                           "rev": 3, "updated_at": "2026-01-01" if i else "",
                           "valid_from": "", "valid_until": None} for i in range(3)],
            "by_rel": {"about": 3}, "count": 3, "neighbors_truncated": False, "error": None}


# ── 단위: 무엇을 빼고 무엇을 남기나 ─────────────────────────────────────────────

def test_default_block_shrinks_to_updated_at_and_scores_stay():
    src = _search_result()
    keep = copy.deepcopy(src)
    out = tools.lean_result("vinc_search", {"query": "x"}, src)
    assert src == keep, "핸들러 결과를 고치면 안 된다"
    for row in out["rows"]:
        assert row["temporal"] == {"updated_at": "2026-02-01T00:00:00Z"}
        assert row["score"] == 4.1 and "rrf_score" not in row and "arms" not in row
        assert row["truncated"] is True and row["snippet"] and row["cite_id"]
    hit = out["node_hits"][0]
    assert hit["rrf_score"] == 0.03 and hit["temporal"] == {"updated_at": "2026-02-01T00:00:00Z"}
    for k in ("matched_in", "hit_tokens", "title_tokens", "token_total", "arms", "distance",
              "prod_rank", "colbert", "sem_lead", "sem_decisive"):
        assert k not in hit, k
    for k in ("node_rescore", "validity_best_dropped", "validity_best_dropped_distance"):
        assert k not in out, k
    # 축소기·커서·계약이 읽는 것은 남는다.
    for k in ("page", "total_count", "arms", "has_more", "next_cursor", "node_window",
              "node_semantic", "validity_filtered", "validity_note", "total_count_capped"):
        assert k in out, k
    assert out["page"] == src["page"]


def test_a_default_block_without_updated_at_goes_away():
    out = tools.lean_result("vinc_search", {}, _search_result(temporal=_block(updated_at=None)))
    assert all("temporal" not in r for r in out["rows"])


@pytest.mark.parametrize("over", NON_DEFAULT)
def test_any_block_with_something_set_stays_whole(over):
    src = _search_result(temporal=_block(**over))
    out = tools.lean_result("vinc_search", {"mode": "current"}, src)
    for entry in out["rows"] + out["node_hits"]:
        assert entry["temporal"] == src["rows"][0]["temporal"]


def test_raw_returns_the_result_untouched():
    for name, src in (("vinc_search", _search_result()), ("vinc_brief", _choose_brief()),
                      ("vinc_neighbors", _neighbors())):
        assert tools.lean_result(name, {"raw": True}, src) is src
    other = {"kind": "node", "temporal": _block()}
    assert tools.lean_result("vinc_node", {}, other) is other


def test_choose_brief_drops_flat_copies_and_ranking():
    src = _choose_brief()
    out = tools.lean_result("vinc_brief", {}, src)
    assert "resolution" not in out and out["status"] == "choose" and out["grounds"]
    for c in out["candidates"]:
        assert set(c) == {"id", "cite_id", "title", "domain", "kind", "temporal"}, c
        assert c["temporal"] == {"updated_at": "2026-02-01T00:00:00Z"}
    assert len(json.dumps(out)) < len(json.dumps(src)) / 2


def test_choose_brief_under_current_keeps_the_conflict_block():
    src = _choose_brief()
    src["candidates"][0]["temporal"]["claim_status"] = "conflict"
    src["candidates"][0]["temporal"]["conflicts"] = [{"id": "concept:c1"}]
    out = tools.lean_result("vinc_brief", {"mode": "current"}, src)
    assert out["candidates"][0]["temporal"] == src["candidates"][0]["temporal"]
    # 블록과 같은 평평한 사본만 빠진다.
    assert "validity" not in out["candidates"][0]


def test_resolved_brief_keeps_node_validity_and_the_dated_excerpt():
    src = _resolved_brief()
    out = tools.lean_result("vinc_brief", {}, src)
    node = out["node"]
    assert node["validity"] == "unknown" and node["claim_validity"] == "unknown"
    assert node["temporal"] == {"updated_at": "2026-02-01T00:00:00Z"}
    for k in ("valid_from", "valid_until", "superseded_by", "created_at", "updated_at", "content_time"):
        assert k not in node, k
    ex = out["excerpts"][0]
    assert ex["temporal"] == src["excerpts"][0]["temporal"] and not set(_flat(ex["temporal"])) & set(ex)
    assert out["links"]["items"][0]["temporal"] == {"updated_at": "2026-02-01T00:00:00Z"}


def test_brief_node_validity_that_differs_from_the_block_stays():
    src = _resolved_brief()
    src["node"]["validity"] = "current"
    assert tools.lean_result("vinc_brief", {}, src)["node"]["validity"] == "current"


def test_neighbors_drop_only_empty_keys():
    src = _neighbors()
    out = tools.lean_result("vinc_neighbors", {}, src)
    assert "error" not in out and out["neighbors_truncated"] is False and out["count"] == 3
    first, second = out["neighbors"][0], out["neighbors"][1]
    assert first == {"rel": "about", "dir": "out", "id": "concept:n0", "title": "N0",
                     "domain": "vinc/x", "one_liner": "has one", "rev": 3}
    assert second["updated_at"] == "2026-01-01" and "one_liner" not in second
    assert [r["id"] for r in out["neighbors"]] == [r["id"] for r in src["neighbors"]]


def test_the_default_table_matches_the_store_metadata():
    from store import search_temporal
    assert tools._TEMPORAL_FLAT == search_temporal.FLAT
    blank = search_temporal.metadata({})
    assert set(blank) == set(tools._TEMPORAL_DEFAULT) | {"created_at", "updated_at"}
    assert tools._default_temporal(blank)


# ── 전송: MCP 만 바뀌고 REST v1 은 같다 ─────────────────────────────────────────

def _mcp(server, name, args):
    rpc_msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": name, "arguments": {**args, "space": TEAM}}}
    status, _, body = server.request("POST", "/mcp", body=rpc_msg)
    assert status == 200 and not body["result"]["isError"], body
    return body["result"]["content"][0]["text"]


@pytest.mark.parametrize("name,path,args,result", [
    ("vinc_search", "/v1/search", {"query": "x"}, _search_result()),
    ("vinc_brief", "/v1/brief", {"query": "x"}, _choose_brief()),
])
def test_rest_v1_keeps_the_full_shape_while_mcp_trims(name, path, args, result):
    server = Server(role="viewer", scope="read")
    try:
        server.bridge.override = copy.deepcopy(result)
        status, _, rest = server.request("POST", f"{path}?space={TEAM}", body=args)
        assert status == 200 and rest == result
        text = _mcp(server, name, args)
        assert json.loads(text) == tools.lean_result(name, args, result)
        assert ", " not in text[:200] and '":' in text           # 빈칸 없는 JSON
        assert json.loads(_mcp(server, name, {**args, "raw": True})) == result
        # MCP 를 거친 뒤에도 REST 는 같은 꼴이다(핸들러 결과를 고치지 않는다).
        status, _, again = server.request("POST", f"{path}?space={TEAM}", body=args)
        assert again == result
    finally:
        server.close()


def test_neighbors_over_mcp_and_rest():
    server = Server(role="viewer", scope="read")
    try:
        server.bridge.override = _neighbors()
        status, _, rest = server.request("GET", f"/v1/nodes/concept:v/neighbors?space={TEAM}")
        assert status == 200 and rest == _neighbors()
        assert json.loads(_mcp(server, "vinc_neighbors", {"node": "concept:v"})) == \
            tools.lean_result("vinc_neighbors", {}, _neighbors())
    finally:
        server.close()


def test_handler_output_is_unchanged():
    class Bridge:
        owner = "user:synthetic"
        def request(self, method, path, params=None, body=None, **_kw):
            return copy.deepcopy(_search_result())
    assert tools.call("vinc_search", {"query": "x"}, Bridge()) == _search_result()
    assert tools.call("vinc_search", {"query": "x", "raw": True}, Bridge()) == _search_result()


def test_raw_is_one_short_boolean_on_the_three_tools():
    served = {s["name"]: s for s in tools.specs(hub=True)}
    for name in ("vinc_search", "vinc_brief", "vinc_neighbors"):
        prop = served[name]["inputSchema"]["properties"]["raw"]
        assert prop["type"] == "boolean" and "x-reference" not in prop
        assert len(prop["description"]) <= 140


# ── 축소기: 줄인 뒤에도 페이지가 이어진다 ──────────────────────────────────────

class _BigBridge:
    owner = "user:synthetic"

    def __init__(self, result):
        self.result = result

    def request(self, method, path, params=None, body=None, **_kw):
        return copy.deepcopy(self.result)


def test_shrink_still_pages_after_the_trim():
    big = _search_result(rows=100)
    for i, row in enumerate(big["rows"]):
        row["snippet"] = f"{i} " + "x" * 900
    big["total_count"] = 100
    big["page"]["row_offsets"] = {r["id"]: i for i, r in enumerate(big["rows"])}
    assert len(json.dumps(tools.lean_result("vinc_search", {}, big))) > rpc.MAX_RESULT_CHARS
    d = rpc.Dispatcher(_BigBridge(big))
    reply = d.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                      "params": {"name": "vinc_search", "arguments": {"query": "x"}}})
    result = reply["result"]
    assert not result["isError"], result
    text = result["content"][0]["text"]
    assert len(text) <= rpc.MAX_RESULT_CHARS and ", " not in text[:300]
    out = json.loads(text)
    kept = len(out["rows"])
    assert 0 < kept < 100 and out["has_more"] and out["next_cursor"]
    cursor = json.loads(__import__("base64").urlsafe_b64decode(out["next_cursor"] + "==="))
    assert cursor["off"] == kept and cursor["qh"] == "abc" and cursor["eid"] == big["rows"][kept]["id"]
    assert all(r["temporal"] == {"updated_at": "2026-02-01T00:00:00Z"} for r in out["rows"])
    assert all("rrf_score" not in r for r in out["rows"])
    # 줄인 덕에 줄이지 않았을 때보다 행이 더 실린다.
    d_raw = rpc.Dispatcher(_BigBridge(big))
    raw = json.loads(d_raw.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
        "name": "vinc_search", "arguments": {"query": "x", "raw": True}}})["result"]["content"][0]["text"])
    assert len(raw["rows"]) < kept and "rrf_score" in raw["rows"][0]


def test_the_deploy_probe_reads_the_full_shape():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import verify_search_temporal_remote as probe
    sent = []

    def fake(request, timeout):
        sent.append(json.loads(request.data))
        return {"result": {"content": [{"type": "text", "text": "{}"}]}}
    old = probe._json_read
    probe._json_read = fake
    try:
        probe.read_mcp({"mode": "current"}, "synthetic", 30)
    finally:
        probe._json_read = old
    assert sent[0]["params"]["arguments"]["raw"] is True


# ── 실제 스토어: 모드마다 MCP 결과가 핸들러 결과를 같은 규칙으로 줄인다 ──────────────

from tests.mcp.test_search_temporal_once import MODES, OWNER, app  # noqa: E402,F401  (모듈 픽스처)


def _over_mcp(app, name, args):
    from api.cli_tools import _InProcessBridge
    bridge = _InProcessBridge(app)
    bridge.owner = OWNER
    reply = rpc.Dispatcher(bridge).handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                           "params": {"name": name, "arguments": args}})
    assert not reply["result"]["isError"], reply
    return json.loads(reply["result"]["content"][0]["text"]), tools.call(name, args, bridge)


@pytest.mark.parametrize("kw", MODES)
def test_real_search_keeps_every_block_that_says_something(app, kw):
    mcp, handler = _over_mcp(app, "vinc_search", {"query": "Marker", "limit": 20, **kw})
    entries = lambda r: (r.get("rows") or []) + (r.get("node_hits") or [])  # noqa: E731
    assert [e["id"] for e in entries(mcp)] == [e["id"] for e in entries(handler)]
    kept = 0
    for got, full in zip(entries(mcp), entries(handler)):
        if tools._default_temporal(full["temporal"]):
            assert got["temporal"] == {"updated_at": full["temporal"]["updated_at"]}
        else:
            assert got["temporal"] == full["temporal"]
            kept += 1
    assert kept, "합성 데이터에는 구간이나 내용 시점을 단 행이 있다"
    if kw.get("mode") == "current":
        # current 는 모든 주장을 판정한다(claim_status 가 unscoped 가 아니다) — 블록이 다 남는다.
        assert kept == len(entries(handler))


def test_real_brief_over_mcp(app):
    mcp, handler = _over_mcp(app, "vinc_brief", {"query": "Marker"})
    assert mcp["status"] == handler["status"]
    assert len(json.dumps(mcp)) < len(json.dumps(handler))

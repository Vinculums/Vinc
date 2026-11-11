"""저작 경로(ingest)의 종류 규칙: 날짜 없는 새 결정·사건은 오늘로 채우고 말한다. 거절하지 않는다.

고정하는 것:
  ① 새 결정의 props.valid_from, 새 사건의 date 를 오늘(UTC)로 채우고 경고에 노드와 필드를 적는다.
  ② 쓴 사람이 보낸 값은 그대로다. 기존 노드의 갱신(props 없이, props_patch)은 다시 찍지 않는다.
  ③ sync·팩(저작 아님)과 커뮤니티 소유자는 채우지 않는다.
  ④ about 없는 결정의 경고와 채움 경고가 MCP·REST v1·CLI 응답까지 간다.
  ⑤ 이 경로로 쓴 그래프는 날짜로 묶인 종류별 목록에서 결정·사건을 잃지 않는다.
"""
import io
import json
import os
from contextlib import redirect_stdout
from unittest.mock import patch

import pytest

from api import cli, fragment as frag
from api import temporal_read
from api.server import VincApp
from store import querybase
from store.backend import open_store
from store.config import load_config
from vinc_mcp import rpc
from vinc_mcp.bridge import Bridge

OWNER = "user:kind-defaults"
DAY = "2026-03-04"          # 고정한 오늘. 벽시계에 기대지 않는다


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_STORE_BACKEND", "embedded")
    # 쓰기(`fragment`)와 읽기(`validity_state`)가 같은 시계를 본다
    monkeypatch.setattr(querybase, "_today", lambda: DAY)
    st = open_store(load_config({"data_dir": str(tmp_path / "db"), "embedding_dim": 4}))
    st.provision(dim=4)
    try:
        yield st
    finally:
        st.close()


def _app(store):
    class Jobs:
        def run_sync(self, name, fn):
            return fn()
    app = object.__new__(VincApp)
    app.store, app.themes, app.jobs = store, None, Jobs()
    return app


def ingest(store, nodes, edges=(), owner=OWNER):
    """사이드카 ingest 라우트 그대로(authored=True, read_back=True)."""
    status, res = VincApp.write_ingest(_app(store), {"owner": owner, "nodes": list(nodes),
                                                     "edges": list(edges)})
    assert status == 200
    return res


def stored(res, nid):
    return next(n for n in res["stored"] if n["id"] == nid)


def filled(res):
    return [w for w in res["warnings"] if "filled with today's date" in w]


def row(store, nid, owner=OWNER):
    return temporal_read.node_times(store, [nid], owner=owner)[nid]


def test_new_decision_without_validity_start_gets_today_and_a_warning(store):
    res = ingest(store, [{"id": "decision:d1", "type": "decision", "title": "Pick A",
                          "props": {"note": "why"}}])
    assert res["executed"] and res["merged"]["nodes"]["created"] == 1
    assert stored(res, "decision:d1")["props"] == {"note": "why", "valid_from": DAY}
    assert filled(res) == [
        f"decision:d1 has no props.valid_from: filled with today's date {DAY} (UTC). "
        "Send props.valid_from to record when the decision took effect."]
    # props 를 아예 안 보낸 결정도 같다
    res = ingest(store, [{"id": "decision:d2", "type": "decision", "title": "Pick B"}])
    assert row(store, "decision:d2")["valid_from"] == DAY
    assert len(filled(res)) == 1 and "decision:d2" in filled(res)[0]


def test_new_episode_without_date_gets_today_and_a_warning(store):
    res = ingest(store, [{"id": "episode:e1", "type": "episode", "title": "Launch"},
                         {"id": "episode:e2", "type": "episode", "title": "Blank", "date": ""}])
    assert res["executed"] and res["merged"]["nodes"]["created"] == 2
    assert stored(res, "episode:e1")["date"] == DAY == stored(res, "episode:e2")["date"]
    assert filled(res)[0] == (f"episode:e1 has no date: filled with today's date {DAY} (UTC). "
                              "Send date to record when it happened.")
    assert len(filled(res)) == 2


def test_the_writers_own_value_is_kept(store):
    res = ingest(store, [
        {"id": "decision:own", "type": "decision", "title": "Own",
         "props": {"valid_from": "2025-01-02"}},
        {"id": "episode:own", "type": "episode", "title": "Own", "date": "2025-05-06"},
        {"id": "episode:promoted", "type": "episode", "title": "Promoted",
         "props": {"date": "2024-07"}},
    ])
    assert filled(res) == []
    assert row(store, "decision:own")["valid_from"] == "2025-01-02"
    assert row(store, "episode:own")["date"] == "2025-05-06"
    assert row(store, "episode:promoted")["date"] == "2024-07"


def test_an_update_does_not_restamp_a_stored_date(store):
    ingest(store, [{"id": "decision:old", "type": "decision", "title": "Old",
                    "props": {"valid_from": "2025-01-02", "note": "n"}},
                   {"id": "episode:old", "type": "episode", "title": "Old", "date": "2025-05-06"}])
    # props 없는 부분 재전송
    res = ingest(store, [{"id": "decision:old", "type": "decision", "tags": ["x"]},
                         {"id": "episode:old", "type": "episode", "title": "Renamed"}])
    assert filled(res) == [] and res["merged"]["nodes"]["matched"] == 2
    assert row(store, "decision:old")["valid_from"] == "2025-01-02"
    assert row(store, "episode:old")["date"] == "2025-05-06"
    # props_patch(+expected_rev) 는 저장된 props 위에 합친다
    from store import pkns
    rev = store.run("MATCH (n:Decision) WHERE n.decision_id = $id RETURN n.rev AS rev",
                    {"id": pkns.key(OWNER, "decision:old")})[0]["rev"]
    res = ingest(store, [{"id": "decision:old", "type": "decision",
                          "props_patch": {"note": "m"}, "expected_rev": rev}])
    assert filled(res) == []
    assert stored(res, "decision:old")["props"] == {"valid_from": "2025-01-02", "note": "m"}
    # 날짜 없이 저장된 옛 노드도 만졌다고 오늘이 되지 않는다(기록 시각은 유효 시점이 아니다)
    frag.merge_fragment({"owner": OWNER, "nodes": [
        {"id": "decision:legacy", "type": "decision", "title": "Legacy"},
        {"id": "episode:legacy", "type": "episode", "title": "Legacy"}]}, store=store)
    res = ingest(store, [{"id": "decision:legacy", "type": "decision", "tags": ["y"]},
                         {"id": "episode:legacy", "type": "episode", "tags": ["y"]}])
    assert filled(res) == []
    assert row(store, "decision:legacy")["valid_from"] == ""
    assert row(store, "episode:legacy")["date"] == ""


def test_sync_pack_and_community_writes_are_not_filled(store):
    nodes = [{"id": "decision:copy", "type": "decision", "title": "Copy"},
             {"id": "episode:copy", "type": "episode", "title": "Copy"}]
    res = frag.merge_fragment({"owner": OWNER, "nodes": nodes}, store=store)   # sync·팩 경로
    assert filled(res) == []
    assert row(store, "episode:copy")["date"] == ""
    community = "community:00000000-0000-7000-8000-000000000001"
    res = frag.merge_fragment({"owner": community, "nodes": nodes}, store=store,
                              authored=True, read_back=True)
    assert filled(res) == [] and not any("has no 'about' edge" in w for w in res["warnings"])


def _sidecar_bridge(store, owner):
    class StoreBridge(Bridge):
        def __init__(self):
            super().__init__(base="http://127.0.0.1:1", token="t", owner=owner)

        def request(self, method, path, params=None, body=None, **_kw):
            assert path == "/ingest/fragment"
            status, res = VincApp.write_ingest(_app(store), dict(body))
            assert status == 200
            return res
    return StoreBridge()


ORPHAN = [{"id": "decision:orphan", "type": "decision", "title": "Orphan"}]


def _assert_receipt(res, nid="decision:orphan"):
    assert res["executed"] and res["partial"] is True
    assert any(w.startswith(f"{nid} has no 'about' edge") for w in res["warnings"]), res
    assert any(w.startswith(f"{nid} has no props.valid_from") for w in res["warnings"]), res


def test_orphan_and_filled_warnings_reach_the_mcp_tool_result(store):
    bridge = _sidecar_bridge(store, OWNER)
    reply = rpc.Dispatcher(bridge).handle({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "vinc_ingest", "arguments": {"nodes": ORPHAN}}})
    result = reply["result"]
    assert result["isError"] is False
    _assert_receipt(json.loads(result["content"][0]["text"]))


def test_orphan_and_filled_warnings_reach_rest_v1_and_the_cli(store):
    from tests.mcp.test_rest_v1 import Server
    server = Server(role="editor")
    try:
        server.bridge.request = _sidecar_bridge(store, "user:u1").request
        status, _, res = server.request("POST", "/v1/fragments", body={"nodes": ORPHAN})
        assert status == 200
        _assert_receipt(res)
        out = io.StringIO()
        node = [{**ORPHAN[0], "id": "decision:orphan-cli"}]
        with patch.dict("os.environ", {"VINC_API_KEY": "vinc_sk_good",
                                      "VINC_API_URL": server.base}), redirect_stdout(out), \
                patch.object(server.server.RequestHandlerClass, "log_message", lambda *a: None):
            code = cli.main(["--target", "account", "call", "vinc_ingest",
                             "--args", json.dumps({"nodes": node})])
        assert code == 0
        _assert_receipt(json.loads(out.getvalue()), "decision:orphan-cli")
    finally:
        server.close()


def test_a_graph_written_through_ingest_keeps_every_decision_and_episode_in_dated_lists(store):
    """종류별 목록 하네스(VIN-977)와 같은 판정을 자체로 본다. 날짜 없이 쓴 결정·사건이 제품의 날짜
    범위 판정(기본 보기·mode=current 범위·as_of 의 오늘·이번 달·올해)에서 하나도 빠지지 않는다."""
    from vinc_rag import retrieval as R
    decisions = [f"decision:d{i}" for i in range(4)]
    episodes = [f"episode:e{i}" for i in range(4)]
    nodes = [{"id": "concept:topic", "type": "concept", "title": "Topic"}]
    nodes += [{"id": d, "type": "decision", "title": d, "props": {"note": d}} for d in decisions]
    nodes += [{"id": e, "type": "episode", "title": e} for e in episodes]
    edges = [{"from": n, "to": "concept:topic", "type": "about"} for n in decisions + episodes]
    res = ingest(store, nodes, edges)
    assert res["merged"]["nodes"]["created"] == 9 and len(filled(res)) == 8
    scopes = [querybase.time_scope(DAY), querybase.time_scope(DAY[:7]), querybase.time_scope(DAY[:4])]
    vf = R._validity_for_ids(store, decisions, owner=OWNER)
    assert all(R._visible(*vf[d], historical=False, scope=None) for d in decisions)
    for scope in scopes:
        assert [d for d in decisions if not R._visible(*vf[d], historical=False, scope=scope)] == []
        gate = temporal_read.Gate(store, decisions + episodes, owner=OWNER,
                                  as_of_scope=scope, known_scope=None)
        assert [n for n in decisions + episodes if not gate.keep(n)] == [], scope

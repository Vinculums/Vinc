# -*- coding: utf-8 -*-
"""한 스토어에 두 소유자를 두고, 한쪽 키로 다른 쪽 그래프에 닿는 길이 없는지 실제 경로로 본다.

스텁 브리지가 아니라 실제 사이드카(`api.server`)와 실제 허브(`vinc_mcp.http_server`)를
루프백에 띄우고 그 위로 요청을 보낸다. 플랜 문자열을 보는 테스트는 엔진이 owner 를
버리는 경우를 못 잡기 때문이다.

  1. 검색: 두 소유자의 문서와 노드가 같은 낱말을 갖는다. 앨리스의 검색 결과에 밥의
     발췌·제목·문서 id 가 하나도 없어야 한다. 사이드카 `/search`, 허브 MCP `vinc_search`,
     REST `/v1/search`, 웹 평면 프록시 `search` 네 곳을 본다.
  2. 동기화: 앨리스의 키로 밥의 노드와 엣지를 지우는 묶음, 밥의 id 로 노드를 덮는 묶음을
     허브 `/sync/push` 로 보낸다. 밥의 노드·엣지·rev·sync_seq·created 가 그대로여야 한다.

두 키 모드(`VINC_PK_NAMESPACED` 0·1)를 같은 테스트로 돈다. 허브는 1 로, 셀프호스트와
로컬은 0 으로 돈다."""
from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import docstore, fragment, revstamp, sync  # noqa: E402
from api import server as sidecar  # noqa: E402
from api.auth import SessionAuth  # noqa: E402
from store import pkns  # noqa: E402
from store.backend import open_store  # noqa: E402
from store.config import load_config  # noqa: E402
from store.embedded_backend import import_driver  # noqa: E402
from vinc_mcp import http_server as hub  # noqa: E402
from vinc_mcp.bridge import Bridge  # noqa: E402

pytestmark = pytest.mark.skipif(import_driver()[0] is None,
                                reason="임베디드 드라이버(ladybug) 미설치")

ALICE, BOB = "user:alice-e2e", "user:bob-e2e"
KEYS = {"vinc_sk_alice": ALICE, "vinc_sk_bob": BOB}
SIDECAR_TOKEN = "sidecar-session-token"
#: 두 소유자가 함께 갖는 검색어. 질의에 쓰이므로 응답에 메아리칠 수 있다.
SHARED_WORD = "zanzibarquill"
#: 밥에게만 있는 표식. 어느 질의에도 쓰지 않으므로 앨리스의 응답에 나오면 곧 유출이다.
BOB_MARKS = ("bobexcerptmark", "bobtitlemark", "doc-bob-private")
ALICE_MARKS = ("aliceexcerptmark", "doc-alice-own")


def _serve(httpd):
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return t


def _call(base, method, path, *, key=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        resp = urllib.request.urlopen(req, timeout=60)
    except urllib.error.HTTPError as exc:
        resp = exc
    with resp:
        return resp.status, resp.read().decode("utf-8")


@pytest.fixture(params=["0", "1"], ids=["pk-flat", "pk-namespaced"])
def stack(tmp_path, monkeypatch, request):
    """두 소유자가 든 스토어 하나, 그 위의 사이드카, 그 앞의 허브."""
    monkeypatch.setenv("VINC_PK_NAMESPACED", request.param)
    monkeypatch.delenv("VINC_EMBED_ENCODE", raising=False)
    cfg = load_config({"data_dir": str(tmp_path / "db"), "embedding_dim": 4,
                       "embedding_model": "local-en", "buffer_pool_mb": 128})
    store = open_store(cfg)
    assert store is not None
    store.provision(dim=4)

    app = sidecar.VincApp(cfg, store=store, auth=SessionAuth(SIDECAR_TOKEN), quiet_boot=True)
    side = sidecar.make_server(app)
    side_thread = _serve(side)
    side_base = f"http://127.0.0.1:{side.server_address[1]}"

    def verify(token, team=None):
        owner = KEYS.get(token)
        if owner is None or team:
            return None
        return {"owner": owner, "scope": "full", "plan": "free"}

    hub_app = hub.MCPHTTPApp(Bridge(base=side_base, token=SIDECAR_TOKEN, owner="user:local"),
                             verify_key=verify)
    front = hub.make_server(hub_app, "127.0.0.1", 0)
    front_thread = _serve(front)
    hub_base = f"http://127.0.0.1:{front.server_address[1]}"
    yield {"store": store, "app": app, "hub_app": hub_app,
           "side": side_base, "hub": hub_base}
    for httpd, thread in ((front, front_thread), (side, side_thread)):
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
    app.jobs.shutdown(drain=True)
    store.close()


# ── 1. 검색 ───────────────────────────────────────────────────────────────────

def _seed_search(store):
    """두 소유자가 같은 검색어를 가진 문서·개념을 하나씩 갖는다.

    키 모드 1 에서는 개념 슬러그를 일부러 같게 둔다: 합성된 물리 키가 둘을 가르는지도 본다.
    모드 0 에서는 같은 슬러그의 두 번째 소유자를 스토어가 건너뛰므로 슬러그를 가른다."""
    shared = pkns.enabled()
    for owner, doc_id, mark, slug, title in (
            (ALICE, "doc-alice-own", "aliceexcerptmark", "concept:quill-note",
             f"Alice {SHARED_WORD} note"),
            (BOB, "doc-bob-private", "bobexcerptmark",
             "concept:quill-note" if shared else "concept:quill-note-bob",
             f"Bob {SHARED_WORD} bobtitlemark")):
        put = docstore.put_doc(store, owner=owner, doc_id=doc_id, title=f"{owner} notes",
                               text=f"{SHARED_WORD} appears here next to {mark} in a sentence.",
                               topic="vinc/test/isolation")
        assert put["executed"], put
        res = fragment.merge_fragment({"owner": owner, "nodes": [
            {"id": slug, "type": "concept", "title": title,
             "domain": "vinc/test/isolation"}]}, store=store)
        assert res["executed"] and not res.get("partial"), res


def _assert_alice_only(text: str, surface: str):
    leaked = [m for m in BOB_MARKS if m in text]
    assert not leaked, f"{surface}: 앨리스의 검색에 밥의 내용이 실렸다 {leaked}: {text[:800]}"
    assert "aliceexcerptmark" in text, f"{surface}: 앨리스 자신의 문서가 없다(검색이 비었다): {text[:800]}"


def test_search_never_returns_another_owners_rows_or_nodes(stack):
    store = stack["store"]
    _seed_search(store)

    # 대조군: 밥은 자기 것을 찾는다. 이것이 없으면 아래의 "없음"은 아무것도 증명하지 않는다.
    status, bob_view = _call(stack["side"], "GET",
                             f"/search?query={SHARED_WORD}&owner={BOB}&top_k=20", key=SIDECAR_TOKEN)
    assert status == 200, bob_view
    assert "bobexcerptmark" in bob_view and "aliceexcerptmark" not in bob_view, bob_view[:800]

    # 사이드카 라우트: 허브와 웹 평면이 owner 를 실어 부르는 그 자리다.
    status, body = _call(stack["side"], "GET",
                         f"/search?query={SHARED_WORD}&owner={ALICE}&top_k=20", key=SIDECAR_TOKEN)
    assert status == 200, body
    _assert_alice_only(body, "sidecar /search")

    # 허브 MCP: 클라이언트가 owner 에 밥을 적어도 키가 정한 앨리스로 덮여야 한다.
    status, body = _call(stack["hub"], "POST", "/mcp", key="vinc_sk_alice", body={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "vinc_search",
                   "arguments": {"query": SHARED_WORD, "owner": BOB, "limit": 20}}})
    assert status == 200, body
    assert '"isError": true' not in body.replace('"isError":true', '"isError": true'), body[:800]
    _assert_alice_only(body, "hub MCP vinc_search")

    # REST: 본문에 owner 를 실어도 라우트가 받지 않는다.
    status, body = _call(stack["hub"], "POST", "/v1/search", key="vinc_sk_alice",
                         body={"query": SHARED_WORD, "limit": 20})
    assert status == 200, body
    _assert_alice_only(body, "REST /v1/search")

    # 웹 평면(계정 서명 프록시): 인자의 owner 는 벗겨지고 서명된 owner 가 실린다.
    out = stack["hub_app"].proxy_op("search", {"query": SHARED_WORD, "owner": BOB, "top_k": 20},
                                    ALICE, actor=ALICE)
    _assert_alice_only(json.dumps(out, ensure_ascii=False), "web proxy search")


# ── 2. 동기화 ─────────────────────────────────────────────────────────────────

VICTIM, PEER = "concept:victim-node", "concept:victim-peer"


def _bob_row(store, nid):
    rows = store.run("MATCH (c:Concept) WHERE c.concept_id = $id AND c.owner_id = $o "
                     "RETURN c.title AS title, c.rev AS rev, c.sync_seq AS seq, "
                     "c.updated_at AS ua, c.created AS created",
                     {"id": pkns.key(BOB, nid), "o": BOB})
    return rows[0] if rows else None


def _bob_edges(store):
    rows = store.run("MATCH (a:Concept)-[r]->(b:Concept) WHERE a.concept_id = $s "
                     "AND b.concept_id = $d RETURN count(r) AS c",
                     {"s": pkns.key(BOB, VICTIM), "d": pkns.key(BOB, PEER)})
    return int(rows[0]["c"]) if rows else 0


def _push(stack, key, bundle):
    status, body = _call(stack["hub"], "POST", "/sync/push", key=key, body={"bundle": bundle})
    assert status == 200, body
    return json.loads(body)


def test_sync_push_from_another_member_cannot_touch_the_owners_nodes_or_edges(stack):
    store = stack["store"]
    res = fragment.merge_fragment({"owner": BOB, "nodes": [
        {"id": VICTIM, "type": "concept", "title": "Bob keeps this", "domain": "vinc/test/sync"},
        {"id": PEER, "type": "concept", "title": "Bob peer", "domain": "vinc/test/sync"},
    ], "edges": [{"from": VICTIM, "to": PEER, "type": "relates_to"}]}, store=store)
    assert res["executed"] and not res.get("partial"), res
    before = _bob_row(store, VICTIM)
    assert before is not None and _bob_edges(store) == 1

    # 엣지 묘비의 와이어 꼴은 저장 rel 이름(`RELATED_TO` 류)이다. 조각 꼴(`relates_to`)을
    # 실으면 apply 가 모르는 rel 로 건너뛰어 아무것도 안 지우고 통과한다. 그래서 동기화 표에서
    # 거꾸로 찾고, 맨 끝에서 밥 자신의 키로 같은 묘비가 실제로 지우는 것까지 본다.
    rel = next(r for r, ft in sync._REL_TO_FRAGMENT.items() if ft == "relates_to")
    assert sync.q.sync_edge_spec(rel) is not None, rel

    # 삭제 시각은 지금이다. 상한에 걸려 거절되는 값이 아니라 받아들여지는 값이어야, 지켜 주는
    # 것이 시계가 아니라 owner 술어임을 본다.
    now = revstamp.hlc_now(store)
    _push(stack, "vinc_sk_alice", {
        "tombstones": [{"node_id": VICTIM, "label": "Concept", "deleted_at": now, "rev": 99}],
        "edge_tombstones": [{"src": VICTIM, "rel": rel, "dst": PEER, "deleted_at": now}]})
    after = _bob_row(store, VICTIM)
    assert after is not None, "앨리스의 묘비가 밥의 노드를 지웠다"
    assert _bob_edges(store) == 1, "앨리스의 엣지 묘비가 밥의 관계를 지웠다"
    assert (after["title"], after["rev"], after["seq"]) == \
        (before["title"], before["rev"], before["seq"]), (before, after)

    # 같은 id 로 덮어쓰기: 키 모드 0 에서는 스토어가 건너뛰고, 1 에서는 앨리스의 별도 행이
    # 생긴다. 어느 쪽이든 밥의 제목·rev·sync_seq·created 는 그대로여야 한다.
    later = revstamp.hlc_now(store)
    _push(stack, "vinc_sk_alice", {"nodes": [
        {"id": VICTIM, "type": "concept", "title": "Hijacked by alice", "domain": "vinc/test/sync",
         "_rev": 500, "_updated_at": later, "_created": "2000-01-01T00:00:00.000Z"}]})
    final = _bob_row(store, VICTIM)
    assert final == after, (after, final)
    assert _bob_edges(store) == 1

    # 대조군: 같은 꼴의 묘비를 밥 자신의 키로 보내면 실제로 지워진다. 이것이 없으면 위의
    # "그대로다"는 묘비 꼴이 틀려서 생긴 것일 수 있다.
    last = revstamp.hlc_now(store)
    _push(stack, "vinc_sk_bob", {
        "edge_tombstones": [{"src": VICTIM, "rel": rel, "dst": PEER, "deleted_at": last}]})
    assert _bob_edges(store) == 0, "밥 자신의 엣지 묘비가 적용되지 않았다(묘비 꼴이 틀렸다)"
    last = revstamp.hlc_now(store)
    _push(stack, "vinc_sk_bob", {
        "tombstones": [{"node_id": VICTIM, "label": "Concept", "deleted_at": last, "rev": 99}]})
    assert _bob_row(store, VICTIM) is None, "밥 자신의 묘비가 적용되지 않았다(묘비 꼴이 틀렸다)"

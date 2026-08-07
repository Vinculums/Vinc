# -*- coding: utf-8 -*-
"""SEC-1 — owner 격리 회귀. 호스티드 멀티테넌트의 핵심 보안 속성.

**왜 이 테스트가 이렇게 생겼나**: 기존 E2E(test_mcp_auth)는 *스텁 브리지*로
"사이드카가 owner=user:u1 을 받았다"까지만 봤다. 배관은 맞았지만 **엔진이 그 owner
를 버렸고**(seed 갈래·search·decide) 스텁은 그걸 볼 수 없었다. 그래서 여기서는
계약의 두 층을 실물로 본다:

  1. **플랜 층**: 생성된 Cypher 에 owner 술어가 실제로 박히는지(어디서든 실행 가능 —
     Windows 에서 임베디드 드라이버(ladybug) E2E 가 못 도는 환경에서도 회귀를 잡는다).
  2. **전송 층**: MCP 도구가 owner 를 사이드카로 **실어 보내는지**(안 보내면 엔진이
     아무리 옳아도 스코프가 없다 — vinc_search/vinc_decide 의 실제 결함).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import query as q          # noqa: E402
from vinc_mcp import tools          # noqa: E402
from vinc_mcp.bridge import Bridge  # noqa: E402

OWNER = "user:alice"


class _StubBridge(Bridge):
    def __init__(self):
        super().__init__(base="http://127.0.0.1:1", token="t", owner="user:local")
        self.calls: list[tuple] = []

    def request(self, method, path, params=None, body=None, timeout=60):
        self.calls.append((method, path, params, body))
        return {"executed": False}


# ── 1) 플랜 층: Cypher 에 owner 술어가 박히는가 ──────────────────────────────
def test_subgraph_ids_plan_scopes_seed_and_reached():
    p = q.subgraph_ids_plan("concept:x", depth=2, owner=OWNER)
    assert "n.owner_id = $owner" in p.cypher   # 시드
    assert "m.owner_id = $owner" in p.cypher   # 도달 노드
    assert p.params["owner"] == OWNER


def test_graph_rows_plan_scopes_rows_and_edge_targets():
    p = q.graph_rows_for_ids_plan(["concept:x"], owner=OWNER)
    assert "c.owner_id = $owner" in p.cypher   # 반환 행
    assert "d.owner_id = $owner" in p.cypher   # 엣지 대상(남의 title 노출 차단)
    assert p.params["owner"] == OWNER


def test_seed_lookup_plan_scopes_owned_tables():
    for seed in ("concept:x", "episode:e1", "record:r1", "entity:n1"):
        p = q.seed_lookup_plan(seed, owner=OWNER)
        assert "n.owner_id = $owner" in p.cypher, seed
    # F1 — 미지 접두사 = 개방층 id(사용자 저작 타입) → Entity 프로브. 무접두사는
    # Concept(하위호환).
    assert "MATCH (n:Entity)" in q.seed_lookup_plan("theory:x", owner=OWNER).cypher
    assert "MATCH (n:Concept)" in q.seed_lookup_plan("bare", owner=OWNER).cypher
    # Bridge 는 v2(F5)에서 owner_id 를 얻어 과도기 술어로 필터한다 — 구 '필터 불가'
    # 한계(존재 여부 노출) 해소.
    pb = q.seed_lookup_plan("bridge:b1", owner=OWNER)
    assert "(n.owner_id = $owner OR n.owner_id = '')" in pb.cypher
    assert pb.params["owner"] == OWNER


def test_bridge_axis_plans_scope_owner():
    """F5 — Bridge 관련 신규/개정 플랜 전부 owner 술어를 갖는다(과도기 '' 포함)."""
    pred = "owner_id = $owner OR"
    assert pred in q.bridge_ties_plan(owner=OWNER).cypher
    assert pred in q.bridge_coupling_plan(a="a", b="b", owner=OWNER).cypher
    assert pred in q.bridge_rows_plan(owner=OWNER, domain="d").cypher
    assert pred in q.bridge_neighbors_plan("concept:x", owner=OWNER).cypher
    # 부착 축(Episode/Record)과 entity 플랜은 엄격 스코프
    assert "e.owner_id = $owner" in q.episode_rows_plan(owner=OWNER, domain="d").cypher
    assert "rc.owner_id = $owner" in q.record_rows_plan(owner=OWNER, domain="d").cypher
    assert "n.owner_id = $owner" in q.entity_subgraph_ids_plan("entity:x", owner=OWNER).cypher
    assert "e.owner_id = $owner" in q.entity_rows_for_ids_plan(["entity:x"], owner=OWNER).cypher


def test_decide_resolve_plan_scopes_and_parenthesizes_or():
    p = q.decide_topic_resolve_plan("t", owner=OWNER)
    assert "c.owner_id = $owner" in p.cypher
    # OR 를 괄호로 안 묶으면 AND 가 뒤 절에만 붙어 필터가 무력화된다 — 급소.
    where = p.cypher.split("WHERE", 1)[1]
    assert where.strip().startswith("("), p.cypher
    assert ") AND c.owner_id" in p.cypher


def test_plans_stay_global_without_owner():
    """로컬 단일 사용자(owner 없음)는 기존 전역 동작 유지 — 하위호환."""
    assert "owner_id" not in q.subgraph_ids_plan("concept:x").cypher
    assert "owner_id" not in q.graph_rows_for_ids_plan(["c"]).cypher
    assert "owner_id" not in q.decide_topic_resolve_plan("t").cypher
    assert "owner_id" not in q.seed_lookup_plan("concept:x").cypher


def test_node_fallback_search_scopes_concept_and_record():
    """_node_fallback_search 는 store.run 을 직접 부른다 — Cypher 를 가로채 검사."""
    seen = {}

    class _S:
        def run(self, cypher, params):
            seen["cypher"], seen["params"] = cypher, params
            return []

    q._node_fallback_search(_S(), "hello world", limit=5, owner=OWNER)
    assert "c.owner_id = $owner" in seen["cypher"]
    assert "r.owner_id = $owner" in seen["cypher"]
    assert seen["params"]["owner"] == OWNER

    seen.clear()
    q._node_fallback_search(_S(), "hello", limit=5)          # owner 없음 → 전역
    assert "owner_id" not in seen["cypher"]


# ── 1b) 청크 팔: FTS·벡터에 owner 필터가 실제로 내려가는가 (위험 감사 1.1 · P0) ──
# 이 절이 **없어서** 결함이 살아 있었다. 위 `test_node_fallback_search_scopes_*` 는
# 노드 폴백만 봤고, 그쪽은 막혀 있었다. 정작 문서 **본문 발췌**를 싣는 청크 팔에는
# owner 가 한 번도 안 갔다 — 호스티드에서 남의 문서 본문이 rows[] 로 나갈 수 있었다.
class _ChunkSpy:
    """청크 팔에 넘어온 filters 를 기록하는 스토어 더블."""

    scoped_chunk_search = True          # owner 필터를 실제로 적용한다는 선언

    def __init__(self):
        self.ft_filters: list = []
        self.vec_filters: list = []

    def run(self, cypher, params=None):
        return []                       # 노드 폴백은 이 테스트의 관심사가 아니다

    def fulltext_search(self, text, k, filters=None):
        self.ft_filters.append(filters)
        return [{"id": "d1#0", "text": "본문", "score": 1.0}]

    def vector_search(self, embedding, k, filters=None):
        self.vec_filters.append(filters)
        return [{"id": "d1#0", "text": "본문", "distance": 0.1}]


def test_search_pushes_owner_into_both_chunk_arms():
    s = _ChunkSpy()
    q.search("hello", owner=OWNER, store=s, query_embedding=[0.1] * 8)
    assert s.ft_filters and s.ft_filters[0].get("owner_id") == OWNER, s.ft_filters
    assert s.vec_filters and s.vec_filters[0].get("owner_id") == OWNER, s.vec_filters


def test_search_keeps_existing_filters_alongside_owner():
    """layer·doc_id 푸시다운(E3)과 공존해야 한다 — owner 가 덮어쓰면 안 된다."""
    s = _ChunkSpy()
    q.search("hello", owner=OWNER, store=s, filters={"layer": "plan"})
    f = s.ft_filters[0]
    assert f.get("owner_id") == OWNER and f.get("layer") == "plan", f


def test_search_stays_global_without_owner():
    """로컬 단일 사용자는 기존 전역 동작 유지 — owner_id 를 넣지 않는다."""
    s = _ChunkSpy()
    q.search("hello", store=s)
    assert "owner_id" not in (s.ft_filters[0] or {}), s.ft_filters


def test_search_refuses_chunk_arms_when_store_cannot_scope():
    """fail-closed — 스코프를 보장 못 하는 스토어에는 청크 팔을 아예 안 돌린다.

    조용히 무스코프로 답하는 것이 정확히 이 결함이었다. 답을 줄이고 사유를 남긴다."""

    class _Unscoped(_ChunkSpy):
        scoped_chunk_search = False

    s = _Unscoped()
    res = q.search("hello", owner=OWNER, store=s)
    assert not s.ft_filters, "무스코프 스토어에 청크 검색을 돌렸다"
    assert not res.get("rows"), res.get("rows")
    assert str(res.get("error") or "").startswith("owner_scope_unsupported"), res
    assert "node_hits" in res      # owner 로 이미 걸러진 노드 폴백은 계속 답한다


# ── 2) 전송 층: MCP 도구가 owner 를 실어 보내는가 ────────────────────────────
def test_every_read_tool_sends_owner_to_sidecar():
    """엔진이 옳아도 도구가 owner 를 안 보내면 스코프가 없다(실제 결함이었음)."""
    b = _StubBridge()
    cases = [
        ("vinc_search", {"query": "x"}),
        ("vinc_decide", {"topic": "x"}),
        ("vinc_graph", {"seed_id": "concept:x"}),
        ("vinc_recall", {}),
        ("vinc_domains", {}),
        ("vinc_suggest_ties", {"node": "concept:x"}),
    ]
    for name, args in cases:
        b.calls.clear()
        tools.call(name, args, b, forced_owner=OWNER)
        params = b.calls[0][2] or {}
        assert params.get("owner") == OWNER, f"{name} 이 owner 를 안 보냄"


# ── SEC-2: share/export 경로도 owner 를 건다 ─────────────────────────────────
# build_share_pack 은 S1 동기화 export 의 토대다 — 여기가 새면 남의 지식이 팩에 실려
# 원격으로 나간다. 예전엔 ids/seed 스코프가 owner 를 받고도 술어에 안 썼다(SEC-1 동류).
def test_share_concepts_plan_always_scopes_owner():
    from api import share

    for kw in ({}, {"domain": "d"}, {"ids": ["concept:x"]}):
        p = share._concepts_plan(owner=OWNER, **kw)
        assert "c.owner_id = $owner" in p.cypher, kw
        assert p.params["owner"] == OWNER, kw


def test_share_episodes_plan_scopes_owner():
    from api import share

    p = share._episodes_plan(owner=OWNER)
    assert "e.owner_id = $owner" in p.cypher
    assert p.params["owner"] == OWNER


def test_ingest_write_carries_forced_owner():
    b = _StubBridge()
    tools.call("vinc_ingest", {"nodes": [{"id": "c:x", "type": "concept"}],
                               "owner": "user:victim"}, b, forced_owner=OWNER)
    assert b.calls[0][3]["owner"] == OWNER

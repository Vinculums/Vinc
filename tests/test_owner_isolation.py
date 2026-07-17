# -*- coding: utf-8 -*-
"""SEC-1 — owner 격리 회귀. 호스티드 멀티테넌트의 핵심 보안 속성.

**왜 이 테스트가 이렇게 생겼나**: 기존 E2E(test_mcp_auth)는 *스텁 브리지*로
"사이드카가 owner=user:u1 을 받았다"까지만 봤다. 배관은 맞았지만 **엔진이 그 owner
를 버렸고**(seed 갈래·search·decide) 스텁은 그걸 볼 수 없었다. 그래서 여기서는
계약의 두 층을 실물로 본다:

  1. **플랜 층**: 생성된 Cypher 에 owner 술어가 실제로 박히는지(어디서든 실행 가능 —
     Windows 에서 임베디드 kuzu E2E 가 못 도는 환경에서도 회귀를 잡는다).
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
    for seed in ("concept:x", "episode:e1", "record:r1"):
        p = q.seed_lookup_plan(seed, owner=OWNER)
        assert "n.owner_id = $owner" in p.cypher, seed
    # Bridge 는 스키마에 owner_id 가 없다 — 필터하면 쿼리가 깨진다(알려진 한계).
    pb = q.seed_lookup_plan("bridge:b1", owner=OWNER)
    assert "owner_id" not in pb.cypher
    assert "owner" not in pb.params


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

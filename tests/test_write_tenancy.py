# -*- coding: utf-8 -*-
"""SEC-3 — 호스티드 쓰기 경로 owner 스코프 (교차테넌트 쓰기 봉쇄).

여기 있는 건 **배선/계약 층**(Windows 포함 어디서든 실행): 강제 owner 경로가
생성하는 Cypher 에 `owner_id = $owner` 술어가 실제로 박히는가, PK 충돌 소유권
탈취가 거부되는가를 캡처-목 스토어로 본다. 실엔진 2-owner 왕복(A 가 B 의 노드를
정말 못 지우는가)은 서버측 e2e/CI 가 담당한다 — Windows 임베디드 kùzu 부재
([[windows-embedded-test-fails]]). SEC-1 교훈: 스텁이 초록이어도 엔진이 다르게
굴 수 있으니 **계약(스코프 술어)을 직접** 검사한다.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import preference as pref   # noqa: E402
from api import query as q           # noqa: E402
from api import fragment as frag     # noqa: E402

A, B = "user:a", "user:b"


class CapturingStore:
    """store.run 을 기록하고, 쿼리 유형별로 그럴듯한 행을 돌려주는 목.

    responder(cypher, params) 를 주면 그 반환을 쓰고, 없으면 기본(카운트=1,
    프로젝션=owner 지정 행)을 돌려준다."""
    def __init__(self, responder=None, owner_id=A):
        self.calls: list[tuple[str, dict]] = []
        self.responder = responder
        self.owner_id = owner_id

    def run(self, cypher, params=None):
        self.calls.append((cypher, dict(params or {})))
        if self.responder is not None:
            r = self.responder(cypher, params or {})
            if r is not None:
                return r
        low = cypher.lower()
        if "count(" in low:
            return [{"c": 1}]
        if "return" in low and ("rev as rev" in low or "as rev" in low):
            row = {"rev": 0, "title": "x", "domain": "", "tags": [], "props": "",
                   "date": "", "summary": "", "owner_id": self.owner_id,
                   "val": None, "owner": self.owner_id}
            return [row]
        return []

    def cyphers(self) -> str:
        return "\n".join(c for c, _ in self.calls)


# ── preference plan: owner 스코프 술어(pin/tag/touch) ─────────────────────────
def test_pref_plans_scope_owner_when_given():
    for plan in (pref.pin_plan("episode:x", owner=A),
                 pref.tag_plan("episode:x", ["t"], owner=A),
                 pref.touch_plan("episode:x", owner=A)):
        assert "e.owner_id = $owner" in plan.cypher, plan.cypher
        assert plan.params.get("owner") == A


def test_pref_plans_unscoped_when_owner_none():
    """로컬(owner=None)은 현행 — owner 술어 없음, 단일유저 동작 보존."""
    for plan in (pref.pin_plan("episode:x"),
                 pref.tag_plan("episode:x", ["t"]),
                 pref.touch_plan("episode:x")):
        assert "owner_id" not in plan.cypher
        assert "owner" not in plan.params


# ── delete_node: owner 스코프 + Bridge 거부 ──────────────────────────────────
def test_delete_scopes_owner_in_every_statement():
    s = CapturingStore()
    q.delete_node("concept:x", owner=A, store=s)
    body = s.cyphers()
    # 존재확인·차수·DETACH DELETE 전부 owner 스코프
    assert body.count("n.owner_id = $owner") >= 2
    assert "DETACH DELETE" in body
    for _, p in s.calls:
        if "owner" in [k for k in p]:
            assert p["owner"] == A


def test_delete_unscoped_when_owner_none():
    s = CapturingStore()
    q.delete_node("concept:x", store=s)
    # 스코프 술어(owner_id = $owner)는 없어야 한다 — tombstone 의 owner_id 조회(AS owner)는 무관.
    assert "owner_id = $owner" not in s.cyphers()


def test_delete_refuses_non_owner_scoped_label_under_forced_owner():
    """Bridge 는 owner_id 없음 → 강제 owner 경로에서 삭제 거부(fail-closed)."""
    s = CapturingStore()
    out = q.delete_node("bridge:x", owner=A, store=s)
    assert out["found"] is False
    assert "refused" in (out.get("note") or "")
    assert "DETACH DELETE" not in s.cyphers()   # 아무것도 안 지웠다


# ── merge_nodes: _exists 게이트가 owner 스코프 ───────────────────────────────
def test_merge_exists_gate_scopes_owner():
    s = CapturingStore()
    q.merge_nodes("concept:keep", "concept:drop", owner=A, store=s)
    # 최초 _exists 쿼리(keep/drop 존재확인)에 owner 술어가 있어야 한다
    first = s.calls[0][0]
    assert "c.owner_id = $owner" in first, first
    assert s.calls[0][1].get("owner") == A


def test_merge_aborts_when_node_not_owned():
    """keep/drop 이 호출자 소유 아니면(_exists 카운트 0) found:False, 쓰기 0."""
    def responder(cypher, params):
        if "count(" in cypher.lower():
            return [{"c": 0}]   # 소유 노드 없음
        return None
    s = CapturingStore(responder=responder)
    out = q.merge_nodes("concept:keep", "concept:drop", owner=A, store=s)
    assert out.get("found") is False
    assert "DETACH DELETE" not in s.cyphers()


# ── merge_fragment: PK 충돌 소유권 탈취 거부 ─────────────────────────────────
def test_ingest_refuses_cross_tenant_node_takeover():
    """A 가 B 소유 concept:x 를 강제 owner 로 적재 → 재스탬핑/덮어쓰기 거부(스킵)."""
    def responder(cypher, params):
        # Concept 존재확인 프로젝션 → B 소유로 응답
        if "as rev" in cypher.lower() and "concept" in cypher.lower():
            return [{"rev": 0, "title": "b-owned", "domain": "", "tags": [],
                     "props": "", "owner_id": B}]
        return None
    s = CapturingStore(responder=responder, owner_id=B)
    out = frag.merge_fragment(
        {"owner": A, "nodes": [{"id": "concept:x", "type": "concept", "title": "hijack"}],
         "edges": []}, store=s)
    # MERGE 가 실행되지 않았다(SET/덮어쓰기 없음) + 경고
    assert "MERGE (n:Concept" not in s.cyphers()
    assert any("another tenant" in w for w in out.get("warnings", []))


def test_ingest_stamps_own_new_node():
    """자기 소유 신규 노드는 정상 적재(owner_explicit=True, 미존재 → 생성)."""
    def responder(cypher, params):
        if "as rev" in cypher.lower():
            return []   # 미존재
        return None
    s = CapturingStore(responder=responder)
    frag.merge_fragment(
        {"owner": A, "nodes": [{"id": "concept:new", "type": "concept", "title": "mine"}],
         "edges": []}, store=s)
    body = s.cyphers()
    assert "MERGE (n:Concept" in body   # 생성됨
    # owner_id 스탬핑 params 에 A
    assert any(p.get("owner_id") == A for _, p in s.calls)


# ── merge_fragment 엣지: owner_explicit 이면 끝점 owner 스코프 ────────────────
def test_ingest_edge_scopes_endpoints_under_forced_owner():
    def responder(cypher, params):
        if "as rev" in cypher.lower():
            return []   # 노드는 신규로 통과
        return None
    s = CapturingStore(responder=responder)
    frag.merge_fragment({
        "owner": A,
        "nodes": [{"id": "concept:s", "type": "concept"},
                  {"id": "concept:d", "type": "concept"}],
        "edges": [{"from": "concept:s", "to": "concept:d", "type": "relates_to"}],
    }, store=s)
    edge_cyphers = [c for c, _ in s.calls if "RELATED_TO" in c or "relates" in c.lower()]
    joined = "\n".join(edge_cyphers)
    assert "a.owner_id = $owner" in joined and "b.owner_id = $owner" in joined, joined

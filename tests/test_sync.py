# -*- coding: utf-8 -*-
"""vinc_sync S1 — delta export 스코프·청킹·LWW 가드.

여기 있는 건 **순수/플랜 층**(어디서든 실행 가능). 실엔진 2-스토어 왕복은
서버측 e2e 가 담당한다 — Windows 로컬은 임베디드 kuzu 가 못 돌기 때문
([[windows-embedded-test-fails]]). 그래서 이 파일은 "스코프 술어가 실제로
박히는가 / 순수 로직이 맞는가"를 본다 — SEC-1 에서 배운 대로, 스텁 왕복이
초록이어도 엔진이 다르게 굴 수 있으니 **계약을 직접** 검사한다.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import sync  # noqa: E402

OWNER = "user:alice"


# ── 스코프: 오프라인 우선 약속이 술어로 박히는가 ──────────────────────────────
def test_delta_plans_scope_owner_and_selected_domains():
    for plan in (sync._concepts_delta_plan(owner=OWNER, domains=["work"], since=0),
                 sync._episodes_delta_plan(owner=OWNER, domains=["work"], since=0)):
        assert "owner_id = $owner" in plan.cypher
        assert "domain IN $domains" in plan.cypher     # 안 고른 도메인은 닿지 않는다
        assert plan.params["owner"] == OWNER
        assert plan.params["domains"] == ["work"]


def test_since_zero_is_full_export_not_delta():
    """최초 동기화: S0 마이그레이션이 기존 노드를 sync_seq=0 으로 백필하므로
    `sync_seq > 0` 은 그것들을 놓친다 → since=0 이면 전량."""
    p0 = sync._concepts_delta_plan(owner=OWNER, domains=["d"], since=0)
    assert "sync_seq >" not in p0.cypher and "since" not in p0.params
    p1 = sync._concepts_delta_plan(owner=OWNER, domains=["d"], since=5)
    assert "c.sync_seq > $since" in p1.cypher and p1.params["since"] == 5


def test_export_with_no_selection_emits_nothing():
    """선택 도메인이 없으면 스토어를 건드리지도 않고 빈 번들 — 기기를 안 떠난다."""
    class _NoStore:
        def run(self, *a, **k):
            raise AssertionError("선택이 없으면 스토어를 조회하면 안 된다")

    out = sync.export_delta(owner=OWNER, since=0, domains=[], store=_NoStore())
    assert out["executed"] and out["nodes"] == [] and "no domains selected" in out["note"]


# ── LWW / 에코 차단 ───────────────────────────────────────────────────────────
def _n(ua: str, rev: int) -> dict:
    return {"id": "concept:x", "type": "concept", "_updated_at": ua, "_rev": rev}


def test_stale_guard_is_lww_with_echo_break():
    older, newer = "2026-01-01T00:00:00.000Z", "2026-06-01T00:00:00.000Z"
    # 저장본이 더 최신 → 인바운드 무시(늦게 온 옛 변경이 최신을 못 덮는다)
    assert sync._is_stale({"updated_at": newer, "rev": 2}, _n(older, 1)) is True
    # 인바운드가 더 최신 → 적용
    assert sync._is_stale({"updated_at": older, "rev": 1}, _n(newer, 2)) is False
    # 같은 버전이 되돌아옴 → 무연산(에코 차단). `>` 가 아니라 `>=` 인 이유.
    assert sync._is_stale({"updated_at": newer, "rev": 2}, _n(newer, 2)) is True
    # updated_at 동률 → rev 로 결정
    assert sync._is_stale({"updated_at": newer, "rev": 1}, _n(newer, 2)) is False
    # 저장본 없음 → 신규 적용
    assert sync._is_stale(None, _n(older, 1)) is False


# ── 청킹: 자기완결(엣지 양끝 노드 동봉) ───────────────────────────────────────
def _node(i: int, pad: int = 0) -> dict:
    return {"id": f"concept:{i}", "type": "concept", "title": "x" * pad, "_seq": i}


def test_chunks_are_self_contained_for_edges():
    """fragment 는 **같은 payload 의 노드로만** 엣지 (from,to) 타입을 해석한다 —
    끝점이 빠진 엣지는 수신측에서 조용히 skip 되므로 청크는 자기완결이어야 한다."""
    nodes = [_node(i, pad=200) for i in range(6)]
    edges = [{"from": "concept:0", "to": "concept:5", "type": "relates_to"}]
    chunks = sync.chunk_bundle({"nodes": nodes, "edges": edges}, limit=600)
    assert len(chunks) > 1                                   # 실제로 쪼개짐
    for ch in chunks:
        have = {n["id"] for n in ch["nodes"]}
        for e in ch["edges"]:
            assert e["from"] in have and e["to"] in have, "엣지 끝점이 청크에 없다"
    # 갈라진 엣지도 유실되지 않는다(양끝 동봉 청크로 회수)
    carried = [e for ch in chunks for e in ch["edges"]]
    assert carried == edges


def test_small_bundle_stays_single_chunk():
    nodes = [_node(i) for i in range(3)]
    edges = [{"from": "concept:0", "to": "concept:1", "type": "relates_to"}]
    chunks = sync.chunk_bundle({"nodes": nodes, "edges": edges})
    assert len(chunks) == 1
    assert len(chunks[0]["nodes"]) == 3 and chunks[0]["edges"] == edges


# ── 선택 도메인 저장 ──────────────────────────────────────────────────────────
def test_selected_domains_roundtrip_and_normalize():
    class _Mem:
        def __init__(self):
            self.kv = {}

        def run(self, cypher, params=None):
            p = params or {}
            if cypher.startswith("MERGE (m:_SyncMeta"):
                self.kv[p["k"]] = p["v"]
                return []
            return [{"v": self.kv.get(p.get("k"), "")}]

    m = _Mem()
    assert sync.get_selected_domains(m) == []
    assert sync.set_selected_domains(m, ["b", "a", "a", " "]) == ["a", "b"]  # 정규화·중복제거
    assert sync.get_selected_domains(m) == ["a", "b"]

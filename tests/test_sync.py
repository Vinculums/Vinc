# -*- coding: utf-8 -*-
"""vinc_sync S1 — delta export 스코프·청킹·LWW 가드.

여기 있는 건 **순수/플랜 층**(어디서든 실행 가능). 실엔진 2-스토어 왕복은
서버측 e2e 가 담당한다 — Windows 로컬은 임베디드 드라이버(ladybug)가 못 돌기 때문
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


# ── F4: ABOUT(Episode→Concept) 엣지 동기화 커버리지 ──────────────────────────
def test_about_edges_covered_by_rel_map_and_plan():
    """_REL_TO_FRAGMENT 에 ABOUT 편입 + 전용 플랜(Episode 소스는 Concept 소스 전용
    graph_rows_for_ids_plan 이 못 본다)의 스코프 술어."""
    assert sync._REL_TO_FRAGMENT["ABOUT"] == "about"
    assert sync._REL_TO_FRAGMENT["RELATED_TO"] == "relates_to"   # 기존 역맵 불변
    p = sync._extra_edges_plan(["episode:e1", "concept:c1"], owner=OWNER,
                               pair=("Episode", "episode_id", "Concept", "concept_id"))
    assert "MATCH (a:Episode)-[r]->(b:Concept)" in p.cypher
    assert "a.episode_id IN $ids" in p.cypher
    assert "b.concept_id IN $ids" in p.cypher
    assert "a.owner_id = $owner" in p.cypher
    assert "b.owner_id = $owner" in p.cypher
    # v12 — 결정이 낀 조합도 커버돼야 한다. 빠지면 그 엣지가 **오류 없이** 기기
    # 간에 사라진다(Concept 소스 전용 플랜이 못 보는 자리라 아무도 안 알려 준다).
    assert ("Decision", "decision_id", "Concept", "concept_id") in sync._EXTRA_EDGE_PAIRS
    assert ("Concept", "concept_id", "Decision", "decision_id") in sync._EXTRA_EDGE_PAIRS


def test_export_carries_about_edges_when_both_endpoints_in_bundle():
    """export_delta — Episode·Concept 이 같은 번들에 있을 때만 about 엣지 동봉."""
    class _SubFake:
        def __init__(self, responses):
            self.responses = responses
        def run(self, cypher, params=None):
            for key, rows in self.responses.items():
                if key in cypher:
                    return rows
            return []
        def close(self):
            pass

    fake = _SubFake({
        "MATCH (c:Concept) WHERE": [
            {"id": "concept:c1", "title": "C1", "domain": "d", "sync_seq": 1}],
        "MATCH (e:Episode) WHERE": [
            {"id": "episode:e1", "title": "E1", "domain": "d", "sync_seq": 2}],
        # v12 — 전용 플랜이 rel 을 좁히지 않고 label(r) 로 뽑는다(끝점 조합별 1플랜).
        "MATCH (a:Episode)-[r]->(b:Concept)": [
            {"id": "episode:e1", "rel": "ABOUT", "target": "concept:c1"},
            # 번들 밖 끝점 — 실리면 안 된다(수신측 fragment 가 타입 해석 불가)
            {"id": "episode:ghost", "rel": "ABOUT", "target": "concept:c1"}],
    })
    out = sync.export_delta(owner=OWNER, since=0, domains=["d"], store=fake)
    assert out["executed"]
    assert {"from": "episode:e1", "to": "concept:c1", "type": "about"} in out["edges"]
    assert all(e["from"] != "episode:ghost" for e in out["edges"])


# ── S4 확장: 엣지 툼스톤(삭제 전파) ───────────────────────────────────────────
def test_edge_tombstone_plan_scopes_owner_and_rels():
    p = sync._edge_tombstones_delta_plan(owner=OWNER, since=0)
    assert "t.owner_id = $owner" in p.cypher
    assert "ABOUT" in p.cypher and "RELATED_TO" in p.cypher   # 동기화 rel 만
    assert "since" not in p.params                            # since=0 = 전량
    p5 = sync._edge_tombstones_delta_plan(owner=OWNER, since=5)
    assert "t.sync_seq > $since" in p5.cypher and p5.params["since"] == 5


class _SubFake:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []
    def run(self, cypher, params=None):
        self.calls.append((cypher, dict(params or {})))
        for key, rows in self.responses.items():
            if key in cypher:
                return rows
        return []
    def close(self):
        pass


def test_export_carries_edge_tombstones_and_advances_cursor():
    fake = _SubFake({
        "MATCH (t:_EdgeTombstone)": [
            {"src": "concept:a", "rel": "RELATED_TO", "dst": "concept:b",
             "deleted_at": "2026-07-19T00:00:00.000Z", "sync_seq": 7}],
    })
    out = sync.export_delta(owner=OWNER, since=0, domains=["d"], store=fake)
    assert out["executed"]
    assert out["edge_tombstones"] == [
        {"src": "concept:a", "rel": "RELATED_TO", "dst": "concept:b",
         "deleted_at": "2026-07-19T00:00:00.000Z", "_seq": 7}]
    assert out["next_cursor"] == 7                            # 커서가 엣지 표식도 소비
    assert out["counts"]["edge_tombstones"] == 1


def test_chunks_carry_edge_tombstones_once():
    ch = sync.chunk_bundle({"nodes": [_node(0)], "edges": [],
                            "edge_tombstones": [{"src": "a", "rel": "ABOUT", "dst": "b"}]})
    assert ch[0]["edge_tombstones"] == [{"src": "a", "rel": "ABOUT", "dst": "b"}]
    only = sync.chunk_bundle({"nodes": [], "edges": [],
                              "edge_tombstones": [{"src": "a", "rel": "ABOUT", "dst": "b"}]})
    assert len(only) == 1 and only[0]["edge_tombstones"]      # 삭제-only 번들도 전송


def test_apply_deletes_edge_and_repropagates():
    """엣지 툼스톤 적용: RELATED_TO 는 양방향 삭제, 수신측 자기 표식(origin remote)
    기록으로 다른 기기 재전파."""
    fake = _SubFake({"RETURN count(r) AS c": [{"c": 1}]})
    out = sync.apply_delta(owner=OWNER, bundle={
        "nodes": [], "edges": [],
        "edge_tombstones": [{"src": "concept:a", "rel": "RELATED_TO",
                             "dst": "concept:b", "deleted_at": "2026-07-19T00:00:00.000Z"}],
    }, store=fake)
    assert out["edges_deleted"] == 2                          # 정+역방향
    et = [(c, p) for c, p in fake.calls if "MERGE (t:_EdgeTombstone" in c]
    assert et, "재전파용 자기 표식이 기록돼야 한다"
    assert et[0][1]["da"] == "2026-07-19T00:00:00.000Z"       # 원 삭제 시각 보존


# ── LWW / 에코 차단 ───────────────────────────────────────────────────────────
def _n(ua: str, rev: int) -> dict:
    return {"id": "concept:x", "type": "concept", "_updated_at": ua, "_rev": rev}


def test_node_tiebreak_updated_at_then_rev_then_id():
    """노드 승자 판정(3-way 충돌 필드에 적용) — updated_at→rev→id."""
    from api import syncmerge
    older, newer = "2026-01-01T00:00:00.000Z", "2026-06-01T00:00:00.000Z"
    # 원격이 더 최신 → True
    assert syncmerge.node_remote_newer(1, older, 2, newer, "c:x") is True
    # 로컬이 더 최신 → False
    assert syncmerge.node_remote_newer(2, newer, 1, older, "c:x") is False
    # updated_at 동률 → rev 로
    assert syncmerge.node_remote_newer(1, newer, 2, newer, "c:x") is True
    # updated_at·rev 동률 → id 결정론(remote_id 미지정이면 local_id 로 False)
    assert syncmerge.node_remote_newer(2, newer, 2, newer, "c:x") is False


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


# ── 삭제 전파(S4) ─────────────────────────────────────────────────────────────
def test_chunking_carries_tombstones():
    """삭제 표식은 유실되면 안 된다 — 노드 있으면 첫 청크에, 없으면 전용 청크에."""
    tombs = [{"node_id": "concept:x", "label": "Concept", "deleted_at": "2026", "_seq": 3}]
    # 노드 있음 → 첫 청크에 실림
    ch = sync.chunk_bundle({"nodes": [_node(0)], "edges": [], "tombstones": tombs})
    assert ch[0]["tombstones"] == tombs
    # 삭제-only(노드 0) → 전용 청크
    ch2 = sync.chunk_bundle({"nodes": [], "edges": [], "tombstones": tombs})
    assert len(ch2) == 1 and ch2[0]["tombstones"] == tombs
    # 완전 빈 번들 → 청크 없음
    assert sync.chunk_bundle({"nodes": [], "edges": [], "tombstones": []}) == []


def test_tombstone_plan_scopes_owner_and_synced_labels():
    p = sync._tombstones_delta_plan(owner="user:a", since=0)
    assert "t.owner_id = $owner" in p.cypher
    # 동기화 라벨만. 목록은 _SYNC_TYPES 에서 파생하므로 파라미터로 실린다 —
    # 라벨을 손으로 두 벌 적으면 어긋난다(v12 Decision 이 그 자리였다).
    assert "t.label IN $labels" in p.cypher
    assert p.params["labels"] == ["Concept", "Episode", "Decision"]
    assert "sync_seq >" not in p.cypher                       # since=0 = 전량
    p1 = sync._tombstones_delta_plan(owner="user:a", since=7)
    assert "t.sync_seq > $since" in p1.cypher and p1.params["since"] == 7


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

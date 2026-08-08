# -*- coding: utf-8 -*-
"""Atlas(D16·D17)·fragment(D11 §5) 계약 테스트 — 무 DB(플랜 순수성) + FakeStore."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import query as q            # noqa: E402
from api import fragment as frag      # noqa: E402


class _FakeStore:
    """run() 호출을 기록하고 미리 정의된 응답을 돌려주는 스토어 더블."""

    def __init__(self, responses: dict | None = None):
        self.calls: list[tuple[str, dict]] = []
        self.responses = responses or {}

    def run(self, cypher: str, params: dict | None = None):
        self.calls.append((cypher, dict(params or {})))
        for key, rows in self.responses.items():
            if key in cypher:
                return rows
        return []

    def close(self):
        pass


class SubgraphPlanTests(unittest.TestCase):
    def test_seed_traversal_uses_shortest_reach_semantics(self):
        # G1(2026-08-03): 이 플랜은 *도달 집합*을 원한다 — 경로 열거(TRAIL)가 아니라
        # SHORTEST 로 같은 집합을 더 싸게 낸다(실측 2.5~3x). WALK 형식은 남지 않아야
        # 한다 — 기본 WALK 는 사이클서 경로가 폭증한다(M3 §6-2 가 막던 것).
        plan = q.subgraph_ids_plan("concept:fourier", depth=2)
        self.assertIn("SHORTEST 1..2", plan.cypher)
        self.assertNotIn("TRAIL", plan.cypher)
        self.assertNotIn("*1..", plan.cypher)
        self.assertEqual(plan.params, {"seed": "concept:fourier"})

    def test_depth_clamped_1_to_4(self):
        self.assertIn("1..1", q.subgraph_ids_plan("x", depth=0).cypher)
        self.assertIn("1..4", q.subgraph_ids_plan("x", depth=9).cypher)

    def test_graph_without_db_returns_plan_only(self):
        res = q.graph(owner="user:t", seed="concept:x", store=None)
        # open_store 가 드라이버 부재로 None 이면 executed=False 계약 유지.
        if not res.get("executed"):
            self.assertEqual(res["nodes"], [])
            self.assertEqual(res["edges"], [])

    def test_graph_seed_prunes_ego_boundary(self):
        # 응답 키는 graph_rows_for_ids_plan 고유 substring — 축 플랜(F2)도 "IN $ids"
        # 를 포함하므로 범용 키는 더블 충돌을 낳는다(축 플랜은 무응답 → 빈 행).
        fake = _FakeStore({
            "DISTINCT m.concept_id": [{"id": "b"}],
            "OPTIONAL MATCH (c)-[r]->(d:Concept)": [
                {"id": "a", "label": "A", "domain": "math",
                 "rel": "RELATED_TO", "target": "b", "target_domain": "physics"},
                # depth+1 유출 엣지 — target 'z' 는 id 집합 밖 → shape 후 제거돼야 함
                {"id": "b", "label": "B", "domain": "physics",
                 "rel": "RELATED_TO", "target": "z", "target_domain": "art"},
            ],
        })
        res = q.graph(owner="user:t", seed="a", depth=1, store=fake)
        self.assertTrue(res["executed"])
        ids = {n["id"] for n in res["nodes"]}
        self.assertEqual(ids, {"a", "b"})                  # z 는 ego 경계 밖
        self.assertEqual(len(res["edges"]), 1)
        self.assertTrue(all("x" in n and "y" in n and "z" in n for n in res["nodes"]))  # D17 3D 좌표

    def test_graph_seed_renders_bridge_glue(self):
        # F2 — '보이지 않는 접착제' 회귀: kept 개념에 닿는 브릿지가 매개자 노드 +
        # 양팔 엣지로 렌더되고 keep 에 편입된다. F3 — 팔이 degree 에 반영된다.
        fake = _FakeStore({
            "DISTINCT m.concept_id": [{"id": "b"}],
            "OPTIONAL MATCH (c)-[r]->(d:Concept)": [
                {"id": "a", "label": "A", "domain": "math"},
                {"id": "b", "label": "B", "domain": "physics"},
            ],
            "MATCH (b:Bridge)-[r:CONNECTS]->(c:Concept)": [
                {"id": "bridge:x", "label": "X", "domain": "math", "domain2": "physics",
                 "kind": "bridge", "rel": "CONNECTS", "role": "math_input",
                 "target": "a", "target_domain": "math", "target_kind": "concept"},
                {"id": "bridge:x", "label": "X", "domain": "math", "domain2": "physics",
                 "kind": "bridge", "rel": "CONNECTS", "role": "physics_input",
                 "target": "b", "target_domain": "physics", "target_kind": "concept"},
            ],
        })
        res = q.graph(owner="user:t", seed="a", depth=2, store=fake)
        by_id = {n["id"]: n for n in res["nodes"]}
        self.assertIn("bridge:x", by_id)                    # 매개자 keep 편입
        self.assertEqual(by_id["bridge:x"]["kind"], "bridge")
        self.assertEqual(by_id["bridge:x"].get("domain2"), "physics")
        arms = [e for e in res["edges"] if e["type"] == "CONNECTS"]
        self.assertEqual(len(arms), 2)                      # 양팔 렌더(글루 가시화)
        self.assertEqual({e.get("role") for e in arms}, {"math_input", "physics_input"})
        self.assertEqual(by_id["a"]["degree"], 1)           # F3 — 브릿지 팔 카운트

    def test_entity_seed_uses_typed_traversal(self):
        # F1 — entity: 시드는 전용 순회(TYPED 폐쇄 성분) + r.type 을 rel 로 투영.
        fake = _FakeStore({
            "DISTINCT m.entity_id": [{"id": "entity:e2"}],
            "OPTIONAL MATCH (e)-[r:TYPED]->(d:Entity)": [
                {"id": "entity:e1", "label": "E1", "domain": "", "kind": "entity",
                 "rel": "influences", "target": "entity:e2", "target_domain": "",
                 "target_label": "E2", "target_kind": "entity"},
            ],
        })
        res = q.graph(owner="user:t", seed="entity:e1", depth=2, store=fake)
        self.assertTrue(res["executed"])
        kinds = {n["id"]: n["kind"] for n in res["nodes"]}
        self.assertEqual(kinds, {"entity:e1": "entity", "entity:e2": "entity"})
        self.assertEqual(res["edges"][0]["type"], "influences")   # 캐리어명 아닌 사용자 타입


class LayoutTests(unittest.TestCase):
    def _nodes(self):
        return [{"id": "a", "domain": "math", "degree": 2},
                {"id": "b", "domain": "math", "degree": 5},
                {"id": "c", "domain": "art", "degree": 0}]

    def test_deterministic(self):
        n1 = q.layout_positions(self._nodes(), [])
        n2 = q.layout_positions(self._nodes(), [])
        self.assertEqual([(n["x"], n["y"], n["z"]) for n in n1],
                         [(n["x"], n["y"], n["z"]) for n in n2])   # 순수 함수(D17·3D)

    def test_domains_form_separate_clusters(self):
        nodes = q.layout_positions(self._nodes(), [])
        by_id = {n["id"]: n for n in nodes}
        # 허브(b, degree 5)가 math 클러스터 중심(반경 0)에 온다.
        math_center = (by_id["b"]["x"], by_id["b"]["y"])
        art_center = (by_id["c"]["x"], by_id["c"]["y"])
        self.assertNotEqual(math_center, art_center)


class AtlasL0Tests(unittest.TestCase):
    def test_ties_plan_counts_cross_domain_pairs(self):
        plan = q.domain_ties_plan(owner="user:t")
        self.assertIn("a.domain <> b.domain", plan.cypher)
        self.assertIn("count(*)", plan.cypher)

    def test_bridge_ties_plan_scopes_owner_with_legacy_transition(self):
        # F5 — owner 지정 시 과도기 술어(내 것 + 레거시 ''), 미지정(로컬)은 글로벌.
        plan = q.bridge_ties_plan(owner="user:t")
        self.assertIn("(b.owner_id = $owner OR b.owner_id = '')", plan.cypher)
        self.assertEqual(plan.params, {"owner": "user:t"})
        self.assertNotIn("$owner", q.bridge_ties_plan().cypher)

    def test_atlas_l0_combines_domains_and_ties(self):
        fake = _FakeStore({
            "count(*) AS episodes": [{"domain": "math", "episodes": 3}],
            "a.domain <> b.domain": [{"source": "math", "target": "art", "ties": 2}],
            # Bridge 축(실데이터에서 크로스도메인은 :Bridge 가 1급 — 합산돼야 함)
            "MATCH (b:Bridge)": [{"source": "math", "target": "physics", "ties": 5}],
        })
        res = q.atlas_l0(owner="user:t", store=fake)
        self.assertTrue(res["executed"])
        self.assertEqual(res["domains"][0]["domain"], "math")
        self.assertEqual(res["ties"],
                         [{"source": "math", "target": "physics", "ties": 5},
                          {"source": "math", "target": "art", "ties": 2}])


class FragmentMergeTests(unittest.TestCase):
    def test_merges_supported_nodes_and_edges(self):
        fake = _FakeStore()   # count 미응답 → 전부 created 경로
        res = frag.merge_fragment({
            "nodes": [
                {"id": "concept:margiela", "type": "concept", "title": "Maison Margiela",
                 "domain": "fashion", "tags": ["deconstruction"], "props": {"note": "x"}},
                {"id": "concept:deconstruction", "type": "concept"},
                {"id": "record:r1", "type": "record", "title": "R1", "props": {"k": 1}},
            ],
            "edges": [
                {"from": "concept:margiela", "to": "concept:deconstruction", "type": "relates_to"},
                {"from": "concept:margiela", "to": "concept:deconstruction", "type": "inherits_from"},
                {"from": "record:r1", "to": "concept:margiela", "type": "relates_to"},
            ],
        }, store=fake)
        self.assertTrue(res["executed"])
        self.assertEqual(res["merged"]["nodes"]["created"], 3)
        self.assertEqual(res["merged"]["edges"]["created"], 3)
        self.assertEqual(res["warnings"], [])
        joined = "\n".join(c for c, _ in fake.calls)
        self.assertIn("MERGE (n:Concept {concept_id: $id})", joined)
        self.assertIn("MERGE (n:Record {record_id: $id})", joined)
        self.assertIn(":INHERITS_FROM", joined)            # §3.2 공백 해소
        self.assertIn(":ATTACHES_TO", joined)              # record 참여 엣지
        self.assertIn('"note": "x"', joined + str([p for _, p in fake.calls]))

    def test_open_types_route_to_entity_carrier(self):
        # F1 — 미지 노드 타입은 skip 이 아니라 Entity 캐리어로 MERGE(정보 손실 0),
        # 양 끝이 개방 타입인 엣지는 TYPED{type} 캐리어로 간다.
        fake = _FakeStore()
        res = frag.merge_fragment({
            "nodes": [{"id": "policy:p1", "type": "policy", "title": "P1"},
                      {"id": "policy:p2", "type": "policy", "title": "P2"}],
            "edges": [{"from": "policy:p1", "to": "policy:p2", "type": "blocks"}],
        }, store=fake)
        self.assertTrue(res["executed"])
        self.assertEqual(res["merged"]["nodes"]["created"], 2)
        self.assertEqual(res["merged"]["edges"]["created"], 1)
        self.assertEqual(res["warnings"], [])
        joined = "\n".join(c for c, _ in fake.calls)
        self.assertIn("MERGE (n:Entity {entity_id: $id})", joined)
        self.assertIn("MERGE (a)-[:TYPED {type: $t}]->(b)", joined)   # type = 매칭 키 참여

    def test_entity_without_title_skips_with_warning(self):
        # 개방층의 skip 은 형태 오류뿐 — Entity 는 title 필수(휴면 엔진 동형).
        fake = _FakeStore()
        res = frag.merge_fragment({"nodes": [{"id": "x", "type": "policy"}]}, store=fake)
        self.assertEqual(res["merged"]["nodes"]["created"], 0)
        self.assertEqual(len(res["warnings"]), 1)
        self.assertTrue(res.get("partial"))

    def test_unsupported_pair_between_legacy_types_still_skips(self):
        # 레거시 노드 간 미지 엣지 = 토폴로지 한계(rel 테이블 고정) — 여전히 warn+skip.
        fake = _FakeStore()
        res = frag.merge_fragment({
            "nodes": [{"id": "episode:e1", "type": "episode", "title": "E"},
                      {"id": "record:r1", "type": "record", "title": "R"}],
            "edges": [{"from": "episode:e1", "to": "record:r1", "type": "blocks"}],
        }, store=fake)
        self.assertEqual(res["merged"]["edges"]["created"], 0)
        self.assertEqual(len(res["warnings"]), 1)

    def test_edge_to_undeclared_target_names_the_fix(self):
        # T4 — 스토어에 이미 있는 노드로 향하는 엣지: payload 에 대상이 없으면
        # '타입 불일치'가 아니라 '미선언'이고, warning 이 조치를 말해야 한다.
        # 종전엔 "edge 'relates_to' between concept→? 미지원" 으로 뭉개져(실측:
        # 17개 중 6개 조용히 skip) 관례 문서의 "id 재사용" 지침만 따르면 반드시
        # 이 함정에 걸렸다.
        fake = _FakeStore()
        res = frag.merge_fragment({
            "nodes": [{"id": "concept:a", "type": "concept", "title": "A"}],
            "edges": [{"from": "concept:a", "to": "concept:exists-in-store",
                       "type": "relates_to"}],
        }, store=fake)
        self.assertEqual(res["merged"]["edges"]["created"], 0)
        self.assertTrue(res.get("partial"))
        [w] = res["warnings"]
        self.assertIn("not declared in this call's nodes", w)
        self.assertIn("concept:exists-in-store", w, "어느 id 가 미해석인지 말해야 한다")
        self.assertIn("(id · type · title · domain)", w, "조치(최소 형태 동봉)가 없다")
        self.assertNotIn("미지원", w, "미선언이 타입 불일치 문구로 뭉개졌다")
        # 문구를 읽고 그대로 하면(대상을 nodes 에 동봉) 성공해야 한다.
        res2 = frag.merge_fragment({
            "nodes": [{"id": "concept:a", "type": "concept", "title": "A"},
                      {"id": "concept:exists-in-store", "type": "concept",
                       "title": "B", "domain": "d"}],
            "edges": [{"from": "concept:a", "to": "concept:exists-in-store",
                       "type": "relates_to"}],
        }, store=_FakeStore())
        self.assertEqual(res2["merged"]["edges"]["created"], 1)
        self.assertEqual(res2["warnings"], [])

    def test_declared_pair_mismatch_keeps_the_pair_message(self):
        # 양 끝이 선언됐는데 쌍이 rel 테이블 밖이면 여전히 '미지원'(진짜 토폴로지
        # 한계) — 두 사유는 다른 문구여야 한다(T4 의 요지).
        fake = _FakeStore()
        res = frag.merge_fragment({
            "nodes": [{"id": "episode:e1", "type": "episode", "title": "E"},
                      {"id": "record:r1", "type": "record", "title": "R"}],
            "edges": [{"from": "record:r1", "to": "episode:e1", "type": "relates_to"}],
        }, store=fake)
        self.assertEqual(res["merged"]["edges"]["created"], 0)
        [w] = res["warnings"]
        self.assertIn("미지원", w)
        self.assertIn("record→episode", w)
        self.assertNotIn("not declared", w)

    def test_theme_fold_proposes_open_types(self):
        # F1 — 어휘 밖 타입은 proposed 편입 + default 테마 lazy 생성·활성, Entity 행에
        # theme_id 각인. proposed_types 는 비어있지 않을 때만 응답에 실린다.
        from store.theme_store import ThemeStore
        themes = ThemeStore(None)   # 인메모리 강등
        fake = _FakeStore()
        res = frag.merge_fragment({
            "nodes": [{"id": "policy:p1", "type": "policy", "title": "P1"},
                      {"id": "policy:p2", "type": "policy", "title": "P2"}],
            "edges": [{"from": "policy:p1", "to": "policy:p2", "type": "blocks"}],
        }, store=fake, themes=themes)
        self.assertEqual(res["proposed_types"],
                         [{"kind": "node", "name": "policy"},
                          {"kind": "edge", "name": "blocks"}])
        self.assertEqual(themes.active_theme_id(), "default")
        ent = [p for c, p in fake.calls if "MERGE (n:Entity" in c]
        self.assertTrue(ent and all(p.get("theme_id") == "default" for p in ent))

    def test_no_proposed_types_field_without_open_types(self):
        fake = _FakeStore()
        res = frag.merge_fragment({"nodes": [{"id": "concept:a", "type": "concept"}]},
                                  store=fake)
        self.assertNotIn("proposed_types", res)

    def test_entity_sec3_skips_foreign_owned(self):
        # SEC-3 승계 — 타 테넌트 소유 Entity 에 강제 owner 경로 MERGE 는 skip.
        fake = _FakeStore({"n.rev AS rev": [
            {"rev": 1, "type": "policy", "title": "P", "tags": [],
             "theme_id": "", "owner_id": "user:bob"}]})
        res = frag.merge_fragment({
            "nodes": [{"id": "policy:p1", "type": "policy", "title": "P"}],
            "owner": "user:alice"}, store=fake)
        self.assertEqual(res["merged"]["nodes"], {"created": 0, "matched": 0})
        self.assertTrue(any("owned by another tenant" in w for w in res["warnings"]))

    def test_entity_rev_not_stamped_when_unchanged(self):
        # revstamp 승계 — 동일 내용 재적재는 무스탬프(가짜 충돌 방지).
        cur = {"rev": 3, "type": "policy", "title": "P1", "tags": [],
               "theme_id": "", "owner_id": ""}
        fake = _FakeStore({"n.rev AS rev": [cur]})
        frag.merge_fragment({"nodes": [{"id": "policy:p1", "type": "policy",
                                        "title": "P1"}]}, store=fake)
        merge_params = [p for c, p in fake.calls if c.startswith("MERGE (n:Entity")]
        self.assertTrue(merge_params)
        self.assertTrue(all("rev" not in p for p in merge_params))

    def test_about_edge_episode_to_concept(self):
        # F4 — about(Episode→Concept → ABOUT), 양 끝 owner 스코프.
        fake = _FakeStore()
        res = frag.merge_fragment({
            "nodes": [{"id": "episode:e1", "type": "episode", "title": "E1"},
                      {"id": "concept:c1", "type": "concept", "title": "C1"}],
            "edges": [{"from": "episode:e1", "to": "concept:c1", "type": "about"}],
            "owner": "user:t",
        }, store=fake)
        self.assertEqual(res["merged"]["edges"]["created"], 1)
        # E-G6 — MERGE 에 rel 변수(r) 도입(선택 evidence/confidence SET 용). evidence
        # 미제공이면 SET 절 없음.
        about = [c for c, _ in fake.calls if "MERGE (a)-[r:ABOUT]->(b)" in c]
        self.assertEqual(len(about), 1)
        self.assertIn("a.owner_id = $owner", about[0])
        self.assertIn("b.owner_id = $owner", about[0])

    def test_about_wrong_pair_skips(self):
        fake = _FakeStore()
        res = frag.merge_fragment({
            "nodes": [{"id": "concept:c1", "type": "concept"},
                      {"id": "concept:c2", "type": "concept"}],
            "edges": [{"from": "concept:c1", "to": "concept:c2", "type": "about"}],
        }, store=fake)
        self.assertEqual(res["merged"]["edges"]["created"], 0)
        self.assertEqual(len(res["warnings"]), 1)

    def test_connects_via_role_participates_in_merge(self):
        # F9 — role 지정 시 MERGE 패턴 참여(bridges_ingest 동형), 미지정은 bare MERGE.
        fake = _FakeStore()
        frag.merge_fragment({
            "nodes": [{"id": "bridge:b1", "type": "bridge", "title": "B"},
                      {"id": "concept:c1", "type": "concept", "title": "C"}],
            "edges": [{"from": "bridge:b1", "to": "concept:c1", "type": "connects_via",
                       "role": "math_input"}],
        }, store=fake)
        joined = "\n".join(c for c, _ in fake.calls)
        self.assertIn("MERGE (a)-[:CONNECTS {role: $role}]->(b)", joined)
        fake2 = _FakeStore()
        frag.merge_fragment({
            "nodes": [{"id": "bridge:b1", "type": "bridge", "title": "B"},
                      {"id": "concept:c1", "type": "concept", "title": "C"}],
            "edges": [{"from": "bridge:b1", "to": "concept:c1", "type": "connects_via"}],
        }, store=fake2)
        self.assertIn("MERGE (a)-[:CONNECTS]->(b)", "\n".join(c for c, _ in fake2.calls))

    def test_bridge_owner_stamped_and_edge_scoped(self):
        # F5 — Bridge 노드 owner 스탬프(생성/명시) + connects_via 의 Bridge 끝점 스코프.
        fake = _FakeStore()
        frag.merge_fragment({
            "nodes": [{"id": "bridge:b1", "type": "bridge", "title": "B"},
                      {"id": "concept:c1", "type": "concept", "title": "C"}],
            "edges": [{"from": "bridge:b1", "to": "concept:c1", "type": "connects_via"}],
            "owner": "user:t",
        }, store=fake)
        bridge_merge = [p for c, p in fake.calls if "MERGE (n:Bridge" in c]
        self.assertTrue(any(p.get("owner_id") == "user:t" for p in bridge_merge))
        cv = [c for c, _ in fake.calls if "MERGE (a)-[:CONNECTS]->(b)" in c][0]
        self.assertIn("a.owner_id = $owner", cv)

    def test_bridge_sec3_skips_foreign_owned(self):
        fake = _FakeStore({"n.rev AS rev": [
            {"rev": 1, "title": "B", "status": "", "source_domain": "",
             "target_domain": "", "props": "", "owner_id": "user:bob"}]})
        res = frag.merge_fragment({
            "nodes": [{"id": "bridge:b1", "type": "bridge", "title": "B"}],
            "owner": "user:alice"}, store=fake)
        self.assertEqual(res["merged"]["nodes"], {"created": 0, "matched": 0})
        self.assertTrue(any("owned by another tenant" in w for w in res["warnings"]))

    _STAMP = "SET n.updated_at = $ua, n.rev = $rev, n.sync_seq = $seq"

    def test_new_synced_edge_stamps_endpoints(self):
        # S4 확장 — 신규 엣지는 양 끝 노드를 revstamp(증분 동기화 전파) + 같은 키의
        # 로컬 엣지 툼스톤을 상쇄한다(재생성이 낡은 삭제를 이김).
        fake = _FakeStore({
            "n.rev AS rev, n.title": [],           # 노드 존재확인(신규)
            "RETURN n.rev AS rev": [{"rev": 0}],   # 끝점 스탬프 읽기
        })
        frag.merge_fragment({
            "nodes": [{"id": "concept:a", "type": "concept", "title": "A"},
                      {"id": "concept:b", "type": "concept", "title": "B"}],
            "edges": [{"from": "concept:a", "to": "concept:b", "type": "relates_to"}],
        }, store=fake)
        stamps = [c for c, _ in fake.calls if c.startswith("MATCH (n:Concept)")
                  and self._STAMP in c]
        self.assertEqual(len(stamps), 2)           # 양 끝점
        self.assertTrue(any("_EdgeTombstone" in c and "DELETE" in c
                            for c, _ in fake.calls))

    def test_existing_edge_does_not_stamp(self):
        fake = _FakeStore({
            "n.rev AS rev, n.title": [],
            "-[:RELATED_TO]->": [{"c": 1}],        # 정·역방향 존재확인 모두 히트
        })
        frag.merge_fragment({
            "nodes": [{"id": "concept:a", "type": "concept", "title": "A"},
                      {"id": "concept:b", "type": "concept", "title": "B"}],
            "edges": [{"from": "concept:a", "to": "concept:b", "type": "relates_to"}],
        }, store=fake)
        self.assertFalse(any(self._STAMP in c for c, _ in fake.calls
                             if c.startswith("MATCH (n:")))

    def test_sync_apply_path_suppresses_edge_stamping(self):
        fake = _FakeStore({
            "n.rev AS rev, n.title": [],
            "RETURN n.rev AS rev": [{"rev": 0}],
        })
        frag.merge_fragment({
            "nodes": [{"id": "concept:a", "type": "concept", "title": "A"},
                      {"id": "concept:b", "type": "concept", "title": "B"}],
            "edges": [{"from": "concept:a", "to": "concept:b", "type": "relates_to"}],
        }, store=fake, stamp_edges=False)
        self.assertFalse(any(self._STAMP in c for c, _ in fake.calls
                             if c.startswith("MATCH (n:")))


class DeleteEdgeTests(unittest.TestCase):
    """S4 확장 — 엣지 삭제 표면: 동기화 rel 5종만, 툼스톤 기록, owner 스코프."""

    def test_deletes_and_writes_tombstone(self):
        fake = _FakeStore({"RETURN count(r) AS c": [{"c": 1}]})
        res = q.delete_edge("concept:a", "prerequisite_of", "concept:b",
                            owner="user:t", store=fake)
        self.assertTrue(res["executed"] and res["found"])
        self.assertEqual(res["deleted"]["edges"], 1)
        joined = "\n".join(c for c, _ in fake.calls)
        self.assertIn("-[r:PREREQUISITE_OF]->", joined)
        self.assertIn("a.owner_id = $owner", joined)      # SEC-3 스코프
        self.assertIn("MERGE (t:_EdgeTombstone", joined)  # 삭제 전파 표식

    def test_related_to_deletes_both_directions(self):
        fake = _FakeStore({"RETURN count(r) AS c": [{"c": 1}]})
        res = q.delete_edge("concept:a", "relates_to", "concept:b", store=fake)
        self.assertEqual(res["deleted"]["edges"], 2)      # 정+역방향
        dels = [p for c, p in fake.calls if "DELETE r" in c]
        self.assertEqual({(p["s"], p["d"]) for p in dels},
                         {("concept:a", "concept:b"), ("concept:b", "concept:a")})

    def test_about_maps_episode_concept_labels(self):
        fake = _FakeStore({"RETURN count(r) AS c": [{"c": 1}]})
        q.delete_edge("episode:e1", "about", "concept:c1", store=fake)
        joined = "\n".join(c for c, _ in fake.calls)
        self.assertIn("MATCH (a:Episode)-[r:ABOUT]->(b:Concept)", joined)

    def test_unsupported_rel_refused_without_delete(self):
        fake = _FakeStore()
        res = q.delete_edge("bridge:b1", "connects_via", "concept:c1", store=fake)
        self.assertTrue(res["executed"])
        self.assertFalse(res["found"])
        self.assertIn("unsupported edge type", res["note"])
        self.assertFalse(any("DELETE" in c for c, _ in fake.calls))

    def test_missing_edge_found_false_no_tombstone(self):
        fake = _FakeStore({"RETURN count(r) AS c": [{"c": 0}]})
        res = q.delete_edge("concept:a", "derives_from", "concept:b", store=fake)
        self.assertTrue(res["executed"])
        self.assertFalse(res["found"])
        self.assertFalse(any("_EdgeTombstone" in c for c, _ in fake.calls))

    def test_no_store_degrades(self):
        res = frag.merge_fragment({"nodes": []}, store=None)
        self.assertFalse(res["executed"])

    # S0 이후 추적 라벨(Concept 등)의 존재확인은 `count(n)` 이 아니라 **현재 추적필드
    # 투영**이다(변경감지 스탬핑에 현재값이 필요) — 더블도 그 질의에 응답해야 '기존
    # 노드'를 흉내낼 수 있다. 투영은 `n.rev AS rev, n.title AS title, …` 로 시작한다.
    _EXISTING = {"n.rev AS rev": [{"rev": 0, "title": "concept:a"}]}

    def test_matched_counted_on_existing_node(self):
        fake = _FakeStore(dict(self._EXISTING))
        res = frag.merge_fragment(
            {"nodes": [{"id": "concept:a", "type": "concept"}]}, store=fake)
        self.assertEqual(res["merged"]["nodes"], {"created": 0, "matched": 1})

    def test_reMERGE_without_owner_does_not_clobber_owner_id(self):
        # owner 미지정 재-MERGE 가 기존 노드 owner_id 를 기본값으로 덮어쓰면 스코프
        # 필터(§1 불변)가 깨진다 — 생성 시/명시 시에만 스탬핑.
        fake = _FakeStore(dict(self._EXISTING))                   # 기존 노드
        frag.merge_fragment({"nodes": [{"id": "concept:a", "type": "concept"}]}, store=fake)
        # 노드 MERGE 만 — S0 스탬핑의 `_SyncMeta` MERGE 는 owner 대상이 아니다.
        merge_params = [p for c, p in fake.calls
                        if c.startswith("MERGE") and "_SyncMeta" not in c]
        self.assertTrue(all("owner_id" not in p for p in merge_params))
        # 명시 owner 는 기존 노드여도 스탬핑
        fake2 = _FakeStore(dict(self._EXISTING))
        frag.merge_fragment({"nodes": [{"id": "concept:a", "type": "concept"}],
                             "owner": "user:t"}, store=fake2)
        merge_params2 = [p for c, p in fake2.calls
                         if c.startswith("MERGE") and "_SyncMeta" not in c]
        self.assertTrue(any(p.get("owner_id") == "user:t" for p in merge_params2))


if __name__ == "__main__":
    unittest.main()


class CouplingTests(unittest.TestCase):
    """Compare 뷰(D22) — 개념 대 개념 결합쌍 계약."""

    def test_direct_plan_matches_both_directions(self):
        plan = q.direct_coupling_plan(owner="u", a="math", b="physics")
        self.assertIn("x.domain = $a AND y.domain = $b", plan.cypher)
        self.assertIn("x.domain = $b AND y.domain = $a", plan.cypher)

    def test_bridge_plan_traverses_connects_both_arms(self):
        plan = q.bridge_coupling_plan(a="math", b="physics")
        self.assertIn("<-[:CONNECTS]-(br:Bridge)-[:CONNECTS]->", plan.cypher)

    def test_bridge_plan_scopes_owner(self):
        # F5 — Concept 양끝 엄격 + Bridge 과도기 술어. 무 owner 는 글로벌(로컬 현행).
        plan = q.bridge_coupling_plan(a="math", b="physics", owner="u")
        self.assertIn("x.owner_id = $owner", plan.cypher)
        self.assertIn("y.owner_id = $owner", plan.cypher)
        self.assertIn("(br.owner_id = $owner OR br.owner_id = '')", plan.cypher)
        self.assertNotIn("$owner", q.bridge_coupling_plan(a="math", b="physics").cypher)

    def test_coupling_merges_and_prefers_direct(self):
        fake = _FakeStore({
            "label(r) AS rel": [
                {"a_id": "c1", "a_title": "C1", "a_domain": "math",
                 "b_id": "p1", "b_title": "P1", "rel": "RELATED_TO"}],
            "br.bridge_id AS via": [
                # 직접 결합과 동일 쌍(무시) + 신규 매개쌍
                {"a_id": "c1", "a_title": "C1", "b_id": "p1", "b_title": "P1",
                 "via": "br-x", "via_title": "X"},
                {"a_id": "c2", "a_title": "C2", "b_id": "p2", "b_title": "P2",
                 "via": "br-y", "via_title": "Y"}],
        })
        res = q.coupling(owner="u", a="math", b="physics", store=fake)
        self.assertTrue(res["executed"])
        kinds = {(p["a_id"], p["b_id"]): p["kind"] for p in res["pairs"]}
        self.assertEqual(kinds[("c1", "p1")], "direct")   # 직접이 매개에 우선
        self.assertEqual(kinds[("c2", "p2")], "bridge")
        self.assertEqual(len(res["pairs"]), 2)


class PinnedHubTests(unittest.TestCase):
    def test_pinned_outranks_degree_inside_its_branch(self):
        """pin 이 허브 승격 1순위(D32). D74 로 중심은 **뿌리**가 됐으므로 pin 은
        더 이상 원점을 주지 않는다 — 자기 가지 안에서 가장 안쪽 자리를 준다."""
        import math
        nodes = [{"id": "a", "domain": "math", "degree": 9},
                 {"id": "p", "domain": "math", "degree": 1, "pinned": True}]
        out = {n["id"]: n for n in q.layout_positions(nodes, [])}
        rp = math.dist((0, 0, 0), (out["p"]["x"], out["p"]["y"], out["p"]["z"]))
        ra = math.dist((0, 0, 0), (out["a"]["x"], out["a"]["y"], out["a"]["z"]))
        self.assertLess(rp, ra)


class SuggestTiesTests(unittest.TestCase):
    """고아 잇기 후보(D32) — 태그 교집합 +2·같은 도메인 +1, 기연결 제외, 결정론 순."""

    def test_orphan_gets_tag_and_domain_candidates(self):
        fake = _FakeStore({
            "AS tags ORDER BY id": [
                {"id": "a", "title": "A", "domain": "math", "tags": ["fft", "waves"]},
                {"id": "b", "title": "B", "domain": "math", "tags": ["fft"]},
                {"id": "c", "title": "C", "domain": "art", "tags": ["waves"]},
                {"id": "d", "title": "D", "domain": "art", "tags": []},
                {"id": "e", "title": "E", "domain": "math", "tags": []},
            ],
            "OPTIONAL MATCH (c)-[r]->(d:Concept)": [
                {"id": "a", "target": None},            # a 는 고아
                {"id": "b", "target": "e"},
            ],
        })
        res = q.suggest_ties(owner="user:t", node="a", store=fake)
        self.assertTrue(res["executed"])
        # F11 — 점수는 태그 **희소성**(IDF) + 도메인 + 2홉 근접이다. 값은 산식에서
        # 파생되므로 여기서는 **순서와 근거**를 못박는다(리터럴 점수는 산식이 바뀔
        # 때마다 무의미하게 깨진다). d 는 신호 0 이라 여전히 제외.
        self.assertEqual([s["id"] for s in res["suggestions"]], ["b", "c", "e"])
        b, c, e = res["suggestions"]
        self.assertEqual(b["shared_tags"], ["fft"])
        self.assertGreater(b["score"], c["score"])      # 태그+도메인 > 태그만
        self.assertGreater(c["score"], e["score"])      # 태그만 > 도메인만
        self.assertEqual(b["reason"], "shared_tags")
        self.assertEqual(e["reason"], "same_domain")
        self.assertEqual(e["shared_tags"], [])
        self.assertEqual(b["signals"]["domain"], 1.0)
        self.assertEqual(e["signals"]["tags"], 0.0)
        self.assertIn("common neighbors", res["formula"])   # 산식 공개(F11)

    def test_already_linked_excluded(self):
        fake = _FakeStore({
            "AS tags ORDER BY id": [
                {"id": "a", "title": "A", "domain": "math", "tags": ["x"]},
                {"id": "b", "title": "B", "domain": "math", "tags": ["x"]},
            ],
            "OPTIONAL MATCH (c)-[r]->(d:Concept)": [
                {"id": "b", "target": "a"},             # 기존 b→a — 재제안 금지
            ],
        })
        res = q.suggest_ties(owner="user:t", node="a", store=fake)
        self.assertEqual(res["suggestions"], [])

    def test_unknown_node_is_empty_not_error(self):
        fake = _FakeStore({"AS tags ORDER BY id": []})
        res = q.suggest_ties(owner="user:t", node="ghost", store=fake)
        self.assertTrue(res["executed"])
        self.assertEqual(res["suggestions"], [])

    def test_bridge_mediated_neighbor_excluded(self):
        # F3 — 브릿지 매개로 이미 이어진 개념은 잇기 후보에서 제외(접착제 인지).
        fake = _FakeStore({
            "AS tags ORDER BY id": [
                {"id": "a", "title": "A", "domain": "math", "tags": ["x"]},
                {"id": "b", "title": "B", "domain": "math", "tags": ["x"]},
            ],
            "DISTINCT y.concept_id AS id": [{"id": "b"}],
        })
        res = q.suggest_ties(owner="user:t", node="a", store=fake)
        self.assertEqual(res["suggestions"], [])


class LayoutPresetTests(unittest.TestCase):
    """레이아웃 프리셋 — **2종**(D34 개정 2026-08-02: hub·rank). 둘 다 순수 함수고
    도메인 클러스터를 만들지 않는다. 폐기된 이름(radial 등)은 hub 로 접어 받는다."""

    def _graph(self):
        nodes = [{"id": "hub", "domain": "math", "degree": 3},
                 {"id": "a", "domain": "math", "degree": 2},
                 {"id": "b", "domain": "phys", "degree": 2},
                 {"id": "lone", "domain": "math", "degree": 0}]
        edges = [{"source": "hub", "target": "a", "type": "PREREQUISITE_OF"},
                 {"source": "a", "target": "b", "type": "RELATED_TO"},
                 {"source": "hub", "target": "b", "type": "RELATED_TO"}]
        return nodes, edges

    def _run(self, layout):
        import copy
        nodes, edges = self._graph()
        return {n["id"]: n for n in
                q.layout_positions(copy.deepcopy(nodes), edges, layout)}

    def test_only_two_presets(self):
        self.assertEqual(tuple(q.LAYOUTS), ("hub", "rank"))

    def test_all_presets_deterministic(self):
        for layout in q.LAYOUTS:
            p1, p2 = self._run(layout), self._run(layout)
            self.assertEqual([(p1[k]["x"], p1[k]["y"], p1[k]["z"]) for k in sorted(p1)],
                             [(p2[k]["x"], p2[k]["y"], p2[k]["z"]) for k in sorted(p2)],
                             layout)

    def test_retired_names_fold_to_hub(self):
        """저장된 프리퍼런스·딥링크가 조용히 깨지지 않아야 한다."""
        want = [(n["x"], n["y"], n["z"]) for n in self._run("hub").values()]
        for old in ("radial", "circle", "tree", "organic", "dendro", "shell", "??"):
            self.assertEqual([(n["x"], n["y"], n["z"]) for n in self._run(old).values()],
                             want, old)

    def test_hub_keeps_centrality_inside_each_branch(self):
        """D74 — 중심성은 **가지 안에서** 산다. math 가지 안에서 연결이 많은 것이
        도메인 축에 가깝고 고아가 가장 멀다(가지를 가로지른 비교는 뜻이 없다)."""
        import math
        pos = self._run("hub")
        r = {k: math.dist((0, 0, 0), (v["x"], v["y"], v["z"])) for k, v in pos.items()}
        self.assertLess(r["hub"], r["a"])       # 같은 math 가지: 3연결 < 2연결
        self.assertLess(r["a"], r["lone"])      # 〃            2연결 < 고아

    def test_hub_separates_domains_into_directions(self):
        """D74 — 도메인은 **방향**이다. 다른 도메인 노드는 다른 쪽으로 날아간다
        (구 배치는 도메인을 무시하고 degree 로만 고리를 만들었다 — 개정)."""
        import math
        pos = self._run("hub")
        def unit(n):
            v = (n["x"], n["y"], n["z"])
            L = math.dist((0, 0, 0), v) or 1.0
            return [t / L for t in v]
        same = sum(x * y for x, y in zip(unit(pos["hub"]), unit(pos["a"])))     # math↔math
        cross = sum(x * y for x, y in zip(unit(pos["hub"]), unit(pos["b"])))    # math↔phys
        self.assertGreater(same, cross)

    def test_hub_reads_domain_as_a_path(self):
        """`vinc/architecture` 는 vinc **아래** architecture 다. 공통 접두는 뿌리로
        벗겨지므로 vinc/a 와 vinc/b 는 형제 가지가 된다(한 줄 막대가 아니라)."""
        import copy, math
        nodes = [{"id": f"{d}{i}", "domain": d, "degree": 1}
                 for d in ("vinc/a", "vinc/b") for i in range(3)]
        out = {n["id"]: n for n in q.layout_positions(copy.deepcopy(nodes), [], "hub")}
        def unit(n):
            v = (n["x"], n["y"], n["z"])
            L = math.dist((0, 0, 0), v) or 1.0
            return [t / L for t in v]
        within = sum(x * y for x, y in zip(unit(out["vinc/a0"]), unit(out["vinc/a1"])))
        across = sum(x * y for x, y in zip(unit(out["vinc/a0"]), unit(out["vinc/b0"])))
        self.assertGreater(within, across)

    def test_planar_coords_are_separate_and_never_collide(self):
        """2D 는 3D 의 투영이 아니라 **독립 산출**이다(D74). z 를 눌러 접는 방식은
        원뿔 배치에서 서로 다른 노드를 같은 점으로 만든다 — 여기서는 원리적으로
        생기지 않아야 하고, 경계 조건은 `_dedupe_planar` 가 막는다."""
        import copy
        nodes = [{"id": f"n{i}", "domain": f"vinc/d{i % 7}", "degree": i % 4}
                 for i in range(140)]
        out = q.layout_positions(copy.deepcopy(nodes), [], "hub")
        planar = {(n["x2"], n["y2"]) for n in out}
        self.assertEqual(len(planar), len(out))
        self.assertTrue(all("x2" in n and "y2" in n for n in out))

    def test_planar_dedupe_survives_exact_collision(self):
        """같은 도메인·같은 degree 인 잎이 한 무더기여도 평면 좌표는 전부 다르다."""
        import copy
        nodes = [{"id": f"n{i}", "domain": "vinc/one", "degree": 1} for i in range(40)]
        out = q.layout_positions(copy.deepcopy(nodes), [], "hub")
        self.assertEqual(len({(n["x2"], n["y2"]) for n in out}), 40)

    def test_rank_orders_by_direction(self):
        """x = 랭크. 선행이 왼쪽, 후행이 오른쪽. 평면 정본은 x·y 그대로다."""
        pos = self._run("rank")
        self.assertLess(pos["hub"]["x"], pos["a"]["x"])   # hub -PREREQUISITE-> a
        self.assertTrue(all(v["x2"] == v["x"] and v["y2"] == v["y"]
                            for v in pos.values()))

    def test_rank_slabs_domains_on_z_only(self):
        """D74 — rank 의 위계는 **z 에만** 싣는다. 층(x)은 그대로이고 2D 는 z 를
        안 읽으므로 평면 그림은 도메인 때문에 흔들리지 않는다(원 주석의 사유:
        계보가 도메인을 가로지르므로 클러스터로 가르면 흐름이 끊긴다)."""
        pos = self._run("rank")
        self.assertNotEqual(pos["b"]["z"], pos["a"]["z"])     # phys ≠ math 판
        self.assertEqual(pos["a"]["z"], pos["hub"]["z"])      # 같은 math 판
        self.assertEqual(pos["b"]["x2"], pos["b"]["x"])       # 평면은 z 무관

    def test_rank_single_domain_stays_flat(self):
        """도메인이 하나뿐이면 판도 하나 — z 는 0 으로 남는다(구 동작 하위호환)."""
        import copy
        nodes = [{"id": i, "domain": "d", "degree": 1} for i in ("a", "b", "c")]
        out = q.layout_positions(copy.deepcopy(nodes), [], "rank")
        self.assertTrue(all(n["z"] == 0.0 for n in out))

    def test_rank_puts_most_connected_on_top(self):
        """같은 열 안 정렬은 허브 우선이고, 그 우선순위가 **화면 위쪽**이어야 한다.
        두 렌더러 다 +y 가 위다(three.js 는 기본, sigma 는 `(1-y)*h/2` 로 뒤집는다)
        — 부호를 잘못 두면 "허브 우선"이 화면에서 거꾸로 읽힌다(실측 회귀)."""
        import copy
        nodes = [{"id": "big", "domain": "d", "degree": 9},
                 {"id": "mid", "domain": "d", "degree": 4},
                 {"id": "small", "domain": "d", "degree": 1}]
        pos = {n["id"]: n for n in q.layout_positions(copy.deepcopy(nodes), [], "rank")}
        self.assertGreater(pos["big"]["y"], pos["mid"]["y"])
        self.assertGreater(pos["mid"]["y"], pos["small"]["y"])

    def test_rank_reverses_derives_from(self):
        """DERIVES_FROM(a→b) 은 "a 가 b 에서 나왔다" 이므로 **b 가 상류**다.
        좌측 문서 트리(D69)는 이미 뒤집어 적재하는데 레이아웃은 안 뒤집어, 같은
        데이터를 두 화면이 반대로 읽고 있었다(2026-08-02 정합)."""
        import copy
        nodes = [{"id": "orig", "domain": "d", "degree": 1},
                 {"id": "derived", "domain": "d", "degree": 1}]
        edges = [{"source": "derived", "target": "orig", "type": "DERIVES_FROM"}]
        pos = {n["id"]: n for n in q.layout_positions(copy.deepcopy(nodes), edges, "rank")}
        self.assertLess(pos["orig"]["x"], pos["derived"]["x"])   # 원본이 왼쪽

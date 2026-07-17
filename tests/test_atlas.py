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
    def test_seed_traversal_uses_trail_guard(self):
        plan = q.subgraph_ids_plan("concept:fourier", depth=2)
        self.assertIn("TRAIL 1..2", plan.cypher)          # 엣지 무재사용 가드(M3)
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
        fake = _FakeStore({
            "DISTINCT m.concept_id": [{"id": "b"}],
            "IN $ids": [
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

    def test_unsupported_types_warn_and_skip(self):
        fake = _FakeStore()
        res = frag.merge_fragment({
            "nodes": [{"id": "x", "type": "policy"}],       # enum 밖 노드
            "edges": [{"from": "a", "to": "b", "type": "blocks"}],  # enum 밖 엣지
        }, store=fake)
        self.assertEqual(res["merged"]["nodes"]["created"], 0)
        self.assertEqual(res["merged"]["edges"]["created"], 0)
        self.assertEqual(len(res["warnings"]), 2)

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
    def test_pinned_outranks_degree_for_hub_center(self):
        # pin 이 허브 승격 1순위(D32) — degree 9 보다 pinned degree 1 이 중심(r=0).
        nodes = [{"id": "a", "domain": "math", "degree": 9},
                 {"id": "p", "domain": "math", "degree": 1, "pinned": True}]
        out = {n["id"]: n for n in q.layout_positions(nodes, [])}
        self.assertEqual((out["p"]["x"], out["p"]["y"], out["p"]["z"]), (0.0, 0.0, 0.0))
        self.assertNotEqual(out["a"]["z"], 0.0)


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
        got = [(s["id"], s["score"]) for s in res["suggestions"]]
        # b: fft(2)+도메인(1)=3 · c: waves(2)=2 · e: 도메인만=1 · d: 0 → 제외
        self.assertEqual(got, [("b", 3), ("c", 2), ("e", 1)])
        self.assertEqual(res["suggestions"][0]["shared_tags"], ["fft"])

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


class LayoutPresetTests(unittest.TestCase):
    """D34 레이아웃 프리셋 — 전부 순수 함수(결정론), 미지 값은 shell 강등."""

    def _graph(self):
        nodes = [{"id": "hub", "domain": "math", "degree": 3},
                 {"id": "a", "domain": "math", "degree": 2},
                 {"id": "b", "domain": "math", "degree": 2},
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

    def test_all_presets_deterministic(self):
        for layout in q.LAYOUTS:
            p1, p2 = self._run(layout), self._run(layout)
            self.assertEqual([(p1[k]["x"], p1[k]["y"], p1[k]["z"]) for k in sorted(p1)],
                             [(p2[k]["x"], p2[k]["y"], p2[k]["z"]) for k in sorted(p2)],
                             layout)

    def test_unknown_layout_degrades_to_shell(self):
        self.assertEqual([(n["x"], n["y"], n["z"]) for n in self._run("??").values()],
                         [(n["x"], n["y"], n["z"]) for n in self._run("shell").values()])

    def test_radial_hub_center_and_orphan_outermost(self):
        import math
        pos = self._run("radial")
        self.assertEqual((pos["hub"]["x"], pos["hub"]["y"]), (0.0, 0.0))  # 허브 = 중심
        r = {k: math.hypot(v["x"], v["y"]) for k, v in pos.items()}
        self.assertGreater(r["lone"], max(r["a"], r["b"]))   # 고아 = 최외곽 껍질

    def test_circle_puts_all_on_equal_radius(self):
        import math
        pos = self._run("circle")
        radii = {round(math.hypot(v["x"], v["y"]), 4) for v in pos.values()}
        self.assertEqual(len(radii), 1)                       # 전원 동일 반경
        self.assertTrue(all(v["z"] == 0.0 for v in pos.values()))

    def test_tree_layers_follow_direction(self):
        pos = self._run("tree")
        # hub -PREREQUISITE-> a : 루트(hub)가 위(y 큰 쪽), a 가 아래
        self.assertGreater(pos["hub"]["y"], pos["a"]["y"])
        # 방향 엣지에 안 걸린 lone 은 최하단
        self.assertLess(pos["lone"]["y"], pos["a"]["y"])

    def test_organic_pulls_connected_closer_than_unconnected(self):
        import math
        pos = self._run("organic")
        d = lambda u, v: math.dist(
            (pos[u]["x"], pos[u]["y"], pos[u]["z"]),
            (pos[v]["x"], pos[v]["y"], pos[v]["z"]))
        # 엣지(hub-a)가 비엣지(hub-lone)보다 가깝다 — 스프링 대 척력의 기본 성질
        self.assertLess(d("hub", "a"), d("hub", "lone"))

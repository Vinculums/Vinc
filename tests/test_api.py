# -*- coding: utf-8 -*-
"""Tests for the query API (plan builders + dry execution)."""
from __future__ import annotations

import sys
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import query as q  # noqa: E402


class PlanTests(unittest.TestCase):
    def test_recall_plan_workspace_filter(self):
        plan = q.recall_plan("billing", workspace="planning", limit=5)
        self.assertEqual(plan.kind, "recall")
        self.assertIn("e.workspace = $workspace", plan.cypher)
        self.assertEqual(plan.params["workspace"], "planning")
        self.assertEqual(plan.params["limit"], 5)

    def test_recall_plan_no_filters(self):
        plan = q.recall_plan()
        self.assertNotIn("WHERE", plan.cypher)

    def test_recall_plan_owner_scope(self):
        plan = q.recall_plan(owner="user:weg1456", limit=5)
        self.assertIn("e.owner_id = $owner", plan.cypher)
        self.assertEqual(plan.params["owner"], "user:weg1456")
        # owner is also projected so callers can see ownership
        self.assertIn("e.owner_id AS owner", plan.cypher)

    def test_recall_plan_owner_tenant_workspace_anded(self):
        plan = q.recall_plan("billing", owner="u1", tenant="acme", workspace="planning")
        for frag in ("e.owner_id = $owner", "e.tenant_id = $tenant", "e.workspace = $workspace"):
            self.assertIn(frag, plan.cypher)
        self.assertEqual(plan.cypher.count(" AND "), 3)  # owner AND tenant AND workspace AND query

    def test_recall_domain_filter_is_open(self):
        # domain is a free string filter — any value the learner used, no enum
        plan = q.recall_plan(owner="user:doheon", domain="art")
        self.assertIn("e.domain = $domain", plan.cypher)
        self.assertEqual(plan.params["domain"], "art")
        self.assertIn("e.domain AS domain", plan.cypher)  # projected too

    def test_recall_no_domain_spans_all(self):
        plan = q.recall_plan(owner="user:doheon")
        self.assertNotIn("e.domain = $domain", plan.cypher)
        self.assertNotIn("domain", plan.params)

    def test_domains_discovery_is_data_driven(self):
        plan = q.domains_plan(owner="user:doheon")
        self.assertEqual(plan.kind, "domains")
        self.assertIn("e.domain AS domain", plan.cypher)
        self.assertIn("count(*) AS episodes", plan.cypher)
        self.assertEqual(plan.params["owner"], "user:doheon")

    def test_domains_all_owners(self):
        plan = q.domains_plan()
        self.assertNotIn("owner", plan.params)
        self.assertIn("e.domain IS NOT NULL", plan.cypher)

    def test_both_backward_pointing_relations_are_normalized(self):
        """`derives_from` 과 `inherits_from` 은 화살표가 **시간을 거스른다**.

        관례가 둘을 같은 모양으로 규정하는데(A가 B에서 나왔다 / A가 B를 상속한다,
        둘 다 B가 위) 2026-08-02 의 방향 정합 수정이 앞의 것만 다뤄, rank 배치가
        상속 사슬만 거꾸로 세우고 있었다. 앱의 인과 원뿔이 이 표를 미러하므로
        (`app/src/spotlight.ts` UPSTREAM_REVERSED) 둘은 **함께** 움직여야 한다."""
        self.assertEqual(set(q._REVERSED_RELS), {"DERIVES_FROM", "INHERITS_FROM"})
        # 선행은 정방향이다 — 뒤집으면 "먼저"가 "나중"이 된다.
        self.assertNotIn("PREREQUISITE_OF", q._REVERSED_RELS)

    def test_record_is_a_synced_kind_with_both_time_columns(self):
        """Record 가 동기화 대상인가. 라이브 277건으로 노드 최대 종류인데 빠져 있었다."""
        from api import sync
        self.assertEqual(sync._SYNC_TYPES.get("record"), "Record")
        self.assertIn("Record", sync._BIRTH_LABELS)          # created 열이 있다
        self.assertIn("content_time", sync.SYNC_FIELDS["Record"])
        # 툼스톤 라벨은 동기화 라벨에서 파생된다 — 따로 적으면 어긋난다.
        self.assertIn("Record", sync._SYNC_LABELS)
        plan = sync._records_delta_plan(owner="user:doheon", domains=["d"], since=3)
        self.assertIn("r.created AS created", plan.cypher)
        self.assertIn("r.content_time AS content_time", plan.cypher)
        self.assertEqual(plan.params["since"], 3)

    def test_record_edges_carry_the_original_type_not_the_label(self):
        """Record 엣지는 전부 ATTACHES_TO 로 저장되고 원 타입은 `r.type` 에 있다.
        라벨을 역맵에 태우면 네 갈래가 한 이름으로 뭉개진다."""
        from api import sync
        plan = sync._record_edges_plan(["record:x"], owner="user:doheon",
                                       pair=("Concept", "concept_id"))
        self.assertIn("r.type AS ftype", plan.cypher)
        self.assertIn("[r:ATTACHES_TO]", plan.cypher)   # 무타입으로 열면 Binder 가 죽는다
        self.assertIn("a.owner_id = $owner", plan.cypher)
        self.assertNotIn("label(r)", plan.cypher)

    def test_domain_census_counts_untopiced(self):
        # 센서스는 무주제도 센다 — WHERE 는 owner 스코프만. NULL·'' 도메인 행을
        # "(none)" 으로 합치는 것은 domains() 몫. 근거: [[d6064cc741e9e121f]] §1.1
        plan = q.domain_counts_plan(owner="user:doheon")
        self.assertNotIn("IS NOT NULL", plan.cypher)
        self.assertIn("e.owner_id = $owner", plan.cypher)
        self.assertNotIn("WHERE", q.domain_counts_plan().cypher)

    def test_learner_model_plan_owner_scoped(self):
        plan = q.learner_model_plan(owner="user:doheon")
        self.assertEqual(plan.kind, "learner_model")
        self.assertIn("MATCH (p:LearnerPattern)", plan.cypher)
        self.assertIn("p.owner_id = $owner", plan.cypher)
        self.assertEqual(plan.params["owner"], "user:doheon")

    def test_graph_plan_owner_and_domain(self):
        plan = q.graph_plan(owner="user:doheon", domain="math")
        self.assertEqual(plan.kind, "graph")
        self.assertIn("MATCH (c:Concept)", plan.cypher)
        self.assertIn("c.owner_id = $owner", plan.cypher)
        # 주제 스코프는 **하위 트리**다(2026-08-15) — 정확일치 OR `d/` 접두.
        self.assertIn("(c.domain = $dom0 OR c.domain STARTS WITH $domp0)", plan.cypher)
        self.assertEqual(plan.params["dom0"], "math")
        self.assertEqual(plan.params["domp0"], "math/")
        self.assertIn("OPTIONAL MATCH (c)-[r]->(d:Concept)", plan.cypher)

    def test_bridge_rows_plan_lens_and_ids_modes(self):
        # F2 축 플랜 — L1(domain)과 L2 후조회(ids)는 상호 배제, 투영에 kind/domain2/role.
        p = q.bridge_rows_plan(owner="u", domain="math")
        self.assertIn("b.source_domain = $dom0", p.cypher)
        self.assertIn("b.target_domain STARTS WITH $domp0", p.cypher)
        self.assertIn("(b.owner_id = $owner OR b.owner_id = '')", p.cypher)
        self.assertIn("'bridge' AS kind", p.cypher)
        self.assertIn("b.target_domain AS domain2", p.cypher)
        self.assertIn("r.role AS role", p.cypher)
        pi = q.bridge_rows_plan(owner="u", ids=["concept:a"])
        self.assertIn("(c.concept_id IN $ids OR b.bridge_id IN $ids)", pi.cypher)
        with self.assertRaises(AssertionError):
            q.bridge_rows_plan(owner="u", domain="d", ids=["x"])

    def test_episode_and_record_rows_plans(self):
        pe = q.episode_rows_plan(owner="u", domain="math")
        self.assertIn("MATCH (e:Episode)-[r:ABOUT]->(c:Concept)", pe.cypher)
        self.assertIn("e.domain = $dom0", pe.cypher)
        self.assertIn("c.domain STARTS WITH $domp0", pe.cypher)
        self.assertIn("'episode' AS kind", pe.cypher)
        pr = q.record_rows_plan(owner="u", ids=["concept:a"])
        self.assertIn("MATCH (rc:Record)-[r:ATTACHES_TO]->(c:Concept)", pr.cypher)
        self.assertIn("r.type AS role", pr.cypher)
        self.assertIn("(c.concept_id IN $ids OR rc.record_id IN $ids)", pr.cypher)


class ShapeGraphTests(unittest.TestCase):
    ROWS = [
        {"id": "math-01", "label": "Lie群", "domain": "math", "rel": "RELATED_TO",
         "target": "math-02", "target_domain": "math"},
        {"id": "math-01", "label": "Lie群", "domain": "math", "rel": "BRIDGE",
         "target": "phys-01", "target_domain": "physics"},   # cross-domain
        {"id": "art-01", "label": "총체예술", "domain": "art", "rel": None,
         "target": None, "target_domain": None},              # isolated node
    ]

    def test_nodes_deduped_with_degree(self):
        g = q.shape_graph(self.ROWS)
        by_id = {n["id"]: n for n in g["nodes"]}
        self.assertEqual(set(by_id), {"math-01", "math-02", "phys-01", "art-01"})
        self.assertEqual(by_id["math-01"]["degree"], 2)   # two outgoing edges
        self.assertEqual(by_id["art-01"]["degree"], 0)    # isolated

    def test_cross_domain_edge_flagged(self):
        g = q.shape_graph(self.ROWS)
        bridge = [e for e in g["edges"] if e["target"] == "phys-01"][0]
        self.assertTrue(bridge["cross_domain"])
        intra = [e for e in g["edges"] if e["target"] == "math-02"][0]
        self.assertFalse(intra["cross_domain"])

    def test_heterogeneous_kinds_and_degree(self):
        # F2/F3 — 이종 kind 행: kind/target_kind/domain2/role 반영 + 모든 엣지 타입이
        # 양 끝 degree 에 카운트(브릿지 팔만 가진 개념이 고아가 아니게 — F3 회귀).
        rows = [
            {"id": "c1", "label": "C1", "domain": "math"},   # 엣지 없는 개념 행
            {"id": "bridge:x", "label": "X", "domain": "math", "domain2": "physics",
             "kind": "bridge", "rel": "CONNECTS", "role": "math_input",
             "target": "c1", "target_domain": "math", "target_kind": "concept"},
            {"id": "episode:e1", "label": "E1", "domain": "math", "kind": "episode",
             "rel": "ABOUT", "target": "c1", "target_domain": "math",
             "target_kind": "concept"},
        ]
        g = q.shape_graph(rows)
        by_id = {n["id"]: n for n in g["nodes"]}
        self.assertEqual(by_id["bridge:x"]["kind"], "bridge")
        self.assertEqual(by_id["bridge:x"]["domain2"], "physics")
        self.assertEqual(by_id["episode:e1"]["kind"], "episode")
        self.assertEqual(by_id["c1"]["kind"], "concept")     # kind 부재 → 기본값
        self.assertEqual(by_id["c1"]["degree"], 2)           # CONNECTS + ABOUT 카운트
        roles = {e["type"]: e.get("role") for e in g["edges"]}
        self.assertEqual(roles, {"CONNECTS": "math_input", "ABOUT": None})

    @mock.patch("api.query.open_store", return_value=None)
    def test_graph_entrypoint_without_db(self, _open):
        g = q.graph(owner="user:doheon")
        self.assertFalse(g["executed"])
        self.assertEqual(g["nodes"], [])
        self.assertEqual(g["edges"], [])


class PPRTests(unittest.TestCase):
    """E-G2 ego-Personalized PageRank 순수 함수 — 결정론·시드 편향·엣지 타입 가중."""

    def _line(self):
        # 경로 그래프 s - a - b - c (RELATED_TO), 시드 = s
        nodes = [{"id": n} for n in ("s", "a", "b", "c")]
        edges = [{"source": u, "target": v, "type": "RELATED_TO"}
                 for u, v in (("s", "a"), ("a", "b"), ("b", "c"))]
        return nodes, edges

    def test_scores_sum_to_one(self):
        nodes, edges = self._line()
        sc = q.ppr_scores(nodes, edges, ["s"])
        self.assertAlmostEqual(sum(sc.values()), 1.0, places=6)
        self.assertEqual(set(sc), {"s", "a", "b", "c"})

    def test_seed_bias_decays_with_distance(self):
        # 개인화 확산: 시드는 균등(0.25) 이상으로 승격되고, 시드에서 멀어질수록
        # 질량이 단조 감소한다(a→b→c). 대칭 RWR 이므로 degree-2 이웃 a 가 degree-1
        # 시드 s 자신을 넘어설 수 있다(허브 효과 — 정상). 시드는 최원거리 c 를 넘는다.
        nodes, edges = self._line()
        sc = q.ppr_scores(nodes, edges, ["s"])
        self.assertGreater(sc["s"], 0.25)          # 균등 이상으로 편향
        self.assertGreater(sc["a"], sc["b"])       # 거리 감소
        self.assertGreater(sc["b"], sc["c"])
        self.assertGreater(sc["s"], sc["c"])        # 시드 > 최원거리

    def test_deterministic_repeatable(self):
        nodes, edges = self._line()
        self.assertEqual(q.ppr_scores(nodes, edges, ["s"]),
                         q.ppr_scores(nodes, edges, ["s"]))

    def test_edge_type_weight_prefers_bridge(self):
        # 허브 h 가 c1(CONNECTS, 가중 2.0)·c2(RELATED_TO, 1.0)에 각각 연결.
        # 시드 h 에서 강가중 이웃 c1 이 c2 보다 높은 질량을 받아야.
        nodes = [{"id": i} for i in ("h", "c1", "c2")]
        edges = [{"source": "h", "target": "c1", "type": "CONNECTS"},
                 {"source": "h", "target": "c2", "type": "RELATED_TO"}]
        sc = q.ppr_scores(nodes, edges, ["h"])
        self.assertGreater(sc["c1"], sc["c2"])

    def test_empty_and_out_of_graph_seed(self):
        self.assertEqual(q.ppr_scores([], [], ["x"]), {})
        # 시드가 그래프 밖 → 균등 개인화로 강등(크래시 없음, 합=1)
        nodes, edges = self._line()
        sc = q.ppr_scores(nodes, edges, ["zzz"])
        self.assertAlmostEqual(sum(sc.values()), 1.0, places=6)

    def test_attach_ppr_adds_field(self):
        nodes, edges = self._line()
        shaped = {"nodes": nodes, "edges": edges}
        q.attach_ppr(shaped, ["s"])
        self.assertTrue(all("ppr" in n for n in shaped["nodes"]))
        by_id = {n["id"]: n["ppr"] for n in shaped["nodes"]}
        self.assertGreater(by_id["s"], by_id["c"])


class MergeCandidatesTests(unittest.TestCase):
    """E-G4 merge_candidates — 결정론 중복 개념 후보(순수). 자동 병합 금지 — 제안만."""

    def test_exact_normalized_title(self):
        rows = [{"id": "c1", "title": "iPaaS", "domain": "tech"},
                {"id": "c2", "title": "ipaas", "domain": "tech"}]
        cands = q.merge_candidates(rows)
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0]["reason"], "exact_title")
        self.assertEqual(cands[0]["score"], 1.0)
        self.assertTrue(cands[0]["same_domain"])

    def test_token_overlap_threshold(self):
        rows = [{"id": "a", "title": "gauge theory duality", "domain": "phys"},
                {"id": "b", "title": "gauge theory symmetry", "domain": "phys"},
                {"id": "c", "title": "완전 무관 주제", "domain": "phys"}]
        cands = q.merge_candidates(rows, min_score=0.4)
        pairs = {(x["a_id"], x["b_id"]) for x in cands}
        self.assertIn(("a", "b"), pairs)          # 2/4 토큰 공유(jaccard 0.5)
        self.assertNotIn(("a", "c"), pairs)       # 무관 — 후보 아님

    def test_subset_relation(self):
        rows = [{"id": "a", "title": "transformer", "domain": "ml"},
                {"id": "b", "title": "transformer architecture", "domain": "ml"}]
        cands = q.merge_candidates(rows)
        self.assertEqual(cands[0]["reason"], "subset")
        self.assertGreaterEqual(cands[0]["score"], 0.8)

    def test_deterministic_and_same_domain_priority(self):
        rows = [{"id": "a", "title": "vector", "domain": "x"},
                {"id": "b", "title": "vector", "domain": "y"},   # cross-domain dup
                {"id": "c", "title": "vector", "domain": "x"}]   # same-domain dup with a
        c1 = q.merge_candidates(rows)
        c2 = q.merge_candidates(rows)
        self.assertEqual(c1, c2)                          # 결정론
        self.assertTrue(c1[0]["same_domain"])             # 동일 도메인 우선

    def test_no_false_positive_on_disjoint(self):
        rows = [{"id": "a", "title": "alpha", "domain": "d"},
                {"id": "b", "title": "beta", "domain": "d"}]
        self.assertEqual(q.merge_candidates(rows), [])

    def test_recall_recency_is_default(self):
        plan = q.recall_plan(owner="u1")
        self.assertIn("ORDER BY e.date DESC", plan.cypher)
        self.assertNotIn("pref_score", plan.cypher)

    def test_recall_preference_plan_fetches_signal_columns_by_date(self):
        # 점수는 백엔드 밖(score_preference)에서 매긴다 — 플랜은 신호 열을 date DESC 로
        # 전량 가져올 뿐(Cypher 내 점수식·방언 없음, §4-2).
        plan = q.recall_plan(owner="u1", rank="preference")
        self.assertIn("access_count", plan.cypher)   # 점수 신호 투영
        self.assertIn("coalesce(e.pinned, false) AS pinned", plan.cypher)
        self.assertIn("ORDER BY e.date DESC", plan.cypher)
        self.assertNotIn("toFloat", plan.cypher)      # 방언 함수 제거
        self.assertNotIn("pref_score", plan.cypher)

    def test_score_preference_pin_and_frequency(self):
        rows = [
            {"id": "a", "pinned": False, "access_count": 0, "tags": []},
            {"id": "b", "pinned": True, "access_count": 0, "tags": []},   # pin boost
            {"id": "c", "pinned": False, "access_count": 100, "tags": []},  # freq
        ]
        ranked = q.score_preference(rows, limit=10)
        self.assertEqual(ranked[0]["id"], "b")        # w_pin(3) > w_freq 포화항
        self.assertEqual({r["id"] for r in ranked}, {"a", "b", "c"})
        self.assertTrue(all("score" in r for r in ranked))

    def test_score_preference_tag_overlap_only_when_tags(self):
        rows = [
            {"id": "a", "pinned": False, "access_count": 0, "tags": ["billing"]},
            {"id": "b", "pinned": False, "access_count": 0, "tags": ["x"]},
        ]
        ranked = q.score_preference(rows, tags=["billing", "kr"], limit=10)
        self.assertEqual(ranked[0]["id"], "a")        # 태그 겹침 부스트
        # 태그 미지정 시 겹침항 0 — 순서는 recency(입력 순서) 유지
        ranked0 = q.score_preference(rows, tags=None, limit=10)
        self.assertEqual([r["id"] for r in ranked0], ["a", "b"])

    def test_score_preference_custom_weights_and_limit(self):
        rows = [
            {"id": "a", "pinned": True, "access_count": 0, "tags": []},
            {"id": "b", "pinned": False, "access_count": 100, "tags": []},
        ]
        ranked = q.score_preference(rows, weights={"w_pin": 0.0}, limit=1)
        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0]["id"], "b")        # w_pin=0 → 빈도 우세

    def test_rrf_fuse_combines_arms(self):
        # RRF: 두 암(vector·BM25) 순위를 1/(k+rank) 합산 융합(§4-2, 순수 함수).
        vec = [{"id": "a"}, {"id": "b"}, {"id": "c"}]     # a 최상위
        fts = [{"id": "c"}, {"id": "a"}, {"id": "d"}]     # c 최상위
        fused = q.rrf_fuse([vec, fts], top_k=3)
        ids = [r["id"] for r in fused]
        # a: 1/61+1/62, c: 1/63+1/61 — a·c 가 양쪽 상위라 선두, 단일 등장 b/d 후순위
        self.assertEqual(set(ids[:2]), {"a", "c"})
        self.assertTrue(all("rrf_score" in r for r in fused))

    def test_rrf_single_arm_passthrough(self):
        fused = q.rrf_fuse([[{"id": "x"}, {"id": "y"}]], top_k=1)
        self.assertEqual(fused[0]["id"], "x")

    def test_decide_plan_depth_clamped(self):
        plan = q.decide_plan("g5.b.x", depth=99)
        self.assertIn("TRAIL 1..4", plan.cypher)  # clamped to 4

    def test_decide_plan_min_depth(self):
        plan = q.decide_plan("t", depth=0)
        self.assertIn("TRAIL 1..1", plan.cypher)

    def test_decide_plan_no_absent_id_property(self):
        # M7(§6-2): 라벨 없는 MATCH 는 어느 테이블엔가 있는 속성만 참조 가능.
        # `n.id` 는 모든 노드 테이블에 부재 → Binder 오류이자 사문이므로 금지.
        plan = q.decide_plan("t")
        self.assertNotIn("n.id", plan.cypher)
        for prop in ("n.concept_id", "n.bridge_id", "n.title"):
            self.assertIn(prop, plan.cypher)   # 실재 시작 노드 식별자/title

    def test_decide_plan_trail_guard_prevents_edge_reuse(self):
        # M3(§6-2): 기본 WALK 는 엣지 재사용 사이클 허용 → 경로 폭증. TRAIL 가드
        # (키워드가 범위 앞)로 엣지 무재사용. WALK 형식(`*1..`)은 남지 않아야 한다.
        # ⚠ decide 는 경로 자체를 소비하므로 G1(도달집합 SHORTEST 교체) 대상이 아니다.
        plan = q.decide_plan("t", depth=3)
        self.assertIn("TRAIL 1..3", plan.cypher)
        self.assertNotIn("*1..", plan.cypher)


class QueryNormalizationTests(unittest.TestCase):
    """A1.10 — 자연어 질문을 노드 검색이 탈 수 있는 토큰으로(순수 함수)."""

    def test_strips_particles_and_question_words(self):
        # 구 토큰 AND 를 깨뜨리던 것들이 사라져야 한다: 조사('제어가')·의문사('뭐야').
        self.assertEqual(q.normalize_query_tokens("낙관적 동시성 제어가 뭐야?"),
                         ["낙관적", "동시성", "제어"])
        self.assertEqual(q.normalize_query_tokens("환불은 며칠 이내에 되나요?"),
                         ["환불", "이내"])

    def test_strips_english_stopwords_and_punctuation(self):
        self.assertEqual(q.normalize_query_tokens("What is optimistic locking?"),
                         ["optimistic", "locking"])

    def test_all_stopwords_falls_back_instead_of_emptying(self):
        # 전부 불용어면 **비우지 않는다** — 빈 토큰은 전 노드를 삼키는 질의가 된다.
        self.assertEqual(q.normalize_query_tokens("뭐야?"), ["뭐야"])
        self.assertEqual(q.normalize_query_tokens("what is the"), ["what", "is", "the"])

    def test_empty_query_yields_nothing(self):
        self.assertEqual(q.normalize_query_tokens("   "), [])
        self.assertEqual(q.normalize_query_tokens(""), [])

    def test_dedup_and_cap(self):
        self.assertEqual(q.normalize_query_tokens("kappa kappa kappa beta"),
                         ["kappa", "beta"])
        self.assertEqual(len(q.normalize_query_tokens(" ".join(f"t{i}" for i in range(20)))), 8)

    def test_stem_never_shrinks_below_two_chars(self):
        # 조사 절단은 어간이 2자 이상 남을 때만 — '가'·'이' 한 글자만 남기지 않는다.
        self.assertEqual(q.normalize_query_tokens("국가"), ["국가"])      # 절단 안 함
        self.assertEqual(q.normalize_query_tokens("결과가"), ["결과"])    # 절단
        for tok in q.normalize_query_tokens("국가 종이 결과가 환불은 제어가"):
            self.assertGreaterEqual(len(tok), 2)


class WeightedPathTests(unittest.TestCase):
    """E-G8 confidence 가중 최단 경로 — 순수 함수(엔진 무관)."""

    # 홉 최단 = a-y-z(2홉, 저신뢰) · 가중 최단 = a-b-c-z(3홉, 고신뢰)
    EDGES = [
        {"source": "a", "target": "b", "confidence": 0.9},
        {"source": "b", "target": "c", "confidence": 0.9},
        {"source": "c", "target": "z", "confidence": 0.9},
        {"source": "a", "target": "y", "confidence": 0.2},
        {"source": "y", "target": "z", "confidence": 0.2},
    ]

    def test_prefers_confident_chain_over_fewer_hops(self):
        paths = q.weighted_paths(self.EDGES, "a", "z", max_hops=6)
        self.assertEqual(paths[0]["ids"], ["a", "b", "c", "z"])
        self.assertAlmostEqual(paths[0]["cost"], 0.3, places=6)   # 3 * (1-0.9)
        self.assertEqual(paths[0]["length"], 3)

    def test_degenerates_to_hop_shortest_without_confidence(self):
        # 증거가 하나도 없으면 모든 엣지 비용이 1.0 → 홉 최단과 같은 답(하위호환).
        bare = [{k: v for k, v in e.items() if k != "confidence"} for e in self.EDGES]
        paths = q.weighted_paths(bare, "a", "z", max_hops=6)
        self.assertEqual(paths[0]["ids"], ["a", "y", "z"])
        self.assertEqual(paths[0]["length"], 2)

    def test_hop_cap_is_honoured(self):
        # 상한 2 면 고신뢰 3홉 체인은 후보에서 빠지고 저신뢰 2홉만 남는다.
        paths = q.weighted_paths(self.EDGES, "a", "z", max_hops=2)
        self.assertEqual(paths[0]["ids"], ["a", "y", "z"])

    def test_undirected_and_unreachable(self):
        self.assertEqual(q.weighted_paths(self.EDGES, "z", "a", max_hops=6)[0]["ids"],
                         ["z", "c", "b", "a"])          # 무방향
        self.assertEqual(q.weighted_paths(self.EDGES, "a", "nope", max_hops=6), [])

    def test_ties_returned_and_deterministic(self):
        # 동률 두 경로(비용 동일) — 둘 다 나오고 순서가 결정론이다.
        edges = [{"source": "a", "target": "p", "confidence": 0.5},
                 {"source": "p", "target": "z", "confidence": 0.5},
                 {"source": "a", "target": "q", "confidence": 0.5},
                 {"source": "q", "target": "z", "confidence": 0.5}]
        first = q.weighted_paths(edges, "a", "z", max_hops=4)
        self.assertEqual([p["ids"] for p in first],
                         [["a", "p", "z"], ["a", "q", "z"]])
        self.assertEqual(first, q.weighted_paths(edges, "a", "z", max_hops=4))

    def test_multi_edge_keeps_strongest(self):
        # 같은 쌍에 엣지가 여럿이면 가장 신뢰도 높은 것(=가장 싼 것)만 쓴다.
        edges = [{"source": "a", "target": "z", "confidence": 0.1},
                 {"source": "a", "target": "z", "confidence": 0.8}]
        self.assertAlmostEqual(q.weighted_paths(edges, "a", "z")[0]["cost"], 0.2, places=6)

    def test_out_of_range_confidence_clamped(self):
        for bad, want in ((5.0, 0.0), (-3.0, 1.0), ("x", 1.0), (None, 1.0)):
            cost = q.weighted_paths([{"source": "a", "target": "z", "confidence": bad}],
                                    "a", "z")[0]["cost"]
            self.assertAlmostEqual(cost, want, places=6, msg=f"confidence={bad!r}")

    def test_candidates_plan_allows_depth_up_to_8(self):
        # ego 뷰(4 클램프)와 달리 경로 상한 8까지 연다.
        self.assertIn("SHORTEST 1..8", q.path_candidates_plan("x", depth=99).cypher)
        self.assertIn("SHORTEST 1..1", q.path_candidates_plan("x", depth=0).cypher)
        self.assertIn("n.owner_id = $owner",
                      q.path_candidates_plan("x", owner="u").cypher)


class ExecuteTests(unittest.TestCase):
    @mock.patch("api.query.open_store", return_value=None)
    def test_execute_without_db_degrades_without_leaking_plan(self, _open):
        """F3 — 스토어가 없어도 **plan 은 안 싣는다.**

        여태 `plan.cypher` 전문과 `plan.params.owner`(회원 UUID)가 매 응답에 실렸다
        (실측: `vinc_recall` 응답 17.9KB · `vinc_domains` 1.3KB). 호출당 수백 토큰의
        낭비이자 내부 구현 노출이고, owner 는 요청자가 이미 아는 값이라 되돌려줄
        이유가 없다. 강등 경로도 예외가 아니다 — 여기가 오히려 새기 쉬운 자리다."""
        res = q.recall("x")
        self.assertFalse(res["executed"])
        self.assertNotIn("plan", res, f"plan 이 기본 응답에 실렸다: {res.get('plan')}")
        self.assertEqual(res["rows"], [])

    @mock.patch("api.query.open_store", return_value=None)
    def test_debug_는_옵트인으로_plan_을_되돌려준다(self, _open):
        """진단 능력을 없앤 게 아니라 옵트인으로 내린 것이다 — 그 증거."""
        res = q.recall("x", debug=True)
        self.assertEqual(res["plan"]["kind"], "recall")
        self.assertIn("cypher", res["plan"])

    def test_search_entrypoint(self):
        # search 는 store.fulltext_search + (선택)vector_search + RRF(§4-2). 인덱스
        # 미구축 시 executed=False 로 강등(무 크래시). plan dict 아님(kind=search).
        res = q.search("query")
        self.assertEqual(res["kind"], "search")
        self.assertIn("executed", res)
        self.assertEqual(res["rows"], [])

    def test_decide_entrypoint(self):
        # A1.9(A5): decide 는 raw plan 을 노출하지 않고 정규화 계약
        # {kind, executed, topic, nodes, edges, conflicts}(MCP-INTERFACE §4)를 반환한다.
        # 스토어 없음 → executed:False 로 강등(무 크래시), 필드는 존재.
        res = q.decide("topic")
        self.assertEqual(res["kind"], "decide")
        self.assertIn("executed", res)
        for k in ("nodes", "edges", "conflicts"):
            self.assertEqual(res[k], [])


if __name__ == "__main__":
    unittest.main()

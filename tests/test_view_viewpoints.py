# -*- coding: utf-8 -*-
"""View 평면 관점(D43) 백엔드 계약 — 실엔진 바인딩 + 원장 롤업.

**왜 실엔진인가**: 이 변경의 유일한 실질 위험은 Kùzu **Binder 오류**다. 관계 열
(`r.evidence`/`r.confidence`)을 그 열이 없는 rel 테이블(CONNECTS·ATTACHES_TO·
TYPED)이 바인딩 후보인 플랜에 붙이면 바인드 시점에 터지고, 그러면 `/graph` 가
새 관점뿐 아니라 **모든 View 레벨에서** 죽는다. 순수 플랜 문자열 비교로는 절대
못 잡는 층이라 갓 프로비저닝한 임베디드 스키마에 실제로 실행해 본다
([[vinc-owner-isolation-sec1]] 의 "스텁 말고 실엔진" 과 같은 태도).

임베디드 드라이버(ladybug) 미설치 시 skip — test_embedded_backend 와 동일 조건.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from store.embedded_backend import _import_driver  # noqa: E402

_DRIVER, _NAME = _import_driver()


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class ViewViewpointsBackend(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._env = {}
        for k, v in {
            "MEMORY_STORE_BACKEND": "embedded",
            "MEMORY_DATA_DIR": os.path.join(self._tmp, "store"),
            "MEMORY_EMBEDDING_MODEL": "local-en",
        }.items():
            self._env[k] = os.environ.get(k)
            os.environ[k] = v

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _fresh_store(self):
        from store.config import load_config
        from store.backend import open_store
        cfg = load_config()
        store = open_store(cfg)
        self.assertIsNotNone(store)
        store.provision(dim=cfg.embedding_dim)
        return store

    def test_widened_plans_bind_on_fresh_schema(self):
        """D43 위험 #1 — 투영이 넓어진 플랜과 신규 원장 플랜이 **갓 만든 스키마에서
        실행**되는가. `execute` 는 Binder 오류를 executed=False 로 강등하므로
        executed 가 곧 바인드 성공 신호다(note 에 사유가 실린다)."""
        from api import query as q

        store = self._fresh_store()
        plans = {
            # ts + evidence/confidence 를 얻은 3종(CC 4종·ABOUT 만 바인딩 후보)
            "graph_plan": q.graph_plan(owner="u"),
            "graph_plan(domain)": q.graph_plan(owner="u", domain="d"),
            "graph_rows_for_ids_plan": q.graph_rows_for_ids_plan(["concept:a"], owner="u"),
            "episode_rows_plan(domain)": q.episode_rows_plan(owner="u", domain="d"),
            "episode_rows_plan(ids)": q.episode_rows_plan(owner="u", ids=["concept:a"]),
            # ts 만 얻은 3종(evidence 열이 없는 rel 이라 절대 붙이지 않는다)
            "bridge_rows_plan(domain)": q.bridge_rows_plan(owner="u", domain="d"),
            "bridge_rows_plan(ids)": q.bridge_rows_plan(owner="u", ids=["concept:a"]),
            "record_rows_plan(domain)": q.record_rows_plan(owner="u", domain="d"),
            "record_rows_plan(ids)": q.record_rows_plan(owner="u", ids=["concept:a"]),
            "entity_rows_for_ids_plan": q.entity_rows_for_ids_plan(["entity:a"], owner="u"),
            # 신규 원장 플랜(도메인 절 유무 양쪽 — $domain 은 낼 때만 바인딩)
            "ledger_origin_plan": q.ledger_origin_plan(owner="u"),
            "ledger_origin_plan(domain)": q.ledger_origin_plan(owner="u", domain="d"),
            "ledger_link_plan": q.ledger_link_plan(owner="u"),
            "ledger_link_plan(domain)": q.ledger_link_plan(owner="u", domain="d"),
        }
        failed = []
        for name, plan in plans.items():
            res = q.execute(plan, store=store)
            if not res.get("executed"):
                failed.append(f"{name}: {res.get('note')}")
        store.close()
        self.assertEqual(failed, [], "플랜이 실엔진에서 바인드 실패 — /graph 전면 정지 위험")

    def test_graph_carries_node_ts_and_edge_evidence(self):
        """6.1/6.2 — 노드 ts 와 엣지 confidence/evidence 가 shape_graph 를 통과한다.
        상류가 안 쓴 관계는 키 자체가 붙지 않는다(role 과 동형 — 하위호환)."""
        from api import query as q

        store = self._fresh_store()
        with store.session() as s:
            for cid, title in (("a", "A"), ("b", "B"), ("c", "C")):
                s.run("MERGE (n:Concept {concept_id:$id}) SET n.title=$t, n.owner_id='u', "
                      "n.domain='math', n.created='2026-07-01', n.updated_at='2026-07-02'",
                      id=cid, t=title)
            s.run("MATCH (x:Concept{concept_id:'a'}),(y:Concept{concept_id:'b'}) "
                  "MERGE (x)-[r:RELATED_TO]->(y) SET r.evidence='본문 근거', r.confidence=0.8")
            s.run("MATCH (x:Concept{concept_id:'b'}),(y:Concept{concept_id:'c'}) "
                  "MERGE (x)-[:PREREQUISITE_OF]->(y)")   # 미저작 관계

        g = q.graph(owner="u", store=store)
        store.close()
        self.assertTrue(g["executed"], g.get("note"))
        by_id = {n["id"]: n for n in g["nodes"]}
        self.assertEqual(by_id["a"]["ts"], "2026-07-01")   # created 우선

        edges = {(e["source"], e["target"]): e for e in g["edges"]}
        sourced = edges[("a", "b")]
        self.assertAlmostEqual(sourced["confidence"], 0.8)
        self.assertEqual(sourced["evidence"], "본문 근거")
        bare = edges[("b", "c")]
        self.assertNotIn("confidence", bare)
        self.assertNotIn("evidence", bare)

    def test_ts_falls_back_to_updated_at_and_may_be_empty(self):
        """ts 계약 — created 부재 시 updated_at, 둘 다 없으면 정당하게 ''(소비처는
        가정 말고 강등해야 한다). Episode 는 date 가 1순위."""
        from api import query as q

        store = self._fresh_store()
        with store.session() as s:
            s.run("MERGE (n:Concept {concept_id:'nots'}) SET n.title='N', n.owner_id='u', "
                  "n.domain='d'")
            s.run("MERGE (n:Concept {concept_id:'upd'}) SET n.title='U', n.owner_id='u', "
                  "n.domain='d', n.updated_at='2026-07-05T00:00:00.000Z'")
            s.run("MERGE (e:Episode {episode_id:'ep'}) SET e.title='E', e.owner_id='u', "
                  "e.domain='d', e.date='2026-07-03', e.updated_at='2026-07-09'")
            s.run("MATCH (e:Episode{episode_id:'ep'}),(c:Concept{concept_id:'upd'}) "
                  "MERGE (e)-[:ABOUT]->(c)")

        g = q.graph(owner="u", domain="d", store=store)
        store.close()
        self.assertTrue(g["executed"], g.get("note"))
        by_id = {n["id"]: n for n in g["nodes"]}
        self.assertEqual(by_id["nots"]["ts"], "")                          # 시간 원천 없음
        self.assertEqual(by_id["upd"]["ts"], "2026-07-05T00:00:00.000Z")   # updated_at 폴백
        self.assertEqual(by_id["ep"]["ts"], "2026-07-03")                  # date 우선

    def test_graph_scopes_edge_targets_to_owner(self):
        """SEC-1 — `graph_plan` 의 OPTIONAL MATCH 대상(먼 끝점 d)도 owner 스코프다.
        교차 소유자 엣지가 물리적으로 존재해도 남의 노드·증거 문장·신뢰도·시간이
        alice 의 `/graph` 에 실리면 안 된다 — D43 이 evidence/confidence/target_ts 를
        투영하며 누출 표면이 넓어졌다(Flow 툴팁까지 도달)."""
        from api import query as q

        store = self._fresh_store()
        with store.session() as s:
            s.run("MERGE (n:Concept {concept_id:'ca'}) SET n.title='Alice A', "
                  "n.owner_id='user:alice', n.domain='math', n.created='2026-07-01'")
            s.run("MERGE (n:Concept {concept_id:'cb'}) SET n.title='Bob Secret', "
                  "n.owner_id='user:bob', n.domain='math', n.created='2026-07-02'")
            s.run("MATCH (x:Concept{concept_id:'ca'}),(y:Concept{concept_id:'cb'}) "
                  "MERGE (x)-[r:RELATED_TO]->(y) SET r.evidence='밥의 비밀 근거', "
                  "r.confidence=0.9")

        g = q.graph(owner="user:alice", store=store)
        store.close()
        self.assertTrue(g["executed"], g.get("note"))
        # 주제 마크(`domain:*`)는 이 검사의 대상이 아니다 — 배치가 내는 구조 마크이지
        # 소유자 데이터가 아니다(2026-08-14 부터 `/graph` 가 함께 낸다). 검사의 주어는
        # "bob 의 개념이 alice 의 응답에 실리는가"이므로 개념만 남겨 비교한다.
        concepts = [n["id"] for n in g["nodes"] if not n["id"].startswith("domain:")]
        self.assertEqual(concepts, ["ca"], "bob 노드 노출 — SEC-1 위반")
        self.assertEqual(g["edges"], [], "교차 소유자 엣지 노출 — SEC-1 위반")
        blob = json.dumps(g, ensure_ascii=False)
        for leak in ("밥의 비밀 근거", "Bob Secret", "2026-07-02"):
            self.assertNotIn(leak, blob, f"bob 의 데이터({leak})가 alice 응답에 실림")

    def test_topic_scope_includes_the_subtree_and_takes_several(self):
        """주제 스코프는 하위 트리다. 그리고 여러 주제를 한 번에 받는다.

        정확일치 하나만 보던 동안 상위 주제를 고르면 **0 노드**가 나왔다 — 계층은
        경로의 `/` 로 이미 있었고 배치와 좌측 트리가 그걸 읽는데 조회만 안 읽었다.
        접두는 `d + "/"` 여야 한다: `STARTS WITH 'vinc'` 는 `vincent` 도 잡는다."""
        from api import query as q

        store = self._fresh_store()
        with store.session() as s:
            for cid, dom in (("a", "vinc"), ("b", "vinc/design"),
                             ("c", "vinc/design/tokens"), ("d", "vincent"),
                             ("e", "neo4j/storage")):
                s.run("MERGE (n:Concept {concept_id:$id}) SET n.title=$id, "
                      "n.owner_id='u', n.domain=$dom", id=cid, dom=dom)

        def ids(**kw):
            return {n["id"] for n in q.graph(owner="u", store=store, **kw)["nodes"]
                    if n["id"] in {"a", "b", "c", "d", "e"}}

        # 상위 하나 → 자기 + 하위 전부. 형제 접두(`vincent`)는 안 딸려 온다.
        self.assertEqual(ids(domain="vinc"), {"a", "b", "c"})
        # 중간 마디 → 그 아래만.
        self.assertEqual(ids(domain="vinc/design"), {"b", "c"})
        # 잎 → 자기만.
        self.assertEqual(ids(domain="vinc/design/tokens"), {"c"})
        # 여럿 → 합집합. 순서와 무관하고 겹쳐도 한 번씩만 센다.
        self.assertEqual(ids(domains=["vinc/design", "neo4j/storage"]), {"b", "c", "e"})
        self.assertEqual(ids(domains=["vinc", "vinc/design"]), {"a", "b", "c"})
        store.close()

    def test_episode_seeded_l2_keeps_concept_dates(self):
        """축 플랜으로만 도착하는 개념도 ``ts`` 를 갖는다 — episode 시드 L2
        (`#view/trace/episode:…`)에서 개념이 전부 무날짜가 되면 Timeline 이 실제로는
        전량 날짜가 있는데도 '날짜 없음' 빈 상태로 강등된다."""
        from api import query as q

        store = self._fresh_store()
        with store.session() as s:
            s.run("MERGE (n:Concept {concept_id:'ca'}) SET n.title='A', n.owner_id='u', "
                  "n.domain='math', n.created='2026-07-01'")
            s.run("MERGE (n:Concept {concept_id:'cb'}) SET n.title='B', n.owner_id='u', "
                  "n.domain='math', n.updated_at='2026-07-04T00:00:00.000Z'")
            s.run("MERGE (e:Episode {episode_id:'episode:ep'}) SET e.title='E', "
                  "e.owner_id='u', e.domain='math', e.date='2026-07-03'")
            for cid in ("ca", "cb"):
                s.run("MATCH (e:Episode{episode_id:'episode:ep'}),(c:Concept{concept_id:$id}) "
                      "MERGE (e)-[:ABOUT]->(c)", id=cid)

        g = q.graph(owner="u", seed="episode:ep", store=store)
        store.close()
        self.assertTrue(g["executed"], g.get("note"))
        by_id = {n["id"]: n for n in g["nodes"]}
        self.assertEqual(sorted(by_id), ["ca", "cb", "episode:ep"])
        self.assertEqual(by_id["episode:ep"]["ts"], "2026-07-03")
        self.assertEqual(by_id["ca"]["ts"], "2026-07-01")
        self.assertEqual(by_id["cb"]["ts"], "2026-07-04T00:00:00.000Z")
        self.assertEqual([n["id"] for n in g["nodes"] if not n["ts"]], [])

    def test_ledger_origin_is_a_strict_partition(self):
        """출처 3분할이 **배타**다(episodic > record > unsourced) — 에피소드와 레코드에
        동시 부착된 개념이 두 칸에 중복 계상되면 소비처(Totals)가 셋을 더해 만드는
        행 합계와 막대가 부푼다."""
        from api import query as q

        store = self._fresh_store()
        with store.session() as s:
            for cid in ("both", "epi", "rec", "none"):
                s.run("MERGE (n:Concept {concept_id:$id}) SET n.title=$id, n.owner_id='u', "
                      "n.domain='math'", id=cid)
            s.run("MERGE (e:Episode {episode_id:'ep'}) SET e.owner_id='u', e.domain='math'")
            s.run("MERGE (rc:Record {record_id:'rc'}) SET rc.owner_id='u', rc.domain='math'")
            for cid in ("both", "epi"):
                s.run("MATCH (e:Episode{episode_id:'ep'}),(c:Concept{concept_id:$id}) "
                      "MERGE (e)-[:ABOUT]->(c)", id=cid)
            for cid in ("both", "rec"):
                s.run("MATCH (rc:Record{record_id:'rc'}),(c:Concept{concept_id:$id}) "
                      "MERGE (rc)-[:ATTACHES_TO]->(c)", id=cid)

        res = q.ledger(owner="u", store=store)
        store.close()
        self.assertTrue(res["executed"], res.get("note"))
        m = {r["domain"]: r for r in res["rows"]}["math"]
        self.assertEqual(m["concepts"], 4)
        # both 는 에피소드 칸에만(우선순위) — 겹침이 흘렀다면 (2, 2, 1) 이 된다
        self.assertEqual((m["episodic"], m["record"], m["unsourced"]), (2, 1, 1))
        self.assertEqual(m["episodic"] + m["record"] + m["unsourced"], m["concepts"])
        self.assertEqual(m["connected"] + m["gateway"] + m["orphan"], m["concepts"])

    def test_ledger_rollup_counts_origin_and_link_state(self):
        """6.3 — 출처 3분할(episodic/record/unsourced)과 연결 3분할(connected/
        gateway/orphan)이 각각 concepts 를 정확히 나눈다."""
        from api import query as q

        store = self._fresh_store()
        with store.session() as s:
            # math: linked(연결) · gate(관문) · lone(고아) · rec(레코드 부착, 고아)
            for cid in ("linked", "gate", "lone", "rec"):
                s.run("MERGE (n:Concept {concept_id:$id}) SET n.title=$id, n.owner_id='u', "
                      "n.domain='math'", id=cid)
            s.run("MERGE (n:Concept {concept_id:'far'}) SET n.title='far', n.owner_id='u', "
                  "n.domain='art'")
            s.run("MATCH (a:Concept{concept_id:'linked'}),(b:Concept{concept_id:'gate'}) "
                  "MERGE (a)-[:RELATED_TO]->(b)")
            s.run("MATCH (a:Concept{concept_id:'gate'}),(b:Concept{concept_id:'far'}) "
                  "MERGE (a)-[:RELATED_TO]->(b)")            # 도메인 교차 → gate 는 관문
            s.run("MERGE (e:Episode {episode_id:'ep'}) SET e.owner_id='u', e.domain='math'")
            s.run("MATCH (e:Episode{episode_id:'ep'}),(c:Concept{concept_id:'linked'}) "
                  "MERGE (e)-[:ABOUT]->(c)")
            s.run("MERGE (rc:Record {record_id:'rc'}) SET rc.owner_id='u', rc.domain='math'")
            s.run("MATCH (rc:Record{record_id:'rc'}),(c:Concept{concept_id:'rec'}) "
                  "MERGE (rc)-[:ATTACHES_TO]->(c)")

        res = q.ledger(owner="u", store=store)
        self.assertTrue(res["executed"], res.get("note"))
        rows = {r["domain"]: r for r in res["rows"]}
        m = rows["math"]
        self.assertEqual(m["concepts"], 4)
        self.assertEqual((m["episodic"], m["record"], m["unsourced"]), (1, 1, 2))
        # linked=연결 · gate=관문 · lone/rec=고아
        self.assertEqual((m["connected"], m["gateway"], m["orphan"]), (1, 1, 2))
        self.assertEqual(res["rows"][0]["domain"], "math")   # concepts 내림차순

        scoped = q.ledger(owner="u", domain="art", store=store)
        store.close()
        self.assertTrue(scoped["executed"], scoped.get("note"))
        self.assertEqual([r["domain"] for r in scoped["rows"]], ["art"])
        self.assertEqual(scoped["rows"][0]["gateway"], 1)    # far 도 반대편 관문

    def test_ledger_isolates_owners_and_keeps_legacy_bridges(self):
        """SEC-1 — 원장은 owner 필수이고 엣지 패턴의 **양 끝**을 필터한다. Bridge 만
        과도기 술어(owner OR '')라 레거시 미소유 결합이 살아 남는다(F5 계약)."""
        from api import query as q

        store = self._fresh_store()
        with store.session() as s:
            for owner, cid in (("user:alice", "ca"), ("user:bob", "cb")):
                s.run("MERGE (n:Concept {concept_id:$id}) SET n.title=$id, n.owner_id=$o, "
                      "n.domain='math'", id=cid, o=owner)
            # bob 의 개념에 alice 개념을 잇는 엣지(쓰기 경로가 막지만 방어층 검증)
            s.run("MATCH (a:Concept{concept_id:'ca'}),(b:Concept{concept_id:'cb'}) "
                  "MERGE (a)-[:RELATED_TO]->(b)")
            # bob 의 에피소드가 alice 개념에 부착 — 원장에 실리면 누출
            s.run("MERGE (e:Episode {episode_id:'eb'}) SET e.owner_id='user:bob', e.domain='math'")
            s.run("MATCH (e:Episode{episode_id:'eb'}),(c:Concept{concept_id:'ca'}) "
                  "MERGE (e)-[:ABOUT]->(c)")
            # 레거시 미소유 브릿지가 alice 개념에 연결
            s.run("MERGE (b:Bridge {bridge_id:'legacy'}) SET b.owner_id='', "
                  "b.source_domain='math', b.target_domain='art'")
            s.run("MATCH (b:Bridge{bridge_id:'legacy'}),(c:Concept{concept_id:'ca'}) "
                  "MERGE (b)-[:CONNECTS]->(c)")

        res = q.ledger(owner="user:alice", store=store)
        store.close()
        self.assertTrue(res["executed"], res.get("note"))
        rows = {r["domain"]: r for r in res["rows"]}
        self.assertEqual(list(rows), ["math"])
        m = rows["math"]
        self.assertEqual(m["concepts"], 1)          # bob 개념 미혼입
        self.assertEqual(m["episodic"], 0, "bob 에피소드가 alice 원장에 실림 — SEC-1 위반")
        self.assertEqual(m["unsourced"], 1)
        # 레거시 브릿지 팔이 살아 있어 고아가 아니고, math↔art 라 관문
        self.assertEqual(m["orphan"], 0)
        self.assertEqual(m["gateway"], 1)

    def test_ledger_route_requires_owner(self):
        """라우트 계약 — `/ledger` 는 owner 선택형(/search·/decide)이 아니라 필수다."""
        from api.server import VincApp

        store = self._fresh_store()
        app = VincApp(store=store)
        code, out = app.read_ledger({"owner": "u"})
        self.assertEqual(code, 200, out)
        self.assertTrue(out.get("executed"), out)
        with self.assertRaises(Exception):
            app.read_ledger({})
        store.close()

    # ── ego 원장(D43 정련 — L2 Totals 시드 스코프) ────────────────────────────

    def test_ego_ledger_plans_bind_on_fresh_schema(self):
        """ego 위험 #1 — 두 원장 플랜의 ids 모드가 갓 만든 스키마에서 바인드된다.
        UNION 분기 하나에서만 ``$ids`` 를 빠뜨려도 Binder 오류로 플랜 전체가 죽는
        위험 클래스(기존 bind 테스트와 같은 층 — 순수 문자열 비교로는 못 잡는다)."""
        from api import query as q

        store = self._fresh_store()
        plans = {
            "ledger_origin_plan(ids)": q.ledger_origin_plan(owner="u", ids=["concept:a"]),
            "ledger_link_plan(ids)": q.ledger_link_plan(owner="u", ids=["concept:a"]),
        }
        failed = []
        for name, plan in plans.items():
            res = q.execute(plan, store=store)
            if not res.get("executed"):
                failed.append(f"{name}: {res.get('note')}")
        store.close()
        self.assertEqual(failed, [], "ego 원장 플랜이 실엔진에서 바인드 실패")

    def test_ego_ledger_rollup_splits_domains(self):
        """ego 기본 — 모집단은 L2 순회(depth 2)와 동일: 시드 ∪ 도달 개념(3홉 밖
        제외), 행은 도메인별로 갈라지고 두 3분할이 각각 concepts 를 정확히 나눈다.
        scope="seed"·population 에코."""
        from api import query as q

        store = self._fresh_store()
        with store.session() as s:
            for cid, dom in (("s", "math"), ("n1", "math"), ("n2", "art"),
                             ("deep2", "math"), ("deep3", "math")):
                s.run("MERGE (n:Concept {concept_id:$id}) SET n.title=$id, n.owner_id='u', "
                      "n.domain=$d", id=cid, d=dom)
            for a, b in (("s", "n1"), ("s", "n2"), ("n1", "deep2"), ("deep2", "deep3")):
                s.run("MATCH (x:Concept{concept_id:$a}),(y:Concept{concept_id:$b}) "
                      "MERGE (x)-[:RELATED_TO]->(y)", a=a, b=b)
            s.run("MERGE (e:Episode {episode_id:'ep'}) SET e.owner_id='u', e.domain='math'")
            s.run("MATCH (e:Episode{episode_id:'ep'}),(c:Concept{concept_id:'s'}) "
                  "MERGE (e)-[:ABOUT]->(c)")

        res = q.ledger(owner="u", seed="s", store=store)
        store.close()
        self.assertTrue(res["executed"], res.get("note"))
        self.assertEqual(res["scope"], "seed")
        self.assertEqual(res["population"], 4)      # s·n1·n2·deep2 — deep3 은 3홉 밖
        rows = {r["domain"]: r for r in res["rows"]}
        self.assertEqual(sorted(rows), ["art", "math"])
        m = rows["math"]
        self.assertEqual(m["concepts"], 3)
        self.assertEqual((m["episodic"], m["record"], m["unsourced"]), (1, 0, 2))
        # s 는 math↔art 엣지로 관문, n1·deep2 는 연결(deep2-deep3 은 동일 도메인)
        self.assertEqual((m["connected"], m["gateway"], m["orphan"]), (2, 1, 0))
        self.assertEqual(m["episodic"] + m["record"] + m["unsourced"], m["concepts"])
        self.assertEqual(m["connected"] + m["gateway"] + m["orphan"], m["concepts"])
        a = rows["art"]
        self.assertEqual((a["concepts"], a["gateway"], a["orphan"]), (1, 1, 0))
        self.assertEqual(res["rows"][0]["domain"], "math")   # concepts 내림차순 유지

    def test_ego_ledger_link_state_is_global(self):
        """전역 연결상태 — ego 를 떠나는 크로스도메인 엣지도 구성원의 차수·관문으로
        센다(먼 끝점은 owner 스코프만, ego 비제한). 바깥 개념 자신은 행을 만들지
        않는다(모집단 밖)."""
        from api import query as q

        store = self._fresh_store()
        with store.session() as s:
            for cid, dom in (("s", "math"), ("n1", "math"), ("ext", "art")):
                s.run("MERGE (n:Concept {concept_id:$id}) SET n.title=$id, n.owner_id='u', "
                      "n.domain=$d", id=cid, d=dom)
            for a, b in (("s", "n1"), ("n1", "ext")):
                s.run("MATCH (x:Concept{concept_id:$a}),(y:Concept{concept_id:$b}) "
                      "MERGE (x)-[:RELATED_TO]->(y)", a=a, b=b)

        res = q.ledger(owner="u", seed="s", depth=1, store=store)   # ego = {s, n1}
        store.close()
        self.assertTrue(res["executed"], res.get("note"))
        self.assertEqual(res["scope"], "seed")
        self.assertEqual(res["population"], 2)
        self.assertEqual([r["domain"] for r in res["rows"]], ["math"])   # ext 행 없음
        m = res["rows"][0]
        self.assertEqual(m["concepts"], 2)
        # n1 은 ego 밖 art 개념과의 엣지로 관문 — 전역 차수가 아니면 s·n1 둘 다 연결
        self.assertEqual((m["connected"], m["gateway"], m["orphan"]), (1, 1, 0))

    def test_ego_ledger_isolates_owners(self):
        """SEC-1 — 시드에 인접한 타인 개념은 모집단에서 빠지고 **차수에도 기여하지
        않는다**(순회 m·연결 축 o 모두 owner 스코프). 타인 에피소드 부착도 출처로
        실리지 않는다."""
        from api import query as q

        store = self._fresh_store()
        with store.session() as s:
            for owner, cid in (("user:alice", "as"), ("user:bob", "bs")):
                s.run("MERGE (n:Concept {concept_id:$id}) SET n.title=$id, n.owner_id=$o, "
                      "n.domain='math'", id=cid, o=owner)
            s.run("MATCH (a:Concept{concept_id:'as'}),(b:Concept{concept_id:'bs'}) "
                  "MERGE (a)-[:RELATED_TO]->(b)")
            s.run("MERGE (e:Episode {episode_id:'eb'}) SET e.owner_id='user:bob', "
                  "e.domain='math'")
            s.run("MATCH (e:Episode{episode_id:'eb'}),(c:Concept{concept_id:'as'}) "
                  "MERGE (e)-[:ABOUT]->(c)")

        res = q.ledger(owner="user:alice", seed="as", store=store)
        store.close()
        self.assertTrue(res["executed"], res.get("note"))
        self.assertEqual(res["scope"], "seed")
        self.assertEqual(res["population"], 1, "bob 개념이 모집단에 혼입 — SEC-1 위반")
        m = {r["domain"]: r for r in res["rows"]}["math"]
        self.assertEqual(m["concepts"], 1)
        self.assertEqual(m["episodic"], 0, "bob 에피소드가 출처로 실림 — SEC-1 위반")
        self.assertEqual(m["unsourced"], 1)
        self.assertEqual(m["orphan"], 1, "교차 소유자 엣지가 차수에 기여 — SEC-1 위반")
        self.assertEqual(m["gateway"], 0)

    def test_ego_ledger_orphan_seed(self):
        """고아 시드 단독 — 순회 0행이어도 시드 자신이 모집단이다(population 1,
        고아 1). '시드 미존재'(population 0)와 구분된다."""
        from api import query as q

        store = self._fresh_store()
        with store.session() as s:
            s.run("MERGE (n:Concept {concept_id:'lone'}) SET n.title='L', n.owner_id='u', "
                  "n.domain='math'")

        res = q.ledger(owner="u", seed="lone", store=store)
        store.close()
        self.assertTrue(res["executed"], res.get("note"))
        self.assertEqual(res["scope"], "seed")
        self.assertEqual(res["population"], 1)
        m = res["rows"][0]
        self.assertEqual((m["domain"], m["concepts"]), ("math", 1))
        self.assertEqual((m["unsourced"], m["orphan"]), (1, 1))

    def test_ego_ledger_episode_seed_population_is_attachments(self):
        """episode: 시드 — 모집단은 부착 개념(L2 접착제와 동일 의미). 부착이 곧
        출처라 전원 episodic 이고, 개념 간 엣지가 없으면 고아다."""
        from api import query as q

        store = self._fresh_store()
        with store.session() as s:
            for cid, dom in (("c1", "math"), ("c2", "art")):
                s.run("MERGE (n:Concept {concept_id:$id}) SET n.title=$id, n.owner_id='u', "
                      "n.domain=$d", id=cid, d=dom)
            s.run("MERGE (e:Episode {episode_id:'episode:ep'}) SET e.title='E', "
                  "e.owner_id='u', e.domain='math'")
            for cid in ("c1", "c2"):
                s.run("MATCH (e:Episode{episode_id:'episode:ep'}),(c:Concept{concept_id:$id}) "
                      "MERGE (e)-[:ABOUT]->(c)", id=cid)

        res = q.ledger(owner="u", seed="episode:ep", store=store)
        store.close()
        self.assertTrue(res["executed"], res.get("note"))
        self.assertEqual(res["scope"], "seed")
        self.assertEqual(res["population"], 2)
        rows = {r["domain"]: r for r in res["rows"]}
        self.assertEqual(sorted(rows), ["art", "math"])
        for dom in ("math", "art"):
            self.assertEqual((rows[dom]["concepts"], rows[dom]["episodic"],
                              rows[dom]["orphan"]), (1, 1, 1))

    def test_ego_ledger_empty_ego_reports_population_zero(self):
        """빈 ego 계약 — 미존재 시드·부착 0 에피소드 시드 모두 executed=true ·
        rows=[] · scope="seed" · population 0 (소비처는 totalsEmpty). 빈 리스트를
        ``IN $ids`` 에 바인딩하지 않는다."""
        from api import query as q

        store = self._fresh_store()
        with store.session() as s:
            s.run("MERGE (e:Episode {episode_id:'episode:bare'}) SET e.owner_id='u', "
                  "e.domain='math'")   # 부착 0

        for seed in ("ghost", "episode:bare"):
            res = q.ledger(owner="u", seed=seed, store=store)
            self.assertTrue(res["executed"], f"{seed}: {res.get('note')}")
            self.assertEqual(res["rows"], [], seed)
            self.assertEqual(res["scope"], "seed", seed)
            self.assertEqual(res["population"], 0, seed)
        store.close()

    def test_ledger_without_seed_keeps_scope_echo(self):
        """하위호환 — seed 없는 기존 호출은 롤업이 그대로이고 scope 는 "domain"/"all"
        을 정직하게 에코한다(새 키는 가산 — 구 소비처 무시, 새 소비처의 스큐 판별)."""
        from api import query as q

        store = self._fresh_store()
        with store.session() as s:
            for cid, dom in (("a", "math"), ("b", "art")):
                s.run("MERGE (n:Concept {concept_id:$id}) SET n.title=$id, n.owner_id='u', "
                      "n.domain=$d", id=cid, d=dom)

        res = q.ledger(owner="u", store=store)
        self.assertTrue(res["executed"], res.get("note"))
        self.assertEqual(res["scope"], "all")
        self.assertEqual(res["population"], 2)
        scoped = q.ledger(owner="u", domain="math", store=store)
        store.close()
        self.assertTrue(scoped["executed"], scoped.get("note"))
        self.assertEqual(scoped["scope"], "domain")
        self.assertEqual(scoped["population"], 1)
        self.assertEqual([r["domain"] for r in scoped["rows"]], ["math"])

    def test_ledger_route_forwards_seed_and_depth(self):
        """라우트 계약 — `/ledger` 가 seed·depth 를 전달한다(depth 는 문자열 파싱,
        `read_graph` 관례). seed 부재 시 기존 경로 그대로(scope="all")."""
        from api.server import VincApp

        store = self._fresh_store()
        with store.session() as s:
            s.run("MERGE (n:Concept {concept_id:'rs'}) SET n.title='R', n.owner_id='u', "
                  "n.domain='math'")
        app = VincApp(store=store)
        code, out = app.read_ledger({"owner": "u", "seed": "rs", "depth": "1"})
        self.assertEqual(code, 200, out)
        self.assertEqual(out.get("scope"), "seed", out)
        self.assertEqual(out.get("population"), 1, out)
        code, out = app.read_ledger({"owner": "u"})
        self.assertEqual(code, 200, out)
        self.assertEqual(out.get("scope"), "all", out)
        store.close()


class _HalfBrokenStore:
    """origin 플랜은 답하고 **연결 축(link) 플랜만** 터뜨리는 최소 스텁 — 부분 실패
    강등 계약 전용이라 실엔진이 필요 없다(드라이버 무관 상시 실행)."""

    def __init__(self, rows):
        self._rows = rows

    def run(self, cypher, params=None):
        if "CONNECTS" in cypher:      # ledger_link_plan 만 실패시킨다
            raise RuntimeError("Binder exception: rel table not found")
        return self._rows

    def close(self):                  # 주입 스토어라 execute 는 닫지 않는다
        raise AssertionError("주입 스토어를 execute 가 닫았다")


class ViewViewpointsPureContracts(unittest.TestCase):
    """실엔진 없이 성립해야 하는 순수 계약(셰이핑·강등)."""

    def test_ledger_degrades_when_the_link_plan_fails(self):
        """두 플랜 중 하나만 죽어도 응답 전체가 강등된다 — 연결 축이 죽으면 차수가
        전부 0 이라 모든 개념이 고아로 보고되는데, ``executed: true`` 로 나가면
        소비처가 그 거짓을 그대로 그린다."""
        from api import query as q

        store = _HalfBrokenStore([{"id": "c1", "domain": "math", "ts": "", "mark": "all"}])
        res = q.ledger(owner="u", store=store)
        self.assertFalse(res["executed"], "연결 축이 죽었는데 executed=true 로 응답")
        self.assertEqual(res["rows"], [])
        self.assertIn("RuntimeError", res.get("note", ""))

    def test_shape_graph_upgrades_empty_ts_only(self):
        """dedup 예외 — 빈 ``ts`` 는 나중 행의 비지 않은 값으로 승격하고, 그 밖의
        필드는 선착 우선을 유지한다."""
        from api import query as q

        rows = [
            {"id": "a", "label": "A", "domain": "math", "ts": "2026-07-01",
             "target": "b", "target_label": "B", "target_domain": "math"},
            {"id": "b", "label": "B 나중", "domain": "art", "ts": "2026-07-02"},
        ]
        by_id = {n["id"]: n for n in q.shape_graph(rows)["nodes"]}
        self.assertEqual(by_id["b"]["ts"], "2026-07-02")     # 빈 값만 승격
        self.assertEqual(by_id["b"]["label"], "B")           # 나머지는 선착 우선
        self.assertEqual(by_id["b"]["domain"], "math")
        self.assertEqual(by_id["a"]["ts"], "2026-07-01")     # 채워진 값은 불변


if __name__ == "__main__":
    unittest.main()

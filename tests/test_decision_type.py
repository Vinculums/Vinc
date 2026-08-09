# -*- coding: utf-8 -*-
"""v12 — 결정(Decision) 타입이 **모든 경로에서** 살아 있는가.

이 파일이 존재하는 이유: 노드 타입을 하나 더하는 일은 스키마 한 줄이 아니라 열두
표면을 건드리는 일이고, 그 표면들은 빠져도 **오류를 내지 않는다**. 실제로 이 작업
중에 셋이 그렇게 조용히 죽었다(전부 실측):

  1. `project_graph` 가 rel 의 끝점 테이블을 전부 투영하라고 요구해 **진단 전체**가
     `executed=False` 로 강등됐다(결정과 무관한 고아 조회까지).
  2. 알고리즘 id 열이 `concept_id` 하나라 결정 행의 id 가 `None` 이 되고, 호출부의
     `if not cid: continue` 가 그것을 버렸다(투영은 성공하고 결과만 빈다).
  3. `_seed_table` 의 미지 접두사 폴백이 Entity 라, `decision:*` 시드가 빈 결과를 냈다.

그래서 여기서는 "표면마다 결정이 실제로 나오는가"를 하나씩 잰다.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import fragment as frag        # noqa: E402
from api import query as q              # noqa: E402
from api import sync                    # noqa: E402

OWNER = "user:dec"


def _fresh_store():
    """임시 스토어 + env 복원 책임을 호출부에 넘긴다(전역 오염 방지)."""
    keys = ("MEMORY_STORE_BACKEND", "MEMORY_DATA_DIR", "MEMORY_EMBEDDING_MODEL")
    keep = {k: os.environ.get(k) for k in keys}
    os.environ["MEMORY_STORE_BACKEND"] = "embedded"
    os.environ["MEMORY_DATA_DIR"] = os.path.join(tempfile.mkdtemp(), "store")
    os.environ["MEMORY_EMBEDDING_MODEL"] = "local-en"
    from store.backend import open_store
    from store.config import load_config
    cfg = load_config()
    store = open_store(cfg)
    store.provision(dim=cfg.embedding_dim)
    return store, keep


def _restore(keep):
    for k, v in keep.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


class DecisionEndToEnd(unittest.TestCase):
    def setUp(self):
        self.store, self._keep = _fresh_store()
        res = frag.merge_fragment({
            "owner": OWNER,
            "nodes": [
                {"id": "concept:chunk-ssot", "type": "concept",
                 "title": "Chunk as the single source of truth", "domain": "vinc/engineering"},
                {"id": "concept:offline", "type": "concept",
                 "title": "Offline first", "domain": "vinc/engineering"},
                {"id": "decision:d45", "type": "decision",
                 "title": "Store the document canon in chunks",
                 "domain": "vinc/engineering", "tags": ["dec"],
                 "props": {"note": "supersedes the file mirror"}},
                {"id": "decision:d19", "type": "decision",
                 "title": "Ship share packs", "domain": "vinc/product"},
                {"id": "record:memo", "type": "record", "title": "Memo",
                 "domain": "vinc/engineering"},
                {"id": "episode:s1", "type": "episode", "title": "Session 1",
                 "domain": "vinc/engineering"},
            ],
            "edges": [
                # 결정이 **무엇을 두고** 내려졌는가
                {"from": "decision:d45", "to": "concept:chunk-ssot", "type": "about",
                 "evidence": "why the value is what it is", "confidence": 0.9},
                # 결정 계보
                {"from": "decision:d45", "to": "decision:d19", "type": "derives_from"},
                # 개념이 결정에서 파생
                {"from": "concept:offline", "to": "decision:d45", "type": "derives_from"},
                # 기록·사건이 결정에 붙는다
                {"from": "record:memo", "to": "decision:d45", "type": "relates_to"},
                {"from": "episode:s1", "to": "decision:d45", "type": "about"},
            ],
        }, store=self.store)
        self.assertFalse(res.get("warnings"), res.get("warnings"))
        self.assertEqual(res["merged"]["edges"]["created"], 5, res)

    def tearDown(self):
        self.store.close()
        _restore(self._keep)

    def test_노드가_전용_테이블에_값과_함께_앉는다(self):
        """개방층(Entity)으로 새면 domain·props 가 조용히 사라진다 — 그 형태를 잰다."""
        rows = self.store.run(
            "MATCH (d:Decision) RETURN d.decision_id AS id, d.title AS t, "
            "d.domain AS dom, d.props AS p, d.owner_id AS o ORDER BY d.decision_id")
        self.assertEqual([r["id"] for r in rows], ["decision:d19", "decision:d45"])
        d45 = [r for r in rows if r["id"] == "decision:d45"][0]
        self.assertEqual(d45["dom"], "vinc/engineering")
        self.assertIn("supersedes", d45["p"] or "")
        self.assertEqual(d45["o"], OWNER)
        self.assertEqual(self.store.run("MATCH (e:Entity) RETURN count(e) AS c")[0]["c"], 0,
                         "결정이 개방층 캐리어로 샜다 — domain·props 가 유실된다")

    def test_다섯_엣지가_제_라벨로_저장된다(self):
        got = {}
        for rel, a, b in (("ABOUT", "Decision", "Concept"),
                          ("DERIVES_FROM", "Decision", "Decision"),
                          ("DERIVES_FROM", "Concept", "Decision"),
                          ("ATTACHES_TO", "Record", "Decision"),
                          ("ABOUT", "Episode", "Decision")):
            n = self.store.run(f"MATCH (:{a})-[r:{rel}]->(:{b}) RETURN count(r) AS c")
            got[(rel, a, b)] = int(n[0]["c"]) if n else 0
        self.assertEqual(set(got.values()), {1}, got)
        ev = self.store.run("MATCH (:Decision)-[r:ABOUT]->(:Concept) "
                            "RETURN r.evidence AS e, r.confidence AS c")
        self.assertEqual(ev[0]["e"], "why the value is what it is")
        self.assertAlmostEqual(float(ev[0]["c"]), 0.9)

    def test_relates_to_는_결정에_거부되고_이유를_말한다(self):
        """조용한 재해석도, 엔진 예외도 아닌 **경고**여야 한다(사용자가 고칠 수 있게)."""
        res = frag.merge_fragment({
            "owner": OWNER,
            "nodes": [{"id": "decision:d45", "type": "decision", "title": "D45"},
                      {"id": "concept:offline", "type": "concept", "title": "Offline"}],
            "edges": [{"from": "decision:d45", "to": "concept:offline",
                       "type": "relates_to"}],
        }, store=self.store)
        self.assertTrue(res.get("partial"))
        joined = " ".join(res["warnings"])
        self.assertIn("relates_to is concept", joined)
        self.assertIn("about", joined)          # 대안을 알려 준다
        self.assertEqual(
            int(self.store.run("MATCH (:Decision)-[r:RELATED_TO]->() "
                               "RETURN count(r) AS c")[0]["c"]), 0)

    def test_진단이_결정을_보고_죽지_않는다(self):
        """회귀 ①② — 투영 누락으로 진단이 통째로 강등되던 자리."""
        diag = q.component_diagnosis(owner=OWNER, store=self.store)
        if not diag["executed"]:
            # ⚠ 여기서 무조건 skip 하면 이 테스트가 **회귀를 못 잡는다**. 실제로
            # 그랬다: 투영을 깨뜨려 놓고 되돌리기 검증을 돌렸더니 실패가 아니라
            # skip 이 났다. 확장 부재(배포 환경)와 투영 결함(우리 잘못)을 가른다.
            note = str(diag.get("note") or "")
            self.assertNotIn("Binder", note,
                             f"투영이 깨졌다(확장 부재가 아니다): {note}")
            self.assertNotIn("not projected", note, f"투영 누락: {note}")
            self.skipTest(f"algo 확장 미가용: {note}")
        members = {m for c in diag["components"] for m in c["members"]}
        self.assertIn("decision:d45", members | set(diag["orphans"]),
                      "결정이 컴포넌트에도 고아에도 없다 — id 열이 None 으로 떨어졌다")
        com = q.communities(owner=OWNER, store=self.store)
        self.assertTrue(com["executed"], com.get("note"))

    def test_시드_접두사가_Entity_로_새지_않는다(self):
        """회귀 ③ — 미지 접두사 폴백이 Entity 라 `decision:*` 조회가 조용히 비었다."""
        self.assertEqual(q._seed_table("decision:d45"), ("Decision", "decision_id"))

    def test_축_플랜이_결정을_kind_로_싣는다(self):
        plan = q.decision_rows_plan(owner=OWNER)
        rows = q.execute(plan, None, store=self.store).get("rows", [])
        self.assertEqual([r["id"] for r in rows], ["decision:d45"])
        self.assertEqual(rows[0]["kind"], "decision")
        self.assertEqual(rows[0]["target"], "concept:chunk-ssot")
        self.assertIn(q.decision_rows_plan, q._GRAPH_AXIS_PLANS)

    def test_동기화가_결정과_그_엣지를_나른다(self):
        """결정은 여태 Concept 이라 동기화되고 있었다. 라벨만 옮기고 여기 빠지면
        다른 기기에서 결정이 통째로 사라진 것처럼 보인다 — 오류 하나 없이."""
        out = sync.export_delta(owner=OWNER, since=0,
                                domains=["vinc/engineering", "vinc/product"],
                                store=self.store)
        self.assertTrue(out["executed"], out)
        ids = {n["id"] for n in out["nodes"]}
        self.assertIn("decision:d45", ids)
        self.assertIn("decision:d19", ids)
        self.assertEqual({n["type"] for n in out["nodes"] if n["id"].startswith("decision:")},
                         {"decision"})
        d45 = [n for n in out["nodes"] if n["id"] == "decision:d45"][0]
        self.assertEqual(d45["props"].get("note"), "supersedes the file mirror")
        edges = {(e["from"], e["type"], e["to"]) for e in out["edges"]}
        self.assertIn(("decision:d45", "about", "concept:chunk-ssot"), edges)
        self.assertIn(("decision:d45", "derives_from", "decision:d19"), edges)
        self.assertIn(("concept:offline", "derives_from", "decision:d45"), edges)

        # **받는 쪽까지** 잰다. export 만 재면 `_SYNC_TYPES` 가 비어도 통과한다
        # (되돌리기 검증에서 실제로 그랬다) — 그 표는 apply 경로가 쓰는 것이다.
        other, keep2 = _fresh_store()
        try:
            got = sync.apply_delta(owner=OWNER, bundle=out, store=other)
            self.assertTrue(got.get("executed"), got)
            self.assertEqual(
                [r["id"] for r in other.run(
                    "MATCH (d:Decision) RETURN d.decision_id AS id ORDER BY d.decision_id")],
                ["decision:d19", "decision:d45"],
                f"받는 기기에 결정이 안 앉았다 — apply 어휘 누락: {got.get('warnings')}")
            self.assertEqual(
                int(other.run("MATCH (:Decision)-[r:ABOUT]->(:Concept) "
                              "RETURN count(r) AS c")[0]["c"]), 1)
        finally:
            other.close()
            _restore(keep2)
            _restore(self._keep)   # 이 테스트의 스토어 env 를 되돌린다

    def test_결정_엣지_삭제가_실제로_지운다(self):
        """SYNC_EDGE_RELS 가 rel 하나에 끝점 쌍 하나를 가정하면 여기가 0건 매치로
        끝난다(툼스톤만 남고 엣지는 그대로 — 기기마다 다른 그래프)."""
        res = q.delete_edge("decision:d45", "about", "concept:chunk-ssot",
                            owner=OWNER, store=self.store)
        self.assertTrue(res["executed"] and res["found"], res)
        self.assertEqual(res["deleted"]["edges"], 1)
        left = self.store.run("MATCH (:Decision)-[r:ABOUT]->(:Concept) RETURN count(r) AS c")
        self.assertEqual(int(left[0]["c"]), 0)

    def test_노드_삭제가_거부되지_않는다(self):
        """_OWNER_SCOPED_LABELS 에 빠지면 owner 강제 경로가 fail-closed 로 **거부**한다
        — 앱에서 결정을 영영 못 지우는 형태(브릿지가 실제로 그랬다)."""
        res = q.delete_node("decision:d19", owner=OWNER, store=self.store)
        self.assertTrue(res["executed"] and res["found"], res)
        self.assertEqual(res["deleted"]["node"], 1)

    def test_사용자_도구_전부가_결정을_본다(self):
        """읽기 경로 전수 — 이 목록이 **이관 직전에 라이브에서** 만들어졌다.

        스키마·쓰기를 다 고치고 배포까지 한 뒤에 탐침을 하나 심어 보니
        `vinc_neighbors` 가 실재하는 결정을 "unknown node" 라고 답했다. 세어 보니
        조회 경로 다섯이 같은 이유로 죽어 있었다 — 결정이 Concept 이던 동안에는
        전부 동작하던 것들이라, 라벨을 옮기는 순간 **한꺼번에** 멈춘다.

        그래서 도구 단위로 잰다. 함수 하나를 고쳤는지가 아니라, 사용자가 쓰는
        표면에서 결정이 실제로 나오는지가 계약이다."""
        D = "decision:d45"
        checks = {
            "search": lambda: q.search("document canon chunks", owner=OWNER,
                                       store=self.store),
            "neighbors(결정에서)": lambda: q.neighbors(D, owner=OWNER, store=self.store),
            "neighbors(개념에서)": lambda: q.neighbors("concept:chunk-ssot", owner=OWNER,
                                                  store=self.store),
            "graph(결정 시드)": lambda: q.graph(seed=D, owner=OWNER, depth=2,
                                            store=self.store),
            "shortest_path": lambda: q.shortest_path("concept:offline", D, owner=OWNER,
                                                     store=self.store),
            "node_context": lambda: q.node_context(owner=OWNER, node=D, store=self.store),
            "suggest_ties": lambda: q.suggest_ties(owner=OWNER, node=D, store=self.store),
        }
        missing = []
        for name, fn in checks.items():
            try:
                out = str(fn())
            except Exception as exc:                      # noqa: BLE001
                missing.append(f"{name}: {type(exc).__name__}: {exc}")
                continue
            # 결정 id 나 그 제목 중 하나는 나와야 한다(도구마다 투영이 다르다).
            if D not in out and "Store the document canon" not in out:
                missing.append(f"{name}: 결정이 응답에 없다 — {out[:120]}")
        self.assertEqual(missing, [], "결정이 안 보이는 도구:\n  " + "\n  ".join(missing))

        # 주제 집계 — 결정을 안 세면 이관 직후 `concepts` 만 68 줄어든 화면이 되고,
        # 그건 데이터 손실과 구분되지 않는다.
        doms = q.domains(owner=OWNER, store=self.store)
        rows = {r["domain"]: r for r in doms["rows"]}
        self.assertEqual(rows["vinc/engineering"]["decisions"], 1, doms)
        self.assertEqual(rows["vinc/product"]["decisions"], 1, doms)
        self.assertIn("decisions", rows["vinc/engineering"])
        # 주제 상한(AP-1)이 세는 집합도 같아야 한다 — 다르면 사용자가 `vinc_domains`
        # 로 N 개를 보면서 "N 개 상한에 걸렸다"는 말을 듣는다.
        self.assertIn("vinc/product", q.domain_names(owner=OWNER, store=self.store))

    def test_문서가_결정을_언급하면_링크가_생긴다(self):
        """MENTIONS 는 링커가 유일한 생성 경로다. 결정이 Concept 을 떠나면
        "값 → 문서 → 결정" 의 마지막 한 칸이 조용히 끊긴다."""
        from engines.mentions_link import link_mentions
        self.store.run(
            "MERGE (c:Chunk {chunk_id:'ck1'}) SET c.owner_id=$o, c.doc_id='doc1', "
            "c.text='We decided to store the document canon in chunks.', "
            "c.source_file='doc1.md'", {"o": OWNER})
        stats = link_mentions(self.store, owner=OWNER)
        self.assertGreater(stats["links"], 0, stats)
        n = self.store.run("MATCH (:Chunk)-[m:MENTIONS]->(:Decision) RETURN count(m) AS c")
        self.assertGreaterEqual(int(n[0]["c"]), 1,
                                "문서가 결정을 언급했는데 링크가 안 생겼다")


if __name__ == "__main__":
    unittest.main()

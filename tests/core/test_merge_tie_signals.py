# -*- coding: utf-8 -*-
"""F10/F11 — 병합 후보의 구조적 오탐과 잇기 제안의 신호 부재.

두 도구가 **읽을 수 있는 답을 못 내던** 두 가지 이유를 못박는다. 둘 다 계획서 서술이
아니라 실측에서 나왔다(22개념 표본 + 라이브 계정 그래프 185개념):

F10  merge_candidates 후보 20쌍 중 19쌍이 **같은 점수 0.8** 이었고, 그중 절반(10쌍)이
     제목 토큰이 `{vinc}` 하나뿐인 노드 **한 개**가 만든 것이었다. 나머지도 대부분
     문서 개념끼리(`vinc.d.graph` vs `vinc.d.graph-views`)이거나 결정↔주제
     (`dec-d45` vs `chunk-ssot`)처럼 GRAPH.md 가 **일부러 갈라 놓은** 쌍이다.
     그리고 한 번 기각해도 다음 호출에 같은 쌍이 또 1등으로 올라왔다.

F11  suggest_ties 는 태그를 희소성과 무관하게 같은 값으로 셌다. `core` 처럼 개념
     절반에 붙은 태그 하나가 점수를 포화시켜 후보 14개 중 13개가 동률이 됐고, 그때
     limit 은 사실상 **id 사전순 앞 N개**를 잘랐다.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import query as q  # noqa: E402
from store.embedded_backend import import_driver  # noqa: E402

_DRIVER, _NAME = import_driver()

# 라이브 계정 그래프의 명명 관례를 그대로 흉내낸 표본.
_ROWS = [
    {"id": "concept:web-vinc", "title": "웹 Vinc", "domain": "vinc/architecture"},
    {"id": "concept:doc.vinc.a.terms", "title": "vinc.a.terms", "domain": "vinc/governance"},
    {"id": "concept:doc.vinc.arch", "title": "vinc.arch", "domain": "vinc/architecture"},
    {"id": "concept:doc.vinc.d.graph", "title": "vinc.d.graph", "domain": "vinc/architecture"},
]


class MergeCandidateStructureTests(unittest.TestCase):
    """순수 층 — DB 불요."""

    def test_한_토큰_제목이_모든_제목의_부분집합이_되지_않는다(self):
        """`웹 Vinc` 의 `웹` 이 버려지면 토큰셋이 `{vinc}` 가 되고, 그러면 vinc 를 담은
        **모든** 제목의 부분집합이라 subset 규칙이 전부 후보로 올린다. 실측에서 이
        노드 하나가 전체 후보쌍의 절반을 만들었다."""
        cands = q.merge_candidates(_ROWS, include_structural=True, limit=100)
        involved = [c for c in cands
                    if "concept:web-vinc" in (c["a_id"], c["b_id"])]
        self.assertEqual(involved, [], f"일반어 노드가 후보를 만들었다: {involved}")

    def test_문서_개념끼리는_기본으로_병합_후보가_아니다(self):
        """문서 미러 개념은 원문 문서 1:1 이다. 제목이 접두 관계인 것은 명명 규칙의
        결과이지 중복의 증거가 아니다. 다만 **조용히** 빼지는 않는다."""
        rows = [{"id": "concept:doc.vinc.d.graph", "title": "vinc.d.graph", "domain": "d"},
                {"id": "concept:doc.vinc.d.graph-views", "title": "vinc.d.graph-views",
                 "domain": "d"}]
        det = q.merge_candidates_detail(rows)
        self.assertEqual(det["candidates"], [])
        self.assertEqual(det["suppressed"]["structural"], 1)
        # 되열 수 있어야 한다 — 필터가 진짜 중복을 먹었을 때 확인할 길이 필요하다.
        back = q.merge_candidates(rows, include_structural=True)
        self.assertEqual(len(back), 1)

    def test_문서_개념과_그것이_비추는_주제_개념은_병합_후보가_아니다(self):
        """v13 — 규칙이 doc↔doc 만 막고 **doc↔주제**를 놓치고 있었다.

        라이브 계정 그래프 실측(2026-08-10): 후보 10쌍 중 **8쌍이 이 모양**이었다.
        `concept:view` 와 `concept:doc.vinc.spec.surface-view` 는 제목이 글자까지
        같은데, 그건 doc 미러가 문서 제목을 그대로 물려받기 때문이지 같은 것을 두 번
        적었기 때문이 아니다. 1-hop 을 재면 어휘가 갈린다 — 주제 쪽은 ABOUT 13 ·
        ATTACHES_TO 5 · RELATED_TO 6 이고 INHERITS_FROM 0, 문서 쪽은 INHERITS_FROM 4
        뿐이고 나머지 0. 사본 둘이 아니라 **층 둘**이라 합치면 문서 상속 격자가 의미
        그래프 안으로 무너진다.

        신호 대 잡음이 2:8 이면 사람이 볼 수 있는 목록이 아니다 — 그래서 기본 제외다."""
        rows = [{"id": "concept:view", "title": "View, the map surface", "domain": "vinc/design"},
                {"id": "concept:doc.vinc.spec.surface-view",
                 "title": "View, the map surface", "domain": "vinc/design"}]
        det = q.merge_candidates_detail(rows)
        self.assertEqual(det["candidates"], [],
                         "문서 미러가 주제 개념의 병합 후보로 올라왔다")
        self.assertEqual(det["suppressed"]["structural"], 1)
        # 무음 억제 금지 — 되열면 그대로 보여야 한다(제목이 같으니 exact_title).
        back = q.merge_candidates(rows, include_structural=True)
        self.assertEqual(len(back), 1)
        self.assertEqual(back[0]["reason"], "exact_title")

    def test_주제_개념끼리는_계속_후보로_남는다(self):
        """확장이 **너무 넓지 않은지**를 반대편에서 못박는다. doc 이 한쪽도 없으면
        규칙은 무동작이어야 한다 — 안 그러면 이 도구가 잡을 것이 없어진다."""
        rows = [{"id": "concept:ipaas", "title": "iPaaS", "domain": "d"},
                {"id": "concept:ipaas-2", "title": "iPaaS", "domain": "d"}]
        det = q.merge_candidates_detail(rows)
        self.assertEqual(len(det["candidates"]), 1, det)
        self.assertEqual(det["suppressed"]["structural"], 0)

    def test_결정은_후보_소스에_아예_들어오지_않는다(self):
        """v12 — 결정이 병합 후보에서 빠지는 근거가 **타입**으로 바뀌었다.

        여태는 `concept:dec-*` 라는 id 접두 관례를 순수층이 알아보고 억제했다. 그건
        타입이 없어서 관례가 타입 노릇을 하던 대역이고, 그래서 `concept:dec-` 로
        시작하는 것만 걸리고 다른 이름을 쓰면 새는 규칙이었다.

        지금 결정은 `Decision` 라벨이고 후보 조회는 `MATCH (c:Concept)` 다 — 결정은
        **애초에 행으로 들어오지 않는다**. 억제 규칙이 아니라 조회 범위가 답이므로,
        이 테스트도 순수층이 아니라 그 조회를 잰다."""
        import os
        import tempfile
        from store.config import load_config
        from store.backend import open_store
        # env 를 **빌려 쓰고 되돌린다**. 그냥 덮으면 같은 프로세스의 다음 테스트가 이
        # 임시 스토어를 진짜 스토어로 본다(scripts/eval_tooluse._fixture_env 가 적어
        # 둔 함정 — 단독 실행에서는 멀쩡해 증상이 원인을 안 가리킨다).
        keys = ("MEMORY_STORE_BACKEND", "MEMORY_DATA_DIR", "MEMORY_EMBEDDING_MODEL")
        keep = {k: os.environ.get(k) for k in keys}
        os.environ["MEMORY_STORE_BACKEND"] = "embedded"
        os.environ["MEMORY_DATA_DIR"] = os.path.join(tempfile.mkdtemp(), "store")
        os.environ["MEMORY_EMBEDDING_MODEL"] = "local-en"
        cfg = load_config()
        store = open_store(cfg)
        store.provision(dim=cfg.embedding_dim)
        try:
            # 제목이 거의 같은 결정·개념 쌍 — 관례가 없어도 걸려선 안 된다.
            store.run("MERGE (d:Decision {decision_id:'decision:d45'}) SET "
                      "d.owner_id='u', d.title='Document canon (Chunk=SSoT)', d.domain='d'")
            store.run("MERGE (c:Concept {concept_id:'concept:chunk-ssot'}) SET "
                      "c.owner_id='u', c.title='Document canon (Chunk=SSoT)', c.domain='d'")
            out = q.suggest_merge_candidates(owner="u", store=store)
            self.assertTrue(out["executed"], out)
            self.assertEqual(out["candidates"], [],
                             "결정이 개념 병합 후보로 올라왔다 — 조회가 라벨을 안 가렸다")
            # 억제 카운터로 숨긴 것이 아니라 **애초에 안 들어온** 것이어야 한다.
            self.assertEqual(out["suppressed"]["structural"], 0)
        finally:
            store.close()
            for k, v in keep.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_부분집합_점수가_겹침에_따라_갈린다(self):
        """`max(0.8, jaccard)` 는 겹침과 무관하게 0.8 바닥을 깔아 실측 20쌍 중 19쌍을
        **같은 점수**로 만들었다. 점수가 한 값이면 정렬은 id 사전순과 다를 게 없다."""
        rows = [{"id": "a", "title": "alpha beta gamma delta", "domain": "d"},
                {"id": "b", "title": "alpha beta gamma delta epsilon", "domain": "d"},
                {"id": "c", "title": "zeta", "domain": "d"},
                {"id": "d", "title": "zeta eta theta iota kappa", "domain": "d"}]
        by_pair = {(x["a_id"], x["b_id"]): x["score"] for x in q.merge_candidates(rows)}
        high, low = by_pair[("a", "b")], by_pair[("c", "d")]   # 자카드 4/5 vs 1/5
        self.assertGreater(high, low, "겹침이 큰 쌍이 더 높아야 한다")
        self.assertGreaterEqual(low, 0.8)      # 부분집합은 여전히 강한 신호
        self.assertLessEqual(high, 1.0)

    def test_기각된_쌍은_다시_제안되지_않는다(self):
        rows = [{"id": "a", "title": "vector", "domain": "d"},
                {"id": "b", "title": "vector", "domain": "d"}]
        self.assertEqual(len(q.merge_candidates(rows)), 1)
        det = q.merge_candidates_detail(rows, rejected=[("b", "a")])   # 순서 무관
        self.assertEqual(det["candidates"], [])
        self.assertEqual(det["suppressed"]["rejected"], 1)

    def test_기각_키는_쌍의_순서를_타지_않는다(self):
        self.assertEqual(q.rejection_pair_key("u", "z", "a"),
                         q.rejection_pair_key("u", "a", "z"))
        self.assertNotEqual(q.rejection_pair_key("u1", "a", "z"),
                            q.rejection_pair_key("u2", "a", "z"))


class _EngineCase(unittest.TestCase):
    """실엔진 공용 — MEMORY_DATA_DIR 을 tempdir 로 격리(공용 스토어 오염 금지)."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._env = {}
        for k, v in {"MEMORY_STORE_BACKEND": "embedded",
                     "MEMORY_DATA_DIR": os.path.join(self._tmp, "store")}.items():
            self._env[k] = os.environ.get(k)
            os.environ[k] = v
        from store.config import load_config
        from store.backend import open_store
        self.store = open_store(load_config())
        self.store.provision(dim=4)

    def tearDown(self):
        self.store.close()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _ingest(self, owner, nodes, edges=()):
        from api import fragment as fr
        res = fr.merge_fragment(
            {"owner": owner,
             "nodes": [{"id": i, "type": "concept", "title": t, "domain": d, "tags": list(g)}
                       for i, t, d, g in nodes],
             "edges": [{"from": a, "type": "relates_to", "to": b} for a, b in edges]},
            store=self.store)
        self.assertTrue(res.get("executed", True), res)


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class MergeRejectionE2E(_EngineCase):
    """F10-b — 기각 기억이 실제로 살아남고 제안을 거른다."""

    OWNER = "user:a"

    def _pairs(self, owner=None, **kw):
        res = q.suggest_merge_candidates(owner=owner or self.OWNER,
                                         store=self.store, **kw)
        self.assertTrue(res["executed"], res)
        return res

    def test_기각하면_사라지고_되돌리면_돌아온다(self):
        self._ingest(self.OWNER, [
            ("concept:ipaas", "iPaaS", "tech", []),
            ("concept:ipaas-2", "ipaas", "tech", []),
        ])
        before = self._pairs()
        self.assertEqual(before["count"], 1, before)
        self.assertEqual(before["suppressed"]["rejected"], 0)

        rej = q.reject_merge_pair(owner=self.OWNER, a_id="concept:ipaas-2",
                                  b_id="concept:ipaas", note="철자만 다른 별개",
                                  store=self.store)
        self.assertTrue(rej["executed"], rej)

        after = self._pairs()
        self.assertEqual(after["candidates"], [])
        self.assertEqual(after["suppressed"]["rejected"], 1)   # 무음 억제 금지

        listed = q.list_merge_rejections(owner=self.OWNER, store=self.store)
        self.assertEqual(listed["count"], 1)
        self.assertEqual(listed["rejections"][0]["note"], "철자만 다른 별개")

        # 오기각이 영구가 되면 안 된다.
        undo = q.reject_merge_pair(owner=self.OWNER, a_id="concept:ipaas",
                                   b_id="concept:ipaas-2", undo=True, store=self.store)
        self.assertTrue(undo["executed"], undo)
        self.assertEqual(self._pairs()["count"], 1)

    def test_기각은_멱등이다(self):
        self._ingest(self.OWNER, [("concept:x", "vector", "d", []),
                                  ("concept:y", "vector", "d", [])])
        for _ in range(3):
            r = q.reject_merge_pair(owner=self.OWNER, a_id="concept:x", b_id="concept:y",
                                    store=self.store)
            self.assertTrue(r["executed"], r)
        self.assertEqual(q.list_merge_rejections(owner=self.OWNER,
                                                 store=self.store)["count"], 1)

    def test_남의_기각이_내_제안을_지우지_못한다(self):
        """SEC-1/SEC-3 규율 — 기각도 소유 스코프다. 아니면 남의 제안 목록을 조용히
        비우는 표면이 된다."""
        self._ingest(self.OWNER, [("concept:a1", "vector", "d", []),
                                  ("concept:a2", "vector", "d", [])])
        self._ingest("user:b", [("concept:b1", "vector", "d", []),
                                ("concept:b2", "vector", "d", [])])
        q.reject_merge_pair(owner="user:b", a_id="concept:a1", b_id="concept:a2",
                            store=self.store)
        mine = self._pairs()
        self.assertEqual(mine["count"], 1, "남의 기각이 내 후보를 먹었다")
        self.assertEqual(mine["suppressed"]["rejected"], 0)
        self.assertEqual(q.list_merge_rejections(owner=self.OWNER,
                                                 store=self.store)["count"], 0)

    def test_owner_없는_기각은_거부된다(self):
        r = q.reject_merge_pair(owner="", a_id="a", b_id="b", store=self.store)
        self.assertFalse(r["executed"])
        self.assertIn("owner", r["error"])
        # 같은 노드 두 번도 거부(무의미한 행).
        r2 = q.reject_merge_pair(owner="user:a", a_id="a", b_id="a", store=self.store)
        self.assertFalse(r2["executed"])

    def test_계정_삭제가_기각_기록도_지운다(self):
        """기각 행은 회원의 노드 id 쌍을 담는다 — purge 캐스케이드가 덮어야 한다."""
        self._ingest(self.OWNER, [("concept:p1", "vector", "d", []),
                                  ("concept:p2", "vector", "d", [])])
        q.reject_merge_pair(owner=self.OWNER, a_id="concept:p1", b_id="concept:p2",
                            store=self.store)
        self.assertEqual(q.list_merge_rejections(owner=self.OWNER,
                                                 store=self.store)["count"], 1)
        res = q.purge_owner(self.OWNER, store=self.store)
        self.assertTrue(res["executed"], res)
        self.assertIn("_MergeRejection", res["deleted"])
        self.assertEqual(res["deleted"]["_MergeRejection"], 1)
        self.assertEqual(q.list_merge_rejections(owner=self.OWNER,
                                                 store=self.store)["count"], 0)


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class TieSignalE2E(_EngineCase):
    """F11 — 잇기 제안 점수에 실제 신호가 실린다."""

    OWNER = "user:a"

    def _seed(self):
        # `core` 는 전 개념에 붙은 포화 태그, `rare` 는 둘만 가진 희소 태그.
        # seed 와 near 는 hub 를 공통 이웃으로 갖는다(2홉 근접).
        self._ingest(self.OWNER, [
            ("concept:seed", "Seed", "d", ["core", "rare"]),
            ("concept:hub", "Hub", "d", ["core"]),
            ("concept:near", "Near", "d", ["core"]),        # 2홉 — 공통 이웃 hub
            ("concept:rare-mate", "Rare mate", "d", ["core", "rare"]),
            ("concept:plain1", "Plain one", "d", ["core"]),
            ("concept:plain2", "Plain two", "d", ["core"]),
            ("concept:plain3", "Plain three", "d", ["core"]),
        ], edges=[("concept:seed", "concept:hub"), ("concept:hub", "concept:near")])

    def _ties(self, node="concept:seed", limit=50):
        res = q.suggest_ties(owner=self.OWNER, node=node, limit=limit, store=self.store)
        self.assertTrue(res["executed"], res)
        return res

    def test_2홉_근접이_포화_태그를_이긴다(self):
        """예전엔 `core` 하나로 전부 동률이라 limit 이 **id 사전순**을 잘랐다.
        공통 이웃이 있는 후보가 그냥 같은 태그를 단 후보보다 위에 와야 한다."""
        self._seed()
        res = self._ties()
        by_id = {s["id"]: s for s in res["suggestions"]}
        self.assertIn("concept:near", by_id)
        near, plain = by_id["concept:near"], by_id["concept:plain1"]
        self.assertGreater(near["score"], plain["score"])
        self.assertEqual(near["reason"], "shared_neighbors")
        self.assertEqual(near["signals"]["common_neighbors"], 1)
        self.assertEqual(near["shared_neighbors"], ["concept:hub"])
        # 사전순 앞자리(plain1)가 1등이던 옛 동작의 회귀 방지.
        self.assertEqual(res["suggestions"][0]["id"], "concept:near")

    def test_희소_태그가_포화_태그보다_무겁다(self):
        """`core`(전원 보유)와 `rare`(둘만 보유)가 같은 +2 였다. 전원이 가진 태그는
        아무것도 말해 주지 않는다."""
        self._seed()
        by_id = {s["id"]: s for s in self._ties()["suggestions"]}
        mate, plain = by_id["concept:rare-mate"], by_id["concept:plain1"]
        self.assertEqual(mate["shared_tags"], ["core", "rare"])
        self.assertEqual(plain["shared_tags"], ["core"])
        self.assertGreater(mate["signals"]["tags"], plain["signals"]["tags"])
        # 전원이 가진 태그의 기여는 0 이어야 한다(정보 없음).
        self.assertEqual(plain["signals"]["tags"], 0.0)

    def test_점수에_해상도가_있다(self):
        """옛 산식은 이 표본 5후보를 **두 값**으로만 갈랐다(태그 하나 = 무조건 +2 라
        `core` 만 공유하는 넷이 전부 같은 점수). 값이 두 개뿐이면 정렬은 사실상 id
        사전순이고 호스트는 무엇을 먼저 볼지 고를 수 없다. 신호가 셋(태그 희소성 ·
        도메인 · 2홉)이면 최소한 그만큼은 갈려야 한다.

        완전히 같은 근거를 가진 후보(plain1~3)가 동률인 것은 **옳다** — 그건 실제로
        구별할 정보가 없다는 정직한 보고다. 여기서 재는 것은 구별 가능한 것이 실제로
        구별되는가다."""
        self._seed()
        sug = self._ties()["suggestions"]
        self.assertGreaterEqual(len(sug), 5)
        self.assertGreaterEqual(len(set(s["score"] for s in sug)), 3,
                                f"점수가 뭉쳤다: {[(s['id'], s['score']) for s in sug]}")

    def test_산식과_절단을_응답이_말한다(self):
        self._seed()
        res = self._ties(limit=2)
        self.assertIn("common neighbors", res["formula"])
        self.assertEqual(len(res["suggestions"]), 2)
        self.assertTrue(res["truncated"])
        self.assertGreater(res["total"], 2)
        self.assertFalse(self._ties(limit=50)["truncated"])

    def test_기존_필드는_그대로다(self):
        """앱(view-reader.ts 의 잇기 제안)이 읽는 네 필드 — 지우거나 이름을 바꾸면 화면이 죽는다.

        (이 검사만은 수정 전 코드에서도 통과한다. 회귀를 잡는 것이 아니라 **앞으로
        지우지 못하게** 못박는 하위호환 가드다.)"""
        self._seed()
        s = self._ties()["suggestions"][0]
        for k in ("id", "title", "domain", "shared_tags"):
            self.assertIn(k, s)
        self.assertIsInstance(s["shared_tags"], list)


if __name__ == "__main__":
    unittest.main()

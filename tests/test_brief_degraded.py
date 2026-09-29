# -*- coding: utf-8 -*-
"""`vinc_brief` 가 반쯤 고장 난 스토어에서도 **답할 수 있는 만큼 답하고, 반쪽임을 말하는가**.

RAG 코드 리뷰 패키지 F(VIN-204 · 205 · 220 · 221)의 회귀 검사다. 전부 실엔진(ladybug)
위에서 돈다 — 고장은 엔진 상태(FTS 잔해)나 특정 쿼리 하나의 실패로 만든다.

  1. 청크 팔만 죽으면(FTS 잔해 등) 노드 팔 결과로 해소를 계속한다. 사유는 `search_error`
     로 싣고 최상위 `error` 는 쓰지 않는다 — REST v1 이 그 키를 보면 HTTP 500 으로 바꾼다.
  2. node_context 가 메타 뒤에서 실패하면(발췌·관계) 답에 `partial` 과 `partial_note` 를
     단다. 빈 관계를 "연결 없는 노드"로 읽히게 두지 않는다.
  3. 질문 속 id 는 **언급 단위로** 자른다. 변형(조사 붙은 꼴·ASCII 접두)으로 자르면 둘째
     언급이 시도조차 안 된다.
  4. 질문이 id 를 담으면 `/brief` 는 질의 임베딩을 구하지 않는다 — brief 가 검색을 안 돈다.

근거: [[vinc.plan.rag-code-review-fixes-2026-09-29]]
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import docstore  # noqa: E402
from api import fragment as frag  # noqa: E402
from api import query as q  # noqa: E402
from store.embedded_backend import _import_driver  # noqa: E402

_DRIVER, _NAME = _import_driver()


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class _StoreCase(unittest.TestCase):
    OWNER = "user:a"

    def setUp(self):
        self._env = {}
        for k, v in {"MEMORY_STORE_BACKEND": "embedded",
                     "MEMORY_DATA_DIR": os.path.join(tempfile.mkdtemp(), "store")}.items():
            self._env[k] = os.environ.get(k)
            os.environ[k] = v
        from store.backend import open_store
        from store.config import load_config
        self.store = open_store(load_config())
        self.store.provision(dim=4)
        frag.merge_fragment({"owner": self.OWNER, "nodes": [
            {"id": "concept:alpha-core", "type": "concept", "title": "alpha core",
             "domain": "eng", "one_liner": "The core of alpha."},
            {"id": "concept:alpha-edge", "type": "concept", "title": "alpha edge",
             "domain": "eng"},
        ], "edges": [
            {"from": "concept:alpha-edge", "to": "concept:alpha-core",
             "type": "relates_to", "evidence": "둘은 같은 계약을 공유한다."},
        ]}, store=self.store)

    def tearDown(self):
        self.store.close()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _brief(self, query, **kw):
        return q.brief(query, owner=self.OWNER, store=self.store, **kw)

    def _failing(self, marker: str):
        """`marker` 를 담은 쿼리만 실패하는 run — 나머지는 엔진이 그대로 답한다."""
        real = self.store.run

        def run(cypher, params=None, **kw):
            if marker in cypher:
                raise RuntimeError("Buffer manager exception: injected for " + marker)
            return real(cypher, params, **kw)
        return mock.patch.object(self.store, "run", side_effect=run)


class ChunkArmDownTests(_StoreCase):
    """VIN-204(RV-08). FTS 잔해면 청크 팔이 통째로 서는데, 같은 검색의 노드 팔은 멀쩡히
    owner 스코프 결과를 낸다. 전에는 `error` 하나에 그것까지 버리고 `none` 을 답했다 —
    잔해 상태에서 id 없는 질문은 전부 none 이었다."""

    def setUp(self):
        super().setUp()
        docstore.put_doc(self.store, owner=self.OWNER, doc_id="notes",
                         text="alpha core notes. the core of alpha is small.")
        # 허브에서 난 그 상태: 청크는 있는데 FTS 색인이 잔해라 다시 서지 않는다.
        self.store.drop_search_indexes()
        self.store._remnants.add("Chunk")

    def test_청크_팔이_죽어도_제목이_맞는_노드를_해소한다(self):
        out = self._brief("alpha core")
        self.assertEqual(out["status"], "resolved", out)
        self.assertEqual(out["node"]["id"], "concept:alpha-core")
        self.assertIs(out["executed"], True)
        self.assertTrue(out.get("search_error", "").startswith("fts_index_remnant"), out)
        # 최상위 error 가 있으면 REST v1 이 해소된 답을 HTTP 500 으로 바꾼다.
        self.assertNotIn("error", out)
        from vinc_mcp import rest_api
        self.assertIsNone(rest_api._failure("vinc_brief", out), "해소된 답이 v1 에서 실패가 된다")

    def test_노드_팔도_빈손이면_그때만_일찍_돌아온다(self):
        out = self._brief("존재하지않는질문어휘")
        self.assertEqual(out["status"], "none")
        self.assertIs(out["executed"], False)
        self.assertTrue(out.get("search_error", "").startswith("fts_index_remnant"), out)
        self.assertNotIn("error", out)


class PartialContextTests(_StoreCase):
    """VIN-205(RV-09). node_context 는 메타를 읽은 직후 executed 를 세우고, 뒤의 발췌·관계
    조회가 실패해도 그것을 되돌리지 않는다. brief 는 executed 만 봤으므로 관계 0 · 발췌 0 의
    resolved 가 나갔고, 에이전트는 그것을 연결 없는 노드로 읽었다."""

    def test_건강한_노드에는_partial_이_없다(self):
        for query in ("alpha core", "concept:alpha-core"):
            out = self._brief(query)
            self.assertEqual(out["status"], "resolved", query)
            self.assertNotIn("partial", out, query)
            self.assertNotIn("partial_note", out, query)

    def test_발췌_조회가_실패하면_partial_로_말한다(self):
        with self._failing("MENTIONS"):
            out = self._brief("concept:alpha-core")
        self.assertEqual((out["status"], out.get("resolved_via")), ("resolved", "id"), out)
        self.assertIs(out.get("partial"), True, out)
        self.assertIn("Buffer manager", out.get("partial_note", ""))
        self.assertNotIn("error", out)
        from vinc_mcp import rest_api
        self.assertIsNone(rest_api._failure("vinc_brief", out), "반쪽 답이 v1 에서 실패가 된다")

    def test_관계_조회만_실패해도_partial_로_말한다(self):
        """이 갈래는 node_context 의 error 에도 안 잡힌다 — `_node_links` 가 빈 요약으로
        강등하고 `executed: False` 만 남긴다. 랭킹 경로로 연다(같은 꼬리를 쓴다)."""
        with self._failing("'out' AS dir"):
            out = self._brief("alpha core")
        self.assertEqual(out["status"], "resolved", out)
        self.assertEqual(out["node"]["id"], "concept:alpha-core")
        self.assertIs(out.get("partial"), True, out)
        self.assertIn("links", out.get("partial_note", ""))


class MentionCapTests(_StoreCase):
    """VIN-220(RV-24). 상한 셋이 변형 목록을 잘라 `[A는, A, B는, B]` 에서 B 가 빠졌다."""

    def test_둘째_언급도_시도한다(self):
        frag.merge_fragment({"owner": self.OWNER, "nodes": [
            {"id": "concept:beta", "type": "concept", "title": "beta", "domain": "eng"},
        ]}, store=self.store)
        out = self._brief("concept:gamma는 무엇이고 concept:beta는 무엇인가")
        self.assertEqual(out["status"], "resolved", out)
        self.assertEqual((out["node"]["id"], out.get("resolved_via")), ("concept:beta", "id"))
        self.assertEqual([f["id"] for f in out.get("fell_through", [])],
                         ["concept:gamma는", "concept:gamma", "concept:beta는"])

    def test_언급_수로_자르고_겹친_언급은_세지_않는다(self):
        ids = q._brief_query_ids(
            "concept:a는 concept:a concept:b는 concept:c concept:d")
        # concept:a 는 앞 언급의 접두와 같아 새 후보가 없다 — 상한을 쓰지 않는다.
        self.assertEqual(ids, ["concept:a는", "concept:a", "concept:b는", "concept:b",
                               "concept:c"])


class BriefSkipsQueryEmbeddingTests(_StoreCase):
    """VIN-221(RV-25). id 직참조는 검색을 안 도는데 `/brief` 는 질의 임베딩부터 구했다 —
    콜드 인코더면 쓰지도 않을 벡터를 오래 기다리고, 실패하면 안 돈 팔에 vector_note 가
    붙었다."""

    def _read_brief(self, query):
        from test_server import _app
        app = _app(store=self.store)
        try:
            with mock.patch.object(app, "_query_embedding",
                                   return_value=(None, "encoder unreachable")) as qe:
                code, res = app.read_brief({"query": query, "owner": self.OWNER})
            return code, res, qe.call_count
        finally:
            app.jobs.shutdown(drain=True)

    def test_id_를_담은_질문은_인코더를_부르지_않는다(self):
        code, res, calls = self._read_brief("concept:alpha-core 는 무엇인가")
        self.assertEqual((code, res["status"], res.get("resolved_via")), (200, "resolved", "id"))
        self.assertEqual(calls, 0)
        self.assertNotIn("vector_note", res)

    def test_id_없는_질문은_그대로_인코더를_부른다(self):
        code, res, calls = self._read_brief("alpha core")
        self.assertEqual(calls, 1, "대조군: 창구를 갈아 끼운 자리가 안 불렸다면 위 검사는 공허하다")
        self.assertEqual(res.get("vector_note"), "encoder unreachable")


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""node_context·brief 의 발췌 플래그(`VINC_EXCERPT_ORDER` · `VINC_EXCERPT_WINDOW`).

꺼져 있으면 발췌는 오늘과 같아야 하고(응답 키도 늘지 않는다), 켜면 순서와 창만 바뀐다.
근거: [[vinc.research.rag-anything-lightrag-read-against-vinc-2026-10-17]]"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import query as q  # noqa: E402
from store import pkns  # noqa: E402

_DRIVER = None
try:
    from store.embedded_backend import _import_driver
    _DRIVER, _ = _import_driver()
except Exception:
    pass

LEAD = "어제 시장은 크게 흔들렸고 외국인은 팔았다. " * 20      # 노드와 무관한 앞부분(> 240자)
SENTENCE = "현금 비중은 평소 30% 정도 들고 있는 게 맞다."
FLAGS = ("VINC_EXCERPT_ORDER", "VINC_EXCERPT_WINDOW")
#: 같은 문서의 청크 셋. 문서 순서로는 c0 → c1 → c2 이고, 노드 문장은 c1 의 240자 뒤에만 있다.
CHUNKS = (("c0", 0, "[c0] " + LEAD + "다른 이야기다."),
          ("c1", 1, "[c1] " + LEAD + SENTENCE),
          ("c2", 2, "[c2] " + LEAD + "또 다른 이야기다."))


def head(cid: str) -> str:
    text = dict((c, t) for c, _, t in CHUNKS)[cid]
    return " ".join(text.split())[:240]


class _Store:
    OWNER = "user:xflags"
    NODE = "concept:keep-cash"
    LINKED = True

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._env = {k: os.environ.get(k) for k in ("MEMORY_STORE_BACKEND", "MEMORY_DATA_DIR",
                                                    *FLAGS)}
        os.environ.update({"MEMORY_STORE_BACKEND": "embedded",
                           "MEMORY_DATA_DIR": os.path.join(self._tmp, "store")})
        for k in FLAGS:
            os.environ.pop(k, None)
        from store.backend import open_store
        from store.config import load_config
        self.store = open_store(load_config())
        self.store.provision(dim=4)
        self.pk = pkns.key(self.OWNER, self.NODE)
        self.store.run(
            "MERGE (c:Concept {concept_id: $pk}) SET c.owner_id = $o, "
            "c.title = '현금 비중은 평소 30% 정도', c.one_liner = $ol",
            {"pk": self.pk, "o": self.OWNER, "ol": SENTENCE})
        for cid, idx, text in CHUNKS:
            self.store.run(
                "MERGE (ch:Chunk {chunk_id: $cid}) SET ch.owner_id = $o, "
                "ch.doc_id = 'doc1', ch.chunk_index = $idx, ch.section_title = '', "
                "ch.text = $t", {"cid": cid, "o": self.OWNER, "idx": idx, "t": text})
            if self.LINKED:
                self.store.run(
                    "MATCH (ch:Chunk {chunk_id: $cid}), (c:Concept {concept_id: $pk}) "
                    "CREATE (ch)-[:MENTIONS {method: 'title'}]->(c)",
                    {"cid": cid, "pk": self.pk})

    def tearDown(self):
        self.store.close()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _vec(self, cid: str, emb: list[float], seq: int = 0):
        self.store.run(
            "MERGE (v:ChunkVec {vec_id: $vid}) SET v.chunk_id = $cid, v.doc_id = 'doc1', "
            "v.owner_id = $o, v.seq = $s, v.embedding = $e",
            {"vid": f"{cid}#{seq:03d}", "cid": cid, "o": self.OWNER, "s": seq, "e": emb})

    def _ctx(self, **kw):
        return q.node_context(owner=self.OWNER, node=self.NODE, store=self.store, **kw)


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class MentionsExcerpts(_Store, unittest.TestCase):

    def test_꺼져_있으면_맨_앞_240자이고_키가_늘지_않는다(self):
        out = self._ctx(limit=2, query_embedding=[1.0, 0.0, 0.0, 0.0])
        self.assertEqual([e["excerpt"] for e in out["excerpts"]], [head("c0"), head("c1")])
        self.assertNotIn("excerpt_order", out)
        self.assertNotIn("excerpt_order_note", out)

    def test_창_플래그는_노드_문장이_있는_자리를_보여_준다(self):
        with mock.patch.dict(os.environ, {"VINC_EXCERPT_WINDOW": "node"}):
            out = self._ctx(limit=3)
        hit = [e["excerpt"] for e in out["excerpts"] if "30% 정도 들고" in e["excerpt"]]
        self.assertEqual(len(hit), 1, out["excerpts"])
        self.assertTrue(hit[0].startswith("…"), hit[0])
        self.assertNotIn("excerpt_order", out)

    def test_질문_순서는_가장_가까운_조각의_청크를_먼저_고른다(self):
        self._vec("c0", [0.0, 1.0, 0.0, 0.0])
        self._vec("c1", [0.0, 0.0, 1.0, 0.0])
        self._vec("c1", [0.9, 0.1, 0.0, 0.0], seq=1)     # 청크 점수는 가장 가까운 조각의 거리다
        self._vec("c2", [0.0, 0.0, 0.0, 1.0])
        with mock.patch.dict(os.environ, {"VINC_EXCERPT_ORDER": "query"}):
            out = self._ctx(limit=1, query_embedding=[1.0, 0.0, 0.0, 0.0])
        self.assertEqual(out.get("excerpt_order"), "query", out)
        self.assertNotIn("excerpt_order_note", out)
        self.assertEqual([e["excerpt"] for e in out["excerpts"]], [head("c1")])

    def test_벡터가_빠진_청크가_있으면_순위_순서로_돌아가고_사유를_낸다(self):
        self._vec("c1", [1.0, 0.0, 0.0, 0.0])
        with mock.patch.dict(os.environ, {"VINC_EXCERPT_ORDER": "query"}):
            out = self._ctx(limit=1, query_embedding=[1.0, 0.0, 0.0, 0.0])
        self.assertEqual(out.get("excerpt_order"), "rank")
        self.assertTrue(out.get("excerpt_order_note", "").startswith("missing_vectors: 2 of 3"),
                        out)
        self.assertEqual([e["excerpt"] for e in out["excerpts"]], [head("c0")])

    def test_질문_벡터가_없으면_사유를_낸다(self):
        with mock.patch.dict(os.environ, {"VINC_EXCERPT_ORDER": "query"}):
            out = self._ctx(limit=1)
        self.assertEqual(out.get("excerpt_order"), "rank")
        self.assertEqual(out.get("excerpt_order_note"), "no_query_vector")

    def test_brief_는_발췌용_벡터를_넘기고_순서를_응답에_싣는다(self):
        for cid, e in (("c0", [0.0, 1.0, 0.0, 0.0]), ("c1", [1.0, 0.0, 0.0, 0.0]),
                       ("c2", [0.0, 0.0, 1.0, 0.0])):
            self._vec(cid, e)
        with mock.patch.dict(os.environ, {"VINC_EXCERPT_ORDER": "query"}):
            out = q.brief(self.NODE, owner=self.OWNER, store=self.store, max_excerpts=1,
                          excerpt_embedding=[1.0, 0.0, 0.0, 0.0])
        self.assertEqual(out.get("status"), "resolved", out)
        self.assertEqual(out.get("excerpt_order"), "query", out)
        self.assertEqual([e["excerpt"] for e in out["excerpts"]], [head("c1")])


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class FtsFallbackRank(_Store, unittest.TestCase):
    """링크가 없는 노드는 제목 전문 검색으로 발췌를 찾는다. 그 순위가 `IN $ids` 에서 버려졌다."""
    LINKED = False

    def test_순위_플래그는_전문_검색_순위를_지킨다(self):
        ranked = (["c2", "c1", "c0"], "")
        with mock.patch.object(q, "_owner_fts_ids", return_value=ranked):
            off = self._ctx(limit=3)
            with mock.patch.dict(os.environ, {"VINC_EXCERPT_ORDER": "rank"}):
                on = self._ctx(limit=3)
        self.assertEqual({e["method"] for e in on["excerpts"]}, {"fts"})
        self.assertEqual([e["excerpt"] for e in on["excerpts"]],
                         [head("c2"), head("c1"), head("c0")])
        self.assertEqual(on.get("excerpt_order"), "rank")
        self.assertNotIn("excerpt_order", off)

    def test_질문_순서는_전문_검색_후보를_넓혀_받는다(self):
        for cid, e in (("c0", [0.0, 1.0, 0.0, 0.0]), ("c1", [1.0, 0.0, 0.0, 0.0]),
                       ("c2", [0.0, 0.0, 1.0, 0.0])):
            self._vec(cid, e)
        calls = []

        def fake(store, text, limit, *, owner):
            calls.append(limit)
            return ["c2", "c0", "c1"][:limit], ""
        with mock.patch.object(q, "_owner_fts_ids", side_effect=fake), \
                mock.patch.dict(os.environ, {"VINC_EXCERPT_ORDER": "query"}):
            out = self._ctx(limit=1, query_embedding=[1.0, 0.0, 0.0, 0.0])
        self.assertEqual(calls, [q._excerpt.POOL])
        self.assertEqual(out.get("excerpt_order"), "query", out)
        self.assertEqual([e["excerpt"] for e in out["excerpts"]], [head("c1")])


if __name__ == "__main__":
    unittest.main()

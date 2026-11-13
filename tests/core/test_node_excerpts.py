# -*- coding: utf-8 -*-
"""node_context·edge_context 의 발췌 — 노드 토큰 창과 제목 전문 검색 폴백의 순위.

창은 노드 문장이 있는 자리를 보이고(`VINC_EXCERPT_WINDOW=head` 면 맨 앞 240자), 폴백은 BM25
순위를 지킨다. 응답 키는 늘지 않는다.
근거: [[vinc.research.node-excerpt-order-and-window-rsangmoo-2026-10-17]]"""
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
    from store.embedded_backend import import_driver
    _DRIVER, _ = import_driver()
except Exception:
    pass

LEAD = "어제 시장은 크게 흔들렸고 외국인은 팔았다. " * 20      # 노드와 무관한 앞부분(> 240자)
SENTENCE = "현금 비중은 평소 30% 정도 들고 있는 게 맞다."
#: 같은 문서의 청크 셋. 문서 순서로는 c0 → c1 → c2 이고, 노드 문장은 c1 의 240자 뒤에만 있다.
CHUNKS = (("c0", 0, "[c0] " + LEAD + "다른 이야기다."),
          ("c1", 1, "[c1] " + LEAD + SENTENCE),
          ("c2", 2, "[c2] " + LEAD + "또 다른 이야기다."))


def head(cid: str) -> str:
    text = dict((c, t) for c, _, t in CHUNKS)[cid]
    return " ".join(text.split())[:240]


class _Store:
    OWNER = "user:xnode"
    NODE = "concept:keep-cash"
    OTHER = "concept:cash-buffer"
    LINKED = True

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._env = {k: os.environ.get(k) for k in ("MEMORY_STORE_BACKEND", "MEMORY_DATA_DIR",
                                                    "VINC_EXCERPT_WINDOW")}
        os.environ.update({"MEMORY_STORE_BACKEND": "embedded",
                           "MEMORY_DATA_DIR": os.path.join(self._tmp, "store")})
        os.environ.pop("VINC_EXCERPT_WINDOW", None)
        from store.backend import open_store
        from store.config import load_config
        self.store = open_store(load_config())
        self.store.provision(dim=4)
        for nid, title, ol in ((self.NODE, "현금 비중은 평소 30% 정도", SENTENCE),
                               (self.OTHER, "현금 완충", "")):
            self.store.run(
                "MERGE (c:Concept {concept_id: $pk}) SET c.owner_id = $o, c.title = $t, "
                "c.one_liner = $ol", {"pk": pkns.key(self.OWNER, nid), "o": self.OWNER,
                                      "t": title, "ol": ol})
        for cid, idx, text in CHUNKS:
            self.store.run(
                "MERGE (ch:Chunk {chunk_id: $cid}) SET ch.owner_id = $o, "
                "ch.doc_id = 'doc1', ch.chunk_index = $idx, ch.section_title = '', "
                "ch.text = $t", {"cid": cid, "o": self.OWNER, "idx": idx, "t": text})
            if self.LINKED:
                self.store.run(
                    "MATCH (ch:Chunk {chunk_id: $cid}), (c:Concept {concept_id: $pk}) "
                    "CREATE (ch)-[:MENTIONS {method: 'title'}]->(c)",
                    {"cid": cid, "pk": pkns.key(self.OWNER, self.NODE)})

    def tearDown(self):
        self.store.close()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _ctx(self, **kw):
        return q.node_context(owner=self.OWNER, node=self.NODE, store=self.store, **kw)


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class MentionsWindow(_Store, unittest.TestCase):

    def test_기본_창은_노드_문장이_있는_자리를_보인다(self):
        out = self._ctx(limit=3)
        hit = [e["excerpt"] for e in out["excerpts"] if "30% 정도 들고" in e["excerpt"]]
        self.assertEqual(len(hit), 1, out["excerpts"])
        self.assertTrue(hit[0].startswith("…"), hit[0])

    def test_head_비상구는_예전_발췌와_같다(self):
        with mock.patch.dict(os.environ, {"VINC_EXCERPT_WINDOW": "head"}):
            out = self._ctx(limit=3)
        self.assertEqual([e["excerpt"] for e in out["excerpts"]],
                         [head("c0"), head("c1"), head("c2")])

    def test_창_모드는_응답_키를_바꾸지_않는다(self):
        node = self._ctx(limit=3)
        with mock.patch.dict(os.environ, {"VINC_EXCERPT_WINDOW": "head"}):
            old = self._ctx(limit=3)
        self.assertEqual(set(node), set(old))
        self.assertEqual([set(e) for e in node["excerpts"]], [set(e) for e in old["excerpts"]])

    def test_흐름_선_발췌도_같은_창을_쓴다(self):
        # 흐름 선은 MENTIONS 첫 청크 하나만 쓴다. 노드 문장이 있는 c1 만 남긴다.
        self.store.run("MATCH (ch:Chunk)-[m:MENTIONS]->() WHERE ch.chunk_id <> 'c1' DELETE m")
        self.store.run(
            "MATCH (a:Concept {concept_id: $a}), (b:Concept {concept_id: $b}) "
            "CREATE (a)-[:RELATED_TO {evidence: '', confidence: 0.5}]->(b)",
            {"a": pkns.key(self.OWNER, self.NODE), "b": pkns.key(self.OWNER, self.OTHER)})
        out = q.edge_context(owner=self.OWNER, src=self.NODE, dst=self.OTHER,
                             etype="RELATED_TO", store=self.store)
        self.assertTrue(out.get("executed"), out)
        ex = (out["src"].get("excerpt") or {}).get("excerpt") or ""
        self.assertTrue(ex.startswith("…") and "30% 정도 들고" in ex, ex)
        with mock.patch.dict(os.environ, {"VINC_EXCERPT_WINDOW": "head"}):
            out = q.edge_context(owner=self.OWNER, src=self.NODE, dst=self.OTHER,
                                 etype="RELATED_TO", store=self.store)
        self.assertEqual((out["src"].get("excerpt") or {}).get("excerpt"), head("c1"))


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class FtsFallbackRank(_Store, unittest.TestCase):
    """링크가 없는 노드는 제목 전문 검색으로 발췌를 찾는다. 그 순위가 `IN $ids` 에서 버려졌다."""
    LINKED = False

    def test_전문_검색_순위를_지킨다(self):
        with mock.patch.object(q, "_owner_fts_ids", return_value=(["c2", "c1", "c0"], "")), \
                mock.patch.dict(os.environ, {"VINC_EXCERPT_WINDOW": "head"}):
            out = self._ctx(limit=3)
        self.assertEqual({e["method"] for e in out["excerpts"]}, {"fts"})
        self.assertEqual([e["excerpt"] for e in out["excerpts"]],
                         [head("c2"), head("c1"), head("c0")])


if __name__ == "__main__":
    unittest.main()

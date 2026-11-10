# -*- coding: utf-8 -*-
"""질문으로 문서 미러에 닿은 brief 의 `section` — 질문이 가리키는 절.

절의 범위(`vinc_rag.excerpt.section_span`), 가리키는 청크 고르기(`api.query._brief_section`),
brief 에 붙는 자리, 축소기가 덜어내는 순서를 본다.
근거: [[vinc.research.answer-material-section-ai-corpus-2026-10-17]]"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import docstore as ds  # noqa: E402
from api import query as q  # noqa: E402
from vinc_mcp import tools  # noqa: E402
from vinc_rag import excerpt as x  # noqa: E402

_DRIVER = None
try:
    from store.embedded_backend import _import_driver
    _DRIVER, _ = _import_driver()
except Exception:
    pass


class SectionSpan(unittest.TestCase):
    CH = [{"sec": "Intro", "text": "a" * 10}, {"sec": "Setup", "text": "b" * 10},
          {"sec": "Setup", "text": "c" * 10}, {"sec": "Setup", "text": "d" * 10},
          {"sec": "", "text": "e" * 10}, {"sec": "", "text": "f" * 10}]

    def test_같은_절_제목으로_이어진_청크까지다(self):
        self.assertEqual(x.section_span(self.CH, 2)[:3], (1, 3, "b" * 10 + "c" * 10 + "d" * 10))

    def test_제목이_비면_그_청크_하나다(self):
        self.assertEqual(x.section_span(self.CH, 4)[:3], (4, 4, "e" * 10))

    def test_상한을_넘으면_가리킨_청크를_안에_둔다(self):
        lo, hi, text, cut = x.section_span(self.CH, 3, cap=12)
        self.assertEqual((lo, hi, cut, len(text)), (1, 3, True, 12))
        self.assertIn("d", text)

    def test_기본_상한은_6000자다(self):
        """위 테스트는 cap 을 직접 넘긴다. brief 는 기본값으로 부르므로 그 값을 여기서 본다."""
        self.assertEqual(x.SECTION_CHARS, 6000)
        ch = [{"sec": "S", "text": "a" * 3000}, {"sec": "S", "text": "b" * 3000}]
        self.assertEqual(x.section_span(ch, 1)[2:], ("a" * 3000 + "b" * 3000, False))
        ch[1]["text"] += "c"
        lo, hi, text, cut = x.section_span(ch, 1)
        self.assertEqual((lo, hi, cut, len(text)), (0, 1, True, 6000))
        self.assertIn("b", text)


class _FakeStore:
    """`_brief_section` 이 부르는 청크 목록 질의와 청크 벡터 거리에만 답한다."""

    def __init__(self, chunks, dist):
        self.chunks, self.dist = chunks, dist

    def chunk_vector_distances(self, ids, embedding):
        return {c: d for c, d in self.dist.items() if c in ids}

    def run(self, cypher, params=None):
        return [dict(c) for c in self.chunks]


class SectionPick(unittest.TestCase):
    CH = [{"cid": "c0", "sec": "Intro", "text": "intro text. "},
          {"cid": "c1", "sec": "Threshold", "text": "the threshold is one. "},
          {"cid": "c2", "sec": "Threshold", "text": "it applies to transformers. "},
          {"cid": "c3", "sec": "Other", "text": "unrelated tail. "}]
    ROWS = [{"id": "c0", "doc_id": "guide"}]
    NODES = ["concept:doc.guide"]

    def _pick(self, store, emb=(1.0, 0.0), nodes=NODES):
        return q._brief_section(store, owner="user:a", query="what threshold for transformers",
                                rows=self.ROWS, query_embedding=list(emb) if emb else None,
                                nodes=list(nodes))

    def test_질문_벡터에_가장_가까운_청크의_절이다(self):
        got = self._pick(_FakeStore(self.CH, {"c0": 0.4, "c2": 0.1, "c3": 0.3}))
        self.assertEqual((got["basis"], got["section"], got["chunk_ids"]),
                         ("vector", "Threshold", ["c1", "c2"]))
        self.assertEqual(got["text"], "the threshold is one. it applies to transformers. ")
        self.assertEqual((got["node_id"], got["cite_id"], got["doc_id"]),
                         ("concept:doc.guide", "concept:doc.guide", "guide"))

    def test_벡터가_없으면_검색_1위_청크다(self):
        got = self._pick(_FakeStore(self.CH, {}), emb=None)
        self.assertEqual((got["basis"], got["chunk_ids"]), ("rank", ["c0"]))

    def test_미러가_답이나_후보가_아니면_싣지_않는다(self):
        self.assertIsNone(self._pick(_FakeStore(self.CH, {}), nodes=["concept:other"]))

    def test_읽기_실패는_없음이다(self):
        class Broken:
            def run(self, *_a, **_k):
                raise RuntimeError("boom")
        self.assertIsNone(self._pick(Broken()))


class Shrink(unittest.TestCase):
    def test_발췌_다음_절_마지막이_관계다(self):
        out = {"status": "resolved", "grounds": ["concept:doc.guide"],
               "excerpts": [{"excerpt": "e" * 400}],
               "section": {"node_id": "concept:doc.guide", "text": "s" * 400},
               "links": {"items": [{"id": "concept:x", "note": "l" * 50}]}}
        size = len(json.dumps(out))
        got = tools._shrink_brief_result(json.loads(json.dumps(out)), size - 100)
        self.assertEqual(got["excerpts"], [])
        self.assertIn("section", got)
        got = tools._shrink_brief_result(json.loads(json.dumps(out)), size - 500)
        self.assertNotIn("section", got)
        self.assertTrue(got["shrunk"]["section_dropped"])
        self.assertEqual(len(got["links"]["items"]), 1)
        self.assertEqual(got["grounds"], ["concept:doc.guide"])


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class BriefSectionE2E(unittest.TestCase):
    OWNER = "user:a"
    TEXT = ("# Gradient clipping guide\n\n"
            "## Why clip\nLarge updates make training unstable, so the norm is bounded.\n\n"
            "## Threshold\nThe clipping threshold for transformers is a global norm of one. "
            "Lower values slow learning.\n\n"
            "## History\nThe method spread with recurrent networks.\n")

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._env = {k: os.environ.get(k) for k in ("MEMORY_STORE_BACKEND", "MEMORY_DATA_DIR",
                                                    "VINC_BRIEF_SECTION")}
        os.environ.update({"MEMORY_STORE_BACKEND": "embedded",
                           "MEMORY_DATA_DIR": os.path.join(self._tmp, "store")})
        os.environ.pop("VINC_BRIEF_SECTION", None)
        from store.backend import open_store
        from store.config import load_config
        self.store = open_store(load_config())
        self.store.provision(dim=4)
        got = ds.put_doc(self.store, owner=self.OWNER, doc_id="clip-guide", text=self.TEXT,
                         title="Gradient clipping guide", max_chars=120, topic="vinc/ai")
        self.assertTrue(got.get("executed"), got)

    def tearDown(self):
        self.store.close()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _brief(self, query):
        return q.brief(query, owner=self.OWNER, store=self.store)

    def test_질문_brief_에_질문이_가리키는_절이_실린다(self):
        out = self._brief("gradient clipping threshold for transformers")
        sec = out.get("section")
        self.assertIsNotNone(sec, out)
        self.assertEqual(sec["node_id"], "concept:doc.clip-guide")
        self.assertIn("global norm of one", sec["text"])
        self.assertIn("concept:doc.clip-guide", out["grounds"])
        ids = {r["cid"] for r in self.store.run(
            "MATCH (c:Chunk) WHERE c.doc_id = 'clip-guide' RETURN c.chunk_id AS cid")}
        self.assertTrue(set(sec["chunk_ids"]) <= ids)

    def test_끄면_키가_없다(self):
        with mock.patch.dict(os.environ, {"VINC_BRIEF_SECTION": "off"}):
            out = self._brief("gradient clipping threshold for transformers")
        self.assertNotIn("section", out)

    def test_id_로_물으면_질문이_없으니_싣지_않는다(self):
        out = self._brief("concept:doc.clip-guide")
        self.assertEqual(out.get("resolved_via"), "id")
        self.assertNotIn("section", out)


if __name__ == "__main__":
    unittest.main()

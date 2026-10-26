# -*- coding: utf-8 -*-
"""brief 의 `quotes` — 답을 쓰는 쪽이 그대로 따올 수 있는 글자 그대로의 인용 단위.

단위 고르기(`vinc_rag.excerpt.quote_units`), 노드에서 단위를 모으는 길(`api.query._brief_node_quotes`),
꺼짐 스위치, 응답이 커질 때 축소기가 덜어내는 순서를 본다.
근거: [[vinc.manual.rag]]"""
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

TEXT = ("## Why\nA linear classifier draws one line. No single line separates the classes in XOR. "
        "This is why a hidden layer is needed. The three settings are zero-shot, one-shot and few-shot.\n\n"
        "- the rest depends on the dataset.\n")


def _flat(s: str) -> str:
    return " ".join(s.split())


class QuoteUnits(unittest.TestCase):
    def test_질문_토큰이_가장_많이_걸린_문장이_먼저다(self):
        got = x.quote_units(TEXT, "why can't a line separate XOR classes")
        self.assertEqual(got[0]["text"], "No single line separates the classes in XOR.")
        self.assertFalse(got[0]["widened"])

    def test_가리키는_말로_열리면_앞_문장을_함께_싣는다(self):
        got = x.quote_units(TEXT, "why is a hidden layer needed")
        self.assertTrue(got[0]["widened"])
        self.assertTrue(got[0]["text"].startswith("No single line separates the classes in XOR."))
        self.assertTrue(got[0]["text"].endswith("a hidden layer is needed."))

    def test_앞_문장을_붙이는_횟수는_둘까지다(self):
        text = "Alpha is a thing. It has beta. It has gamma. It has delta."
        got = x.quote_units(text, "delta", n=1)
        self.assertEqual(got[0]["text"], "It has beta. It has gamma. It has delta.")

    def test_단위는_본문의_이어진_글자_그대로다(self):
        for query in ("hidden layer", "three settings", "dataset", "line"):
            for u in x.quote_units(TEXT, query, n=3):
                self.assertIn(u["text"], _flat(TEXT))

    def test_제목_줄은_단위가_되지_않는다(self):
        for u in x.quote_units(TEXT, "Why", n=5):
            self.assertNotIn("##", u["text"])

    def test_걸리는_토큰이_없으면_맨_앞_문장_하나다(self):
        got = x.quote_units(TEXT, "zzz qqq")
        self.assertEqual([u["text"] for u in got], ["A linear classifier draws one line."])

    def test_겹치는_단위는_하나만_낸다(self):
        got = x.quote_units(TEXT, "classes XOR hidden layer", n=2)
        spans = [u["text"] for u in got]
        self.assertEqual(len(spans), len(set(spans)))
        if len(spans) == 2:
            self.assertFalse(spans[0] in spans[1] or spans[1] in spans[0])

    def test_상한을_넘으면_낱말_경계에서_앞부분만_싣는다(self):
        text = "Only " + " ".join(["word"] * 400) + "."
        u = x.quote_units(text, "word", cap=100)[0]["text"]
        self.assertLessEqual(len(u), 100)
        self.assertTrue(text.startswith(u))

    def test_한국어_지시어로_열리면_앞_문장을_붙인다(self):
        text = "경사 하강법은 손실을 줄이는 방법이다. 이것은 학습률에 민감하다."
        got = x.quote_units(text, "학습률", n=1)
        self.assertTrue(got[0]["widened"])
        self.assertTrue(got[0]["text"].startswith("경사 하강법은"))

    def test_빈_본문은_빈_목록이다(self):
        self.assertEqual(x.quote_units("", "anything"), [])
        self.assertEqual(x.quote_units("## only a heading\n", "anything"), [])


class _FakeStore:
    """`_brief_node_quotes` 가 부르는 질의에만 답한다: 청크 목록, 청크 벡터 거리, 문서 제목."""

    def __init__(self, chunks, dist=None, title="Guide to XOR"):
        self.chunks, self.dist, self.title = chunks, dist or {}, title
        self.cyphers: list[str] = []

    def run(self, cypher, params=None):
        self.cyphers.append(cypher)
        if "ChunkVec" in cypher:
            return [{"cid": c, "d": d} for c, d in self.dist.items()]
        if "_DocMeta" in cypher:
            return [{"t": self.title}]
        return [dict(c) for c in self.chunks]


class NodeQuotes(unittest.TestCase):
    CH = [{"cid": "c0", "doc_id": "guide", "sec": "Intro", "text": "Intro words. Nothing here."},
          {"cid": "c1", "doc_id": "guide", "sec": "Why", "text": TEXT}]

    def _quotes(self, store, node="concept:doc.guide", emb=(1.0, 0.0), query="why is a hidden layer needed"):
        return q._brief_node_quotes(store, owner="user:a", node_id=node, query=query,
                                    query_embedding=list(emb) if emb else None)

    def test_문서_미러는_질문_벡터에_가장_가까운_청크에서_낸다(self):
        got = self._quotes(_FakeStore(self.CH, {"c0": 0.6, "c1": 0.1}))
        self.assertTrue(got)
        self.assertEqual({(u["doc_id"], u["source"], u["section"]) for u in got},
                         {("guide", "Guide to XOR", "Why")})
        self.assertIn("a hidden layer is needed.", got[0]["text"])

    def test_벡터가_없으면_질문_토큰이_가장_많은_청크에서_낸다(self):
        got = self._quotes(_FakeStore(self.CH), emb=None)
        self.assertEqual(got[0]["section"], "Why")

    def test_개념은_MENTIONS_청크에서_낸다(self):
        store = _FakeStore(self.CH, {"c1": 0.1})
        got = self._quotes(store, node="concept:xor")
        self.assertTrue(got)
        self.assertTrue(any("MENTIONS" in c for c in store.cyphers))

    def test_다른_종류의_노드는_빈_목록이다(self):
        self.assertEqual(self._quotes(_FakeStore(self.CH), node="record:something"), [])
        self.assertEqual(self._quotes(_FakeStore(self.CH), node="episode:something"), [])

    def test_청크가_없으면_빈_목록이다(self):
        self.assertEqual(self._quotes(_FakeStore([])), [])

    def test_읽기_실패는_빈_목록이다(self):
        class Broken:
            def run(self, *_a, **_k):
                raise RuntimeError("boom")
        self.assertEqual(self._quotes(Broken()), [])

    def test_스위치(self):
        with mock.patch.dict(os.environ, {"VINC_BRIEF_QUOTES": "off"}):
            self.assertFalse(q._brief_quotes_on())
        with mock.patch.dict(os.environ, {"VINC_BRIEF_QUOTES": ""}):
            self.assertTrue(q._brief_quotes_on())


class Shrink(unittest.TestCase):
    def _out(self):
        return {"status": "choose", "grounds": ["concept:a", "concept:b"],
                "candidates": [{"id": "concept:a", "quotes": [{"text": "q" * 300}]},
                               {"id": "concept:b", "quotes": [{"text": "q" * 300}]}]}

    def test_뒤_후보의_인용부터_덜어낸다(self):
        out = self._out()
        size = len(json.dumps(out, ensure_ascii=False))
        got = tools._shrink_brief_result(json.loads(json.dumps(out)), size - 100)
        self.assertIn("quotes", got["candidates"][0])
        self.assertNotIn("quotes", got["candidates"][1])
        self.assertEqual(got["shrunk"]["quotes_dropped"], 1)
        self.assertEqual(got["grounds"], ["concept:a", "concept:b"])

    def test_예산_안이면_건드리지_않는다(self):
        out = self._out()
        size = len(json.dumps(out, ensure_ascii=False))
        got = tools._shrink_brief_result(json.loads(json.dumps(out)), size + 10)
        self.assertEqual(got, out)


_DRIVER = None
try:
    from store.embedded_backend import _import_driver
    _DRIVER, _ = _import_driver()
except Exception:
    pass


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class BriefQuotesE2E(unittest.TestCase):
    OWNER = "user:a"
    TEXT = ("# Gradient clipping guide\n\n"
            "## Why clip\nLarge updates make training unstable, so the norm is bounded.\n\n"
            "## Threshold\nThe clipping threshold for transformers is a global norm of one. "
            "This keeps updates small. Lower values slow learning.\n\n"
            "## History\nThe method spread with recurrent networks.\n")

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._env = {k: os.environ.get(k) for k in ("MEMORY_STORE_BACKEND", "MEMORY_DATA_DIR",
                                                    "VINC_BRIEF_QUOTES")}
        os.environ.update({"MEMORY_STORE_BACKEND": "embedded",
                           "MEMORY_DATA_DIR": os.path.join(self._tmp, "store")})
        os.environ.pop("VINC_BRIEF_QUOTES", None)
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

    def _all_quotes(self, out):
        rows = list(out.get("quotes") or [])
        for c in out.get("candidates") or []:
            rows += list(c.get("quotes") or [])
        return rows

    def test_문서_미러_답에_출처와_절을_단_인용_단위가_실린다(self):
        out = self._brief("gradient clipping threshold for transformers")
        rows = self._all_quotes(out)
        self.assertTrue(rows, out)
        flat = _flat(self.TEXT)
        for r in rows:
            self.assertEqual((r["doc_id"], r["source"]), ("clip-guide", "Gradient clipping guide"))
            self.assertIn(r["text"], flat)
        self.assertTrue(any("global norm of one" in r["text"] for r in rows))

    def test_끄면_키가_없다(self):
        with mock.patch.dict(os.environ, {"VINC_BRIEF_QUOTES": "off"}):
            out = self._brief("gradient clipping threshold for transformers")
        self.assertEqual(self._all_quotes(out), [])

    def test_id_로_물으면_질문이_없으니_싣지_않는다(self):
        out = self._brief("concept:doc.clip-guide")
        self.assertEqual(out.get("resolved_via"), "id")
        self.assertNotIn("quotes", out)


if __name__ == "__main__":
    unittest.main()

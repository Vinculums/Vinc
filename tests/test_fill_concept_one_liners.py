# -*- coding: utf-8 -*-
"""Concept one_liner 인용 채움 — 생성 없이 이미 있는 문장만 고른다.

근거: [[record:the-one-liner-join-improves-no-item-and-costs-four-2026-09-16]]
근거: [[record:korean-titled-nodes-answer-with-provenance-excerpts-2026-09-14]]
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import fill_concept_one_liners as fill  # noqa: E402
from api import fragment as frag  # noqa: E402
from api import query as q  # noqa: E402
from store.embedded_backend import _import_driver  # noqa: E402

_DRIVER, _ = _import_driver()


class PickQuoteTests(unittest.TestCase):
    def test_칸이_있으면_건드리지_않는다(self):
        self.assertIsNone(fill.pick_quote(
            one_liner="이미 있다.", props={"note": "다른 문장"},
            excerpts=[{"excerpt": "발췌", "method": "title", "doc_id": "d"}]))

    def test_props_note_를_칸으로_올린다(self):
        got = fill.pick_quote(one_liner="", props={"note": "  핵심 한 줄.  "},
                              excerpts=[])
        self.assertEqual((got["text"], got["src"], got["cite"]),
                         ("핵심 한 줄.", "props", "note"))

    def test_props_one_liner_가_note_보다_이긴다(self):
        got = fill.pick_quote(one_liner="",
                              props={"one_liner": "먼저", "note": "나중"},
                              excerpts=[])
        self.assertEqual(got["text"], "먼저")

    def test_title_발췌만_쓰고_source_와_fts_는_버린다(self):
        empty = fill.pick_quote(one_liner="", props={}, excerpts=[
            {"excerpt": "파일 머리말", "method": "source", "doc_id": "d1"},
            {"excerpt": "제목 검색 폴백", "method": "fts", "doc_id": "d2"},
        ])
        self.assertIsNone(empty)
        got = fill.pick_quote(one_liner="", props={}, excerpts=[
            {"excerpt": "파일 머리말", "method": "source", "doc_id": "d1"},
            {"excerpt": "이 개념을 말하는 문장.", "method": "title", "doc_id": "d3"},
        ])
        self.assertEqual((got["text"], got["src"]), ("이 개념을 말하는 문장.", "excerpt"))
        self.assertIn("d3", got["cite"])

    def test_stem_도_인용이다(self):
        got = fill.pick_quote(one_liner="", props={}, excerpts=[
            {"excerpt": "어간으로 묶인 문장.", "method": "stem", "doc_id": "d"}])
        self.assertEqual(got["src"], "excerpt")

    def test_정답_파일에서_concept_id_만_걷는다(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "gold.jsonl"
            p.write_text(
                '{"query": "do not reuse", "gold_node_id": "concept:a", "gold_kind": "concept"}\n'
                '{"query": "x", "gold_node_id": "record:r", "gold_kind": "record"}\n'
                '{"query": "y", "gold_node_id": "", "answerable": false}\n',
                encoding="utf-8")
            self.assertEqual(fill.load_id_filter(str(p)), {"concept:a"})


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class FillOneLinerE2E(unittest.TestCase):
    OWNER = "user:a"

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
        frag.merge_fragment({"owner": self.OWNER, "nodes": [
            {"id": "concept:filled", "type": "concept", "title": "filled",
             "domain": "eng", "one_liner": "이미 칸이 있다."},
            {"id": "concept:from-props", "type": "concept", "title": "from props",
             "domain": "eng", "props": {"note": "props 에만 있던 문장."}},
            {"id": "concept:empty", "type": "concept", "title": "empty",
             "domain": "eng"},
        ]}, store=self.store)
        # from-props 는 승격이 칸을 이미 채웠을 수 있다. 빈 칸을 재현하려면
        # 승격 없이 열만 비운 행이 필요하므로, 열이 비어 있는 empty 만 dry-run 대상으로 본다.

    def tearDown(self):
        self.store.close()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_dry_run_은_쓰지_않는다(self):
        rows = fill.collect(self.store, self.OWNER)
        planned = [fill.plan_one(self.store, self.OWNER, r) for r in rows]
        by = {r["id"]: r for r in planned}
        self.assertEqual(by["concept:filled"]["status"], "filled")
        self.assertEqual(by["concept:empty"]["status"], "empty")
        # 승격이 있는 스토어에서는 from-props 가 이미 filled 다. 어느 쪽이든 생성은 없다.
        self.assertIn(by["concept:from-props"]["status"], ("filled", "fillable"))

    def test_apply_는_fillable_만_쓰고_이미_있는_칸은_남긴다(self):
        rec = {"id": "concept:empty", "status": "fillable",
               "text": "인용한 한 줄.", "src": "props", "cite": "note"}
        out = fill.apply_one(self.store, self.OWNER, rec)
        self.assertFalse(out.get("partial"), out)
        ctx = q.node_context(owner=self.OWNER, node="concept:empty", store=self.store)
        self.assertEqual(ctx.get("one_liner"), "인용한 한 줄.")
        kept = q.node_context(owner=self.OWNER, node="concept:filled", store=self.store)
        self.assertEqual(kept.get("one_liner"), "이미 칸이 있다.")

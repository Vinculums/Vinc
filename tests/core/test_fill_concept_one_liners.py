# -*- coding: utf-8 -*-
"""Concept one_liner 인용 채움 — 생성 없이 props 에 이미 있는 문장만 올린다.

근거: [[record:the-one-liner-join-improves-no-item-and-costs-four-2026-09-16]]
근거: [[record:korean-titled-nodes-answer-with-provenance-excerpts-2026-09-14]]
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import fill_concept_one_liners as fill  # noqa: E402
from api import fragment as frag  # noqa: E402
from store import pkns  # noqa: E402
from store.embedded_backend import _import_driver  # noqa: E402

_DRIVER, _ = _import_driver()


class PickQuoteTests(unittest.TestCase):
    def test_칸이_있으면_건드리지_않는다(self):
        got = fill.pick_quote(one_liner="이미 있다.", props={"note": "다른 문장"})
        self.assertEqual(got, {"status": "filled"})

    def test_props_note_를_칸으로_올린다(self):
        got = fill.pick_quote(one_liner="", props={"note": "  핵심 한 줄.  "})
        self.assertEqual(got, {"status": "fillable", "text": "핵심 한 줄.", "cite": "note"})

    def test_props_one_liner_가_note_보다_이긴다(self):
        got = fill.pick_quote(one_liner="",
                              props={"one_liner": "먼저", "note": "나중"})
        self.assertEqual((got["text"], got["cite"]), ("먼저", "one_liner"))

    def test_props_가_JSON_문자열이어도_읽는다(self):
        got = fill.pick_quote(one_liner=None, props='{"note": "열에서 온 문장."}')
        self.assertEqual(got["text"], "열에서 온 문장.")

    def test_인용할_문장이_없으면_empty(self):
        self.assertEqual(fill.pick_quote(one_liner="", props={"alpha": 1}),
                         {"status": "empty"})


class ArgsTests(unittest.TestCase):
    def test_apply_는_out_없이_거부된다(self):
        with contextlib.redirect_stderr(io.StringIO()), \
                self.assertRaises(SystemExit) as cm:
            fill.main(["--data", "unused", "--owner", "user:a", "--apply"])
        self.assertNotEqual(cm.exception.code, 0)


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class FillOneLinerE2E(unittest.TestCase):
    OWNER = "user:a"
    OLD_TEXT = "props 에만 있던 문장."

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.data = os.path.join(self._tmp, "store")
        self._env = {}
        for k, v in {"MEMORY_STORE_BACKEND": "embedded",
                     "MEMORY_DATA_DIR": self.data}.items():
            self._env[k] = os.environ.get(k)
            os.environ[k] = v
        store = self._open()
        store.provision(dim=4)
        frag.merge_fragment({"owner": self.OWNER, "nodes": [
            {"id": "concept:filled", "type": "concept", "title": "filled",
             "domain": "eng", "one_liner": "이미 칸이 있다."},
            # 쓰기 승격이 칸을 채운다 → filled.
            {"id": "concept:from-props", "type": "concept", "title": "from props",
             "domain": "eng", "props": {"note": "승격된 문장."}},
            # 승격 전에 쓰인 옛 행: props 에 문장이 있는데 칸이 비어 있다 → fillable.
            {"id": "concept:old-row", "type": "concept", "title": "old row",
             "domain": "eng", "tags": ["a", "b"], "one_liner": "",
             "props": {"note": self.OLD_TEXT, "alpha": 1}},
            {"id": "concept:empty", "type": "concept", "title": "empty",
             "domain": "eng"},
        ]}, store=store)
        self.before = self._raw(store)
        store.close()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _open(self):
        from store.backend import open_store
        from store.config import load_config
        return open_store(load_config({"data_dir": self.data}))

    def _raw(self, store=None) -> dict:
        own = store is None
        store = store or self._open()
        try:
            rows = store.run(
                "MATCH (c:Concept) WHERE c.owner_id = $o "
                "RETURN c.concept_id AS id, c.title AS title, c.domain AS domain, "
                "c.tags AS tags, c.props AS props, c.one_liner AS one_liner",
                {"o": self.OWNER}) or []
        finally:
            if own:
                store.close()
        return {pkns.strip(str(r["id"])): {k: r[k] for k in r if k != "id"}
                for r in rows}

    def _main(self, *extra) -> dict:
        out = os.path.join(self._tmp, "out.json")
        with contextlib.redirect_stdout(io.StringIO()):
            rc = fill.main(["--data", self.data, "--owner", self.OWNER,
                            "--out", out, *extra])
        self.assertEqual(rc, 0)
        return json.loads(Path(out).read_text(encoding="utf-8"))

    def test_dry_run_은_쓰지_않는다(self):
        got = self._main()
        by = {r["id"]: r["status"] for r in got["rows"]}
        self.assertEqual(by, {"concept:filled": "filled",
                              "concept:from-props": "filled",
                              "concept:old-row": "fillable",
                              "concept:empty": "empty"})
        self.assertEqual(got["applied"], [])
        after = self._raw()
        self.assertEqual(after["concept:old-row"]["one_liner"], "")
        self.assertEqual(after, self.before)

    def test_apply_는_칸만_쓰고_다른_필드는_그대로다(self):
        got = self._main("--apply")
        self.assertEqual([x["id"] for x in got["applied"]], ["concept:old-row"])
        self.assertFalse(got["applied"][0]["partial"], got["applied"])
        after = self._raw()
        old_b, old_a = self.before["concept:old-row"], after["concept:old-row"]
        self.assertEqual(old_a["one_liner"], self.OLD_TEXT)
        for field in ("title", "domain", "tags", "props"):
            self.assertTrue(old_b[field], field)  # 네 필드가 모두 찬 행이어야 시험이 된다
            self.assertEqual(old_a[field], old_b[field], field)
        # 칸이 차 있거나 인용할 것이 없는 행은 한 바이트도 안 바뀐다.
        for nid in ("concept:filled", "concept:from-props", "concept:empty"):
            self.assertEqual(after[nid], self.before[nid], nid)

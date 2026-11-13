# -*- coding: utf-8 -*-
"""choose 의 `candidate_material` — 되묻기 응답에 첫 후보의 one_liner 와 발췌(최대 2)를 싣는다.

목록(candidates)·status 는 그대로이고, 후보가 없으면 키가 없다. MCP 다이어트(`lean_result`)는 재료
안의 기본 시간 블록과 평평한 사본도 확정 답처럼 줄이고, `raw:true` 는 전체 꼴을 돌려준다. 축소기는
재료의 발췌를 맨 먼저 던다.
근거: [[vinc.plan.choose-cost-measurement-2026-10-19]]"""
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
from api import fragment as frag  # noqa: E402
from api import query as q  # noqa: E402
from store.embedded_backend import import_driver  # noqa: E402
from vinc_mcp import tools  # noqa: E402

_DRIVER, _NAME = import_driver()


class ShrinkFirst(unittest.TestCase):
    def _out(self):
        return {"status": "choose", "grounds": [],
                "candidates": [{"id": "concept:a", "quotes": [{"text": "q" * 300}]}],
                "candidate_material": {"candidate": "concept:a", "one_liner": "a",
                                       "excerpts": [{"doc_id": f"d{i}", "excerpt": "x" * 300}
                                                    for i in range(2)]}}

    def test_재료의_발췌를_후보_인용보다_먼저_던다(self):
        out = self._out()
        size = len(json.dumps(out, ensure_ascii=False))
        got = tools._shrink_brief_result(json.loads(json.dumps(out)), size - 100)
        self.assertEqual(len(got["candidate_material"]["excerpts"]), 1)
        self.assertIn("quotes", got["candidates"][0])
        self.assertEqual(got["shrunk"]["excerpts_dropped"], 1)
        self.assertEqual(got["candidate_material"]["one_liner"], "a")


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class ChooseMaterialE2E(unittest.TestCase):
    OWNER = "user:a"

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._env = {k: os.environ.get(k) for k in ("MEMORY_STORE_BACKEND", "MEMORY_DATA_DIR")}
        os.environ.update({"MEMORY_STORE_BACKEND": "embedded",
                           "MEMORY_DATA_DIR": os.path.join(self._tmp, "store")})
        from store.backend import open_store
        from store.config import load_config
        self.store = open_store(load_config())
        self.store.provision(dim=4)
        frag.merge_fragment({"owner": self.OWNER, "nodes": [
            {"id": "concept:alpha-core", "type": "concept", "title": "alpha core",
             "domain": "eng", "one_liner": "The core of alpha."},
            {"id": "concept:alpha-edge", "type": "concept", "title": "alpha edge",
             "domain": "eng"},
        ]}, store=self.store)
        text = "\n\n".join(f"## Part {i}\nThe alpha core holds rule {i}. The alpha edge is near."
                           for i in range(6))
        got = ds.put_doc(self.store, owner=self.OWNER, doc_id="alpha-guide", text=text,
                         title="Alpha guide", max_chars=80, topic="eng")
        self.assertTrue(got.get("executed"), got)

    def tearDown(self):
        self.store.close()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _brief(self, query, **kw):
        return q.brief(query, owner=self.OWNER, store=self.store, **kw)

    def test_첫_후보의_본문과_발췌_둘이_실린다(self):
        out = self._brief("alpha")
        self.assertEqual(out["status"], "choose")
        mat = out["candidate_material"]
        first = out["candidates"][0]["id"]
        self.assertEqual((mat["candidate"], mat["cite_id"]), (first, first))
        ctx = q.node_context(owner=self.OWNER, node=first, store=self.store)
        self.assertEqual(mat["one_liner"], q._brief_body(ctx)["text"])
        self.assertTrue(mat["one_liner"])
        self.assertEqual(len(mat["excerpts"]), 2, "발췌가 여섯 청크에서 둘로 잘려야 한다")
        self.assertEqual([e["excerpt"] for e in mat["excerpts"]],
                         [e["excerpt"] for e in ctx["excerpts"][:2]])
        # 확정 답의 칸은 생기지 않는다.
        for k in ("node", "one_liner", "excerpts", "links"):
            self.assertNotIn(k, out)

    def test_max_excerpts_가_더_작으면_그쪽이다(self):
        mat = self._brief("alpha", max_excerpts=1)["candidate_material"]
        self.assertEqual(len(mat["excerpts"]), 1)

    def test_목록과_나머지_응답은_재료가_없을_때와_같다(self):
        self._brief("alpha")                           # 지연 색인 빌드를 먼저 치른다
        on = self._brief("alpha")
        with mock.patch.object(q, "_brief_candidate_material", return_value=None):
            off = self._brief("alpha")
        self.assertNotIn("candidate_material", off)
        on.pop("candidate_material")
        self.assertEqual(json.dumps(on, ensure_ascii=False), json.dumps(off, ensure_ascii=False))

    def test_후보가_없으면_싣지_않는다(self):
        out = self._brief("존재하지않는질문어휘")
        self.assertEqual((out["status"], out["candidates"]), ("none", []))
        self.assertNotIn("candidate_material", out)

    def test_확정_답에는_싣지_않는다(self):
        out = self._brief("alpha core")
        self.assertEqual(out["status"], "resolved")
        self.assertNotIn("candidate_material", out)

    def test_MCP_다이어트가_재료의_기본_시간_블록을_줄이고_raw_는_되돌린다(self):
        out = self._brief("alpha")
        full = out["candidate_material"]["excerpts"][0]
        self.assertIn("validity", full["temporal"])
        self.assertIn("validity", full, "평평한 사본이 없으면 이 검사는 공허하다")
        lean = tools.lean_result("vinc_brief", {}, out)
        ex = lean["candidate_material"]["excerpts"][0]
        self.assertEqual(set(ex["temporal"]), {"updated_at"})
        for k in tools._TEMPORAL_FLAT:
            self.assertNotIn(k, ex)
        self.assertEqual(ex["excerpt"], full["excerpt"])
        # 재료의 칸(후보·한줄 설명·본문 출처·발췌)과 후보 목록은 그대로다.
        for k in ("candidate", "one_liner", "body_src"):
            if k in out["candidate_material"]:
                self.assertEqual(lean["candidate_material"][k], out["candidate_material"][k], k)
        self.assertEqual(len(lean["candidate_material"]["excerpts"]),
                         len(out["candidate_material"]["excerpts"]))
        self.assertEqual([c["id"] for c in lean["candidates"]], [c["id"] for c in out["candidates"]])
        self.assertIs(tools.lean_result("vinc_brief", {"raw": True}, out), out)


if __name__ == "__main__":
    unittest.main()

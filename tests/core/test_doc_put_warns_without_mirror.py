# -*- coding: utf-8 -*-
"""`put_doc` 이 미러 없이 끝난 쓰기를 말한다 — 프런트매터가 주제나 관계를 적어 뒀을 때.

미러(`concept:doc.<id>`)는 `topic` 을 받을 때만 생기고, 주제와 관계는 그 미러에 붙는다.
topic 없이 쓰면 성공만 답해서 문서 74개가 토픽도 관계도 없이 남았다.
근거: [[record:seventy-four-documents-had-no-mirror-because-a-doc-put-without-a-topic-creates-none-2026-10-31]]

여기서 고정하는 것:
  ① 세 반환 경로(새로 씀 · 무변경 · 메타만 바뀜)가 모두 말한다.
  ② topic 을 주거나 미러가 이미 있으면 조용하다.
  ③ 프런트매터가 주제도 관계도 적지 않았으면 조용하다(동기화·팩 가져오기의 소음 방지).
  ④ 거절하지 않는다. 저장은 그대로 된다."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import docstore  # noqa: E402

_DRIVER = None
try:
    from store.embedded_backend import import_driver
    _DRIVER, _ = import_driver()
except Exception:
    pass

_FM = "\n".join(["---", "layer: plan", "domain: vinc/engineering/docstore",
                 "derives_from: [vinc.plan.earlier]", "related: [vinc.spec.x]", "---", "",
                 "# 계획", "", "본문 한 줄.", ""])
_REL_ONLY = "\n".join(["---", "layer: plan", "inherits_from: [vinc.spec.x]", "---", "",
                       "# 계획", "", "본문.", ""])
_PLAIN_FM = "\n".join(["---", "layer: spec", "---", "", "# 제목", "", "본문.", ""])


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class PutDocWarnsWithoutMirrorE2E(unittest.TestCase):
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

    def tearDown(self):
        self.store.close()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _put(self, text, doc_id, **kw):
        return docstore.put_doc(self.store, owner=self.OWNER, text=text,
                                doc_id=doc_id, title="T", **kw)

    def _mirror_warning(self, r):
        return [w for w in r["warnings"] if "does not exist" in w]

    def test_새_문서가_주제와_관계를_적었는데_topic_이_없으면_말한다(self):
        r = self._put(_FM, "vinc.plan.a")
        self.assertTrue(r["executed"])
        self.assertIs(r["created"], True)
        w = " ".join(self._mirror_warning(r))
        self.assertIn("concept:doc.vinc.plan.a", w)
        self.assertIn("vinc/engineering/docstore", w)
        self.assertIn("derives_from", w)
        self.assertIn("related", w)

    def test_관계_줄만_있어도_말한다(self):
        w = " ".join(self._mirror_warning(self._put(_REL_ONLY, "vinc.plan.b")))
        self.assertIn("inherits_from", w)
        self.assertNotIn("domain:", w)

    def test_무변경_재적재도_말한다(self):
        self._put(_FM, "vinc.plan.c")
        r = self._put(_FM, "vinc.plan.c")
        self.assertTrue(r["skipped"])
        self.assertTrue(self._mirror_warning(r))

    def test_메타만_바뀐_재적재도_말한다(self):
        self._put(_FM, "vinc.plan.d")
        r = self._put(_FM, "vinc.plan.d", valid_from="2026-10-31")
        self.assertIs(r.get("skipped"), False)
        self.assertTrue(self._mirror_warning(r))

    def test_topic_을_주면_조용하다(self):
        r = self._put(_FM, "vinc.plan.e", topic="vinc/engineering/docstore")
        self.assertEqual(self._mirror_warning(r), [])

    def test_미러가_이미_있으면_topic_없이_써도_조용하다(self):
        self._put(_FM, "vinc.plan.f", topic="vinc/engineering/docstore")
        r = self._put(_FM + "덧붙임.\n", "vinc.plan.f")
        self.assertEqual(self._mirror_warning(r), [])

    def test_프런트매터가_주제도_관계도_없으면_조용하다(self):
        self.assertEqual(self._put(_PLAIN_FM, "vinc.plan.g")["warnings"], [])
        self.assertEqual(self._put("# 제목만\n\n본문", "vinc.plan.h")["warnings"], [])

    def test_본문은_한_글자도_안_바뀐다(self):
        self._put(_FM, "vinc.plan.i")
        got = docstore.get_doc(self.store, "vinc.plan.i", owner=self.OWNER)
        self.assertIs(got["verified"], True)
        self.assertEqual(got["text"], _FM)


if __name__ == "__main__":
    unittest.main()

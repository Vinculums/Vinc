# -*- coding: utf-8 -*-
"""`put_doc` 이 자기가 무엇을 했는지 말한다 — 만들었나 갈아치웠나.

2026-08-25 에 이 침묵이 사본을 하나 만들었다. 문서 본문의 프런트매터가
`doc_id: vinc.ops.graph-claim-decay` 라고 선언하는데 **그건 그 문서의 id 가 아니다**
(실제 id 는 `d4ab94561078498e3`). 그 선언값을 읽어 `vinc_doc_put` 에 넘겼더니 고쳐지지
않고 **제목이 같은 문서가 하나 더** 생겼고, 유일한 신호는 성공 응답 안의 `replaced: 0`
이었다. 그건 "내용이 안 바뀐 교체"와도 같은 값이라 구분이 안 된다.

여기서 고정하는 것 넷:
  ① `created` 가 만들기와 교체를 가른다 — `replaced` 만으로는 못 가른다.
  ② 본문 선언과 대상이 다르면 **말한다**. 거부는 안 한다 — 같은 깔때기를 동기화와
     팩 가져오기가 쓰고, 거기 문서들은 정당하게 자기 이름을 적고 있다.
  ③ **본문은 한 글자도 안 바뀐다.** text 는 Chunk=SSoT 이고 `verified` 는 그 바이트의
     sha256 이라, 프런트매터를 손대는 순간 그 검증이 거짓이 된다.
  ④ 흔한 정상 경우(선언과 대상이 같음)는 **조용하다**. 안 그러면 경고가 소음이 되고,
     소음이 된 경고는 안 읽힌다."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import docstore  # noqa: E402

_DRIVER = None
try:
    from store.embedded_backend import _import_driver
    _DRIVER, _ = _import_driver()
except Exception:
    pass

_BODY = "\n".join(["---", "doc_id: vinc.a.declared", "layer: spec", "---", "",
                   "# 제목", "", "본문 한 줄.", ""])


class DeclaredIdReaderTests(unittest.TestCase):
    """판독기만 따로 — 스토어 없이 도는 층이라 왜 틀렸는지 두 겹을 가를 수 있다."""

    def test_프런트매터의_doc_id_를_읽는다(self):
        self.assertEqual(docstore._declared_doc_id(_BODY), "vinc.a.declared")

    def test_프런트매터가_없으면_빈_문자열(self):
        self.assertEqual(docstore._declared_doc_id("# 제목만\n\n본문"), "")

    def test_프런트매터에_doc_id_가_없으면_빈_문자열(self):
        self.assertEqual(
            docstore._declared_doc_id("---\nlayer: spec\n---\n\n# T"), "")

    def test_CRLF_본문도_읽는다(self):
        self.assertEqual(
            docstore._declared_doc_id("---\r\ndoc_id: q\r\n---\r\n\r\n# T"), "q")

    def test_본문_중간의_doc_id_는_프런트매터가_아니다(self):
        """프런트매터는 **첫머리**다. 본문이 doc_id 를 인용만 해도 잡으면
        경고가 거짓으로 뜨고, 거짓 경고는 진짜 경고까지 못 읽게 만든다."""
        self.assertEqual(
            docstore._declared_doc_id("# T\n\n참고: doc_id: vinc.x.y 를 보라\n"), "")


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class PutDocSaysWhatItDidE2E(unittest.TestCase):
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

    def _put(self, text, doc_id, title="T"):
        return docstore.put_doc(self.store, owner=self.OWNER, text=text,
                                doc_id=doc_id, title=title)

    def test_새_문서는_created_가_참이다(self):
        r = self._put("# 하나\n\n본문", "vinc.a.new")
        self.assertTrue(r["executed"])
        self.assertIs(r["created"], True)
        self.assertEqual(r["replaced"], 0)

    def test_교체는_created_가_거짓이다(self):
        self._put("# 하나\n\n본문", "vinc.a.same")
        r = self._put("# 하나\n\n본문이 바뀐다", "vinc.a.same")
        self.assertIs(r["created"], False)
        self.assertGreater(r["replaced"], 0)

    def test_replaced_0_만으로는_못_가른다(self):
        """이 검사가 이 파일의 이유다. 새 문서와 '내용 같은 재적재'가 둘 다
        replaced 0 을 낸다 — `created` 가 그 둘을 가르는 유일한 값이다."""
        fresh = self._put("# 하나\n\n본문", "vinc.a.x")
        again = self._put("# 하나\n\n본문", "vinc.a.x")     # 무변경 → skipped
        self.assertEqual(fresh["replaced"], again.get("replaced", 0))
        self.assertIsNot(fresh["created"], again["created"])

    def test_무변경_재적재도_같은_키를_돌려준다(self):
        """skipped 분기가 created·warnings 를 빠뜨리면 호출부가 보는 봉투가
        분기마다 달라진다 — 그 자체가 조용한 함정이다."""
        self._put("# 하나\n\n본문", "vinc.a.y")
        r = self._put("# 하나\n\n본문", "vinc.a.y")
        self.assertTrue(r["skipped"])
        for key in ("created", "warnings"):
            self.assertIn(key, r)
        self.assertIs(r["created"], False)

    def test_본문_선언과_대상이_다르면_경고한다(self):
        r = self._put(_BODY, "d1234567890abcdef")
        self.assertTrue(r["warnings"], r)
        w = " ".join(r["warnings"])
        self.assertIn("vinc.a.declared", w)
        self.assertIn("d1234567890abcdef", w)
        # 저장은 된다 — 막으면 동기화·팩 가져오기가 선다.
        self.assertTrue(r["executed"])
        self.assertIs(r["created"], True)

    def test_선언과_대상이_같으면_조용하다(self):
        """흔한 정상 경우다. 여기서 소리를 내면 경고가 소음이 되고, 소음이 된
        경고는 정작 필요할 때 안 읽힌다."""
        r = self._put(_BODY, "vinc.a.declared")
        self.assertEqual(r["warnings"], [])

    def test_선언이_없으면_조용하다(self):
        self.assertEqual(self._put("# 제목만\n\n본문", "vinc.a.z")["warnings"], [])

    def test_본문은_한_글자도_안_바뀐다(self):
        """text 는 Chunk=SSoT 이고 `verified` 는 그 바이트의 sha256 이다. 프런트매터를
        고쳐 '친절하게' 맞춰 주면 그 검증이 거짓이 된다."""
        self._put(_BODY, "d1234567890abcdef")
        got = docstore.get_doc(self.store, "d1234567890abcdef", owner=self.OWNER)
        self.assertTrue(got["executed"])
        self.assertIs(got["verified"], True)
        self.assertEqual(got["text"], _BODY)


if __name__ == "__main__":
    unittest.main()

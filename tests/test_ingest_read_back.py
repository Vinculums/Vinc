# -*- coding: utf-8 -*-
"""R3 — vinc_ingest 는 개수가 아니라 **저장형**을 돌려준다.

`merged 14 created, 4 matched, warnings 0` 은 참이면서 동시에 필드 하나가 사라진
것을 감췄다(2026-08-24 실측, [[d4ab94561078498e3]] §7.2). 개수는 행이 몇 개
움직였는지만 말한다. 호출부가 같은 턴에 대조하려면 저장형이 응답에 있어야 한다.

여기서 고정하는 것 넷:
  ① 되읽기 열은 `_KEPT_FIELDS` 를 따른다 — 표와 응답이 갈라지면 "안 보이는 필드"의
     뜻이 "그 열이 없다"에서 "되읽기가 빠뜨렸다"로 조용히 바뀐다.
  ② 안 보관하는 필드는 응답에도 없다(경고와 같은 사실을 두 방향에서 말한다).
  ③ 기본은 꺼짐 — sync·팩 경로가 수백 노드의 저장형을 되돌리지 않는다.
  ④ 소유자 술어가 되읽기에도 걸린다."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import fragment as frag  # noqa: E402

_DRIVER = None
try:
    from store.embedded_backend import _import_driver
    _DRIVER, _ = _import_driver()
except Exception:
    pass


class KeptFieldsTableTests(unittest.TestCase):
    """되읽기가 쓰는 표는 경고가 쓰는 표와 **같은 객체**여야 한다."""

    def test_owner_스코프_목록이_한_벌이다(self):
        # `_merge_node` 가 물리 키를 합성하는 기준과 `_stored_nodes` 가 술어를 거는
        # 기준이 갈라지면 되읽기는 0행을 만나고 호출부는 "쓴 것이 없다"로 읽는다.
        self.assertIn("Concept", frag._OWNER_SCOPED_LABELS)
        self.assertNotIn("Domain", frag._OWNER_SCOPED_LABELS)


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class ReadBackE2E(unittest.TestCase):
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

    def _ingest(self, nodes, **kw):
        return frag.merge_fragment({"owner": self.OWNER, "nodes": nodes},
                                   store=self.store, **kw)

    def test_기본은_꺼져_있다(self):
        """sync 복제·팩 가져오기는 수백 노드를 민다. 거기서 저장형 전량을 되돌리면
        응답이 요청보다 커지고 아무도 읽지 않는다."""
        res = self._ingest([{"id": "concept:off", "type": "concept", "title": "t"}])
        self.assertNotIn("stored", res)

    def test_저장형이_돌아온다(self):
        res = self._ingest([
            {"id": "concept:c1", "type": "concept", "title": "제목", "domain": "eng",
             "tags": ["a"], "one_liner": "한 줄.", "props": {"k": "v"},
             "content_time": "2026-08"}], read_back=True)
        self.assertEqual(res["merged"]["nodes"]["created"], 1, res)
        got = {n["id"]: n for n in res["stored"]}
        self.assertIn("concept:c1", got)
        n = got["concept:c1"]
        self.assertEqual(n["kind"], "concept")
        self.assertEqual(n["title"], "제목")
        self.assertEqual(n["tags"], ["a"])
        self.assertEqual(n["one_liner"], "한 줄.")
        self.assertEqual(n["props"], {"k": "v"})       # dict 로 되읽는다(문자열 아님)
        self.assertEqual(n["content_time"], "2026-08")
        # 도메인은 저장 시 정규화된다. **정규화된 형태를 보여 주는 것이 요점이다** —
        # 여태 호출부는 자기가 보낸 문자열이 그대로 들어갔다고 믿을 수밖에 없었다.
        self.assertTrue(n["domain"])

    def test_안_보관하는_필드는_응답에도_없다(self):
        """Decision 에는 content_time·one_liner 열이 없다. 경고가 그렇게 말하고,
        저장형도 같은 사실을 말해야 한다 — 한쪽만 말하면 다른 쪽이 근거가 된다."""
        res = self._ingest([
            {"id": "decision:d1", "type": "decision", "title": "D", "domain": "eng",
             "content_time": "2026-08"}], read_back=True)
        n = next(x for x in res["stored"] if x["id"] == "decision:d1")
        self.assertNotIn("content_time", n)
        self.assertNotIn("one_liner", n)
        self.assertIn("title", n)
        self.assertTrue(any("content_time" in w for w in res["warnings"]), res["warnings"])

    def test_열이_없는_종류가_섞여도_죽지_않는다(self):
        """라벨마다 열이 다르다 — 한 질의로 뭉치면 Binder 가 죽는다. 라벨별로 나눠
        뽑는 이유이자, 이 리포가 UNION 에서 이미 두 번 겪은 자리다."""
        res = self._ingest([
            {"id": "concept:m1", "type": "concept", "title": "C"},
            {"id": "decision:m2", "type": "decision", "title": "D"},
            {"id": "record:m3", "type": "record", "title": "R"},
            {"id": "episode:m4", "type": "episode", "title": "E"},
            {"id": "tool:m5", "type": "tool", "title": "T"}], read_back=True)
        self.assertTrue(res["executed"], res)
        self.assertEqual({n["id"] for n in res["stored"]},
                         {"concept:m1", "decision:m2", "record:m3", "episode:m4", "tool:m5"})

    def test_스킵된_노드는_저장형에_없다(self):
        """`stored` 는 `merged` 와 같은 집합을 말해야 한다 — 안 들어간 것이 목록에
        있으면 되읽기가 개수보다 더 나쁜 거짓말이 된다."""
        res = self._ingest([
            {"id": "concept:ok", "type": "concept", "title": "OK"},
            {"id": "tool:notitle", "type": "tool"}], read_back=True)   # 제목 없는 Entity = 스킵
        self.assertEqual([n["id"] for n in res["stored"]], ["concept:ok"])

    def test_남의_노드는_되읽지_않는다(self):
        self._ingest([{"id": "concept:mine", "type": "concept", "title": "A"}])
        res = frag.merge_fragment(
            {"owner": "user:b", "nodes": [{"id": "concept:mine", "type": "concept",
                                           "title": "B"}]},
            store=self.store, read_back=True)
        for n in res.get("stored", []):
            self.assertNotEqual(n.get("title"), "A")

    def test_예산을_넘으면_밝히고_자른다(self):
        big = "가" * 40_000
        nodes = [{"id": f"concept:b{i}", "type": "concept", "title": "t",
                  "props": {"blob": big}} for i in range(12)]
        res = self._ingest(nodes, read_back=True)
        self.assertEqual(res["merged"]["nodes"]["created"], 12, res)
        self.assertTrue(res.get("stored_truncated"), res.keys())
        self.assertLess(len(res["stored"]), 12)


if __name__ == "__main__":
    unittest.main()

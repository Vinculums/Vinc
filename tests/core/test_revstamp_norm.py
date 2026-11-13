# -*- coding: utf-8 -*-
"""VIN-595 — 쓰기 경로의 변경감지(`revstamp.differs`)가 동기화 병합과 같은 정규화를 쓴다.

예전에는 `revstamp._norm` 이 list 만 tuple 로 바꿨다. 그래서 props 키 순서만 바뀐 dict 나
None 과 '' 의 교체가 "변경"으로 잡혀 rev 가 올랐고, 같은 쓰기를 `syncmerge` 는 "변경 없음"
으로 봤다. rev 가 헛되이 오르면 그 rev 를 들고 있던 클라이언트의 `expected_rev` 가 어긋난다.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import fragment as frag  # noqa: E402
from api import query as q  # noqa: E402
from api import revstamp, syncmerge  # noqa: E402

_DRIVER = None
try:
    from store.embedded_backend import import_driver
    _DRIVER, _ = import_driver()
except Exception:
    pass


class DiffersNormTests(unittest.TestCase):
    def test_정규화는_한_벌이다(self):
        self.assertIs(revstamp._norm, syncmerge._norm)

    def test_props_키_순서만_바뀐_것은_변경이_아니다(self):
        cur = {"props": {"a": 1, "b": [1, 2]}}
        self.assertFalse(revstamp.differs(cur, {"props": {"b": [1, 2], "a": 1}}))

    def test_None_과_빈_문자열은_같다(self):
        self.assertFalse(revstamp.differs({"one_liner": None}, {"one_liner": ""}))
        self.assertFalse(revstamp.differs({}, {"domain": ""}))

    def test_진짜_변경은_변경이다(self):
        self.assertTrue(revstamp.differs({"props": {"a": 1}}, {"props": {"a": 2}}))
        self.assertTrue(revstamp.differs({"one_liner": None}, {"one_liner": "x"}))
        # 태그 순서는 여전히 의미가 있다(STRING[] 순서보존 비교).
        self.assertTrue(revstamp.differs({"tags": ["a", "b"]}, {"tags": ["b", "a"]}))


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class RevBumpE2E(unittest.TestCase):
    OWNER = "user:a"

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._env = {}
        for k, v in {
            "MEMORY_STORE_BACKEND": "embedded",
            "MEMORY_DATA_DIR": os.path.join(self._tmp, "store"),
        }.items():
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

    def _ingest(self, node):
        out = frag.merge_fragment({"owner": self.OWNER, "nodes": [node], "edges": []},
                                  store=self.store)
        self.assertTrue(out.get("merged"), out)

    def _rev(self, cid):
        out = q.node_context(owner=self.OWNER, node=cid, store=self.store)
        self.assertTrue(out["executed"], out)
        return int(out["rev"])

    def test_키_순서만_바꾼_재전송은_rev_를_올리지_않는다(self):
        base = {"id": "concept:k", "type": "concept", "title": "K", "domain": "vinc/d"}
        self._ingest({**base, "props": {"alpha": 1, "beta": "b"}})
        first = self._rev("concept:k")
        self._ingest({**base, "props": {"beta": "b", "alpha": 1}})
        self.assertEqual(self._rev("concept:k"), first)
        self._ingest({**base, "props": {"beta": "b", "alpha": 2}})
        self.assertEqual(self._rev("concept:k"), first + 1)

    def test_비어_있던_열에_빈_문자열을_보내도_rev_가_그대로다(self):
        base = {"id": "concept:n", "type": "concept", "title": "N", "domain": "vinc/d"}
        self._ingest(base)
        first = self._rev("concept:n")
        self._ingest({**base, "one_liner": ""})
        self.assertEqual(self._rev("concept:n"), first)
        self._ingest({**base, "one_liner": "이제 값이 있다."})
        self.assertEqual(self._rev("concept:n"), first + 1)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""scripts/make_queries_from_store.py — vinc.plan.reinforcement-points 우선순위 4
(vinc.ops.mcp-hardening-eval: "실 코퍼스에 속한 평가셋이 없다"). make_queries.py 의
select()/candidates_in_text() 는 test_make_queries.py 가 이미 덮으므로, 여기서는
이 파일에서만 새로 생긴 것 — 스토어 문서 순회(`iter_doc_texts`, 커서 페이지네이션)
만 실엔진으로 검사한다."""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import docstore as ds  # noqa: E402

_SRC = Path(__file__).resolve().parents[2] / "scripts" / "make_queries_from_store.py"

_DRIVER = None
try:
    from store.embedded_backend import import_driver
    _DRIVER, _ = import_driver()
except Exception:
    pass


def _load():
    spec = importlib.util.spec_from_file_location("_vinc_make_queries_from_store", _SRC)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class IterDocTextsE2E(unittest.TestCase):
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
        self.m = _load()

    def tearDown(self):
        self.store.close()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_owner_의_문서를_페이지네이션으로_전부_낸다(self):
        for i in range(5):
            ds.put_doc(self.store, owner=self.OWNER, text=f"# 문서{i}\n\n## 절{i}\n\n본문{i}.\n",
                      doc_id=f"doc:{i}", title=f"문서{i}")
        # 남의 문서는 나오지 않는다(owner 스코프).
        ds.put_doc(self.store, owner="user:b", text="# 남의 문서\n\n## 절\n\n본문.\n",
                  doc_id="doc:other", title="남의 문서")

        got = list(self.m.iter_doc_texts(self.store, self.OWNER, page_size=2))
        self.assertEqual({d for d, _ in got}, {f"doc:{i}" for i in range(5)})
        self.assertEqual(len(got), 5)   # 중복 없이 정확히 5건(페이지 크기 2로 3페이지)

    def test_문서가_없으면_빈_이터레이터다(self):
        self.assertEqual(list(self.m.iter_doc_texts(self.store, "user:empty")), [])


if __name__ == "__main__":
    unittest.main()

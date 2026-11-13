# -*- coding: utf-8 -*-
"""node_context 발췌는 링크 방법이 강한 순(title > stem > source)으로 먼저 고른다.

`source` 는 파일 제공처라 그 파일의 **모든** 청크가 개념에 걸린다. 그래서 문서
순서만으로 창을 자르면 앞 문서 머리말이 창을 다 먹고, 정작 그 노드를 말하는
청크(`title`)가 밀려난다. 이 테스트는 창이 한 칸일 때 무엇이 남는지로 그 순서를
고정한다 — 문서 순서로는 `source` 가 먼저인 배치를 일부러 만든다.
근거: [[record:korean-titled-nodes-answer-with-provenance-excerpts-2026-09-14]]"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import query as q  # noqa: E402
from store import pkns  # noqa: E402

_DRIVER = None
try:
    from store.embedded_backend import import_driver
    _DRIVER, _ = import_driver()
except Exception:
    pass


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class ExcerptRankByLinkMethod(unittest.TestCase):
    OWNER = "user:rank"
    NODE = "concept:ordering-probe"

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._env = {}
        for k, v in {
            "MEMORY_STORE_BACKEND": "embedded",
            "MEMORY_DATA_DIR": os.path.join(self._tmp, "store"),
        }.items():
            self._env[k] = os.environ.get(k)
            os.environ[k] = v
        from store.backend import open_store
        from store.config import load_config
        self.store = open_store(load_config())
        self.store.provision(dim=4)

        pk = pkns.key(self.OWNER, self.NODE)
        self.store.run(
            "MERGE (c:Concept {concept_id: $pk}) SET c.owner_id = $o, "
            "c.title = 'alpha beta', c.one_liner = ''", {"pk": pk, "o": self.OWNER})
        # 같은 문서 안에서 source 가 chunk_index 로 **앞선다** — 옛 정렬이라면 이게 이긴다.
        for cid, idx, method, text in (
            ("ck0", 0, "source", "front matter of the file, says nothing about the node"),
            ("ck1", 1, "title", "alpha beta is what this passage is actually about"),
        ):
            self.store.run(
                "MERGE (ch:Chunk {chunk_id: $cid}) SET ch.owner_id = $o, "
                "ch.doc_id = 'doc1', ch.chunk_index = $idx, ch.section_title = '', "
                "ch.text = $t", {"cid": cid, "o": self.OWNER, "idx": idx, "t": text})
            self.store.run(
                "MATCH (ch:Chunk {chunk_id: $cid}), (c:Concept {concept_id: $pk}) "
                "CREATE (ch)-[:MENTIONS {method: $m}]->(c)",
                {"cid": cid, "pk": pk, "m": method})

    def tearDown(self):
        self.store.close()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_title_발췌가_source_발췌보다_먼저_온다(self):
        out = q.node_context(owner=self.OWNER, node=self.NODE, limit=1,
                             store=self.store)
        ex = out.get("excerpts") or []
        self.assertEqual(len(ex), 1, out)
        # 창이 한 칸이면 정렬이 곧 선택이다 — 옛 정렬(doc_id, chunk_index)은 source 를 준다.
        self.assertEqual(ex[0].get("method"), "title", out)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""문서보다 늦게 들어온 개념·결정도 MENTIONS 로 이어진다(`merge_fragment` → `link_nodes`),
그리고 이미 생긴 빈틈은 기동 작업 한 번이 메운다(`ensure_late_links`)."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import docstore as ds  # noqa: E402
from api import fragment  # noqa: E402
from engines import mentions_link as ml  # noqa: E402
from store import pkns  # noqa: E402
from store.embedded_backend import import_driver  # noqa: E402

_DRIVER, _ = import_driver()
A, B = "user:a", "user:b"
CASH = "Keep a cash buffer of about thirty percent before adding risk."
BOND = "A bond ladder spreads maturities so that cash comes back every year."


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class IngestMentions(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._env = {k: os.environ.get(k) for k in ("MEMORY_STORE_BACKEND", "MEMORY_DATA_DIR")}
        os.environ.update({"MEMORY_STORE_BACKEND": "embedded",
                           "MEMORY_DATA_DIR": os.path.join(self._tmp, "store")})
        from store.backend import open_store
        from store.config import load_config
        self.store = open_store(load_config())
        self.store.provision(dim=4)

    def tearDown(self):
        self.store.close()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _ingest(self, owner, *nodes):
        out = fragment.merge_fragment({"owner": owner, "nodes": list(nodes)}, store=self.store)
        self.assertTrue(out.get("executed"), out)
        return out

    def _links(self, label="Concept", pk="concept_id"):
        rows = self.store.run(
            f"MATCH (ch:Chunk)-[m:MENTIONS]->(c:{label}) RETURN ch.doc_id AS doc, "
            f"c.{pk} AS node, m.method AS method") or []
        return sorted((r["doc"], pkns.strip(r["node"]), r["method"]) for r in rows)

    def test_문서보다_늦게_들어온_개념도_링크된다(self):
        doc = ds.put_doc(self.store, owner=A, title="cash", text=CASH)["doc_id"]
        self.assertEqual(self._links(), [])
        self._ingest(A, {"id": "concept:cash-buffer", "type": "concept", "title": "Cash buffer"})
        self.assertEqual(self._links(), [(doc, "concept:cash-buffer", "title")])

    def test_결정도_링크된다(self):
        doc = ds.put_doc(self.store, owner=A, title="cash", text=CASH)["doc_id"]
        self._ingest(A, {"id": "decision:hold-a-cash-buffer", "type": "decision",
                         "title": "Cash buffer"})
        self.assertEqual(self._links("Decision", "decision_id"),
                         [(doc, "decision:hold-a-cash-buffer", "title")])

    def test_재료가_그대로면_청크를_다시_훑지_않는다(self):
        ds.put_doc(self.store, owner=A, title="cash", text=CASH)
        node = {"id": "concept:cash-buffer", "type": "concept", "title": "Cash buffer"}
        self._ingest(A, node)
        with mock.patch.object(ml, "link_nodes", wraps=ml.link_nodes) as spy:
            self._ingest(A, node)
            self._ingest(A, {"id": "concept:cash-buffer", "type": "concept"})
            spy.assert_not_called()
            self._ingest(A, {"id": "concept:cash-again", "type": "concept", "title": "Cash buffer"})
            self.assertEqual(spy.call_count, 1, "새 노드는 훑는다(가로채기가 걸렸는지도 본다)")
        self.assertEqual(len(self._links()), 2)

    def test_제목이_바뀌면_옛_제목의_링크가_지워진다(self):
        cash = ds.put_doc(self.store, owner=A, title="cash", text=CASH)["doc_id"]
        bond = ds.put_doc(self.store, owner=A, title="bond", text=BOND)["doc_id"]
        self._ingest(A, {"id": "concept:plan", "type": "concept", "title": "Cash buffer"})
        self.assertEqual(self._links(), [(cash, "concept:plan", "title")])
        self._ingest(A, {"id": "concept:plan", "type": "concept", "title": "Bond ladder"})
        self.assertEqual(self._links(), [(bond, "concept:plan", "title")])

    def test_남의_청크에는_잇지_않는다(self):
        ds.put_doc(self.store, owner=B, title="cash", text=CASH)
        mine = ds.put_doc(self.store, owner=A, title="cash", text=CASH)["doc_id"]
        self._ingest(A, {"id": "concept:cash-buffer", "type": "concept", "title": "Cash buffer"})
        self.assertEqual(self._links(), [(mine, "concept:cash-buffer", "title")])

    def test_링크_실패는_쓰기를_무효로_만들지_않는다(self):
        with mock.patch.object(ml, "link_nodes", side_effect=RuntimeError("boom")):
            out = self._ingest(A, {"id": "concept:cash-buffer", "type": "concept",
                                   "title": "Cash buffer"})
        self.assertEqual(out["merged"]["nodes"]["created"], 1)

    def _late_concept(self, owner, nid, title):
        # 링크 단계를 거치지 않은 옛 적재를 흉내 낸다 — 스토어에 직접 넣는다.
        self.store.run("MERGE (c:Concept {concept_id: $k}) SET c.owner_id = $o, c.title = $t",
                       {"k": pkns.key(owner, nid), "o": owner, "t": title})

    def test_기동_작업은_링크가_없는_노드만_한_번_메운다(self):
        cash = ds.put_doc(self.store, owner=A, title="cash", text=CASH)["doc_id"]
        ds.put_doc(self.store, owner=B, title="bond", text=BOND)
        self._late_concept(A, "concept:cash-buffer", "Cash buffer")
        self.assertEqual(self._links(), [])
        first = ml.ensure_late_links(self.store)
        self.assertTrue(first["changed"], first)
        self.assertEqual(first["owners"], {A: 1})
        self.assertEqual(self._links(), [(cash, "concept:cash-buffer", "title")])
        self._late_concept(A, "concept:cash-again", "Cash buffer")
        second = ml.ensure_late_links(self.store)
        self.assertFalse(second["changed"], "표식이 같으면 다시 돌지 않는다")
        self.assertEqual(len(self._links()), 1)

    def test_단일_소유자_스토어는_기동_작업이_링크를_바꾸지_않는다(self):
        ds.put_doc(self.store, owner=A, title="cash", text=CASH)
        self._late_concept(A, "concept:cash-buffer", "Cash buffer")
        out = ml.ensure_late_links(self.store)
        self.assertTrue(out["skipped_single_owner"], out)
        self.assertEqual(self._links(), [])


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""노드 미러 행의 ColBERT 토큰 열. 이 파일이 지키는 것: 토큰 텍스트는 dense 미러보다
넓다 · 바뀐 것만 다시 만든다 · 값만 바꾸고 dense 벡터와 색인은 건드리지 않는다 · 코드북이 다른
인코더의 코드는 싣지 않는다.
근거: [[vinc.plan.vin845-colbert-node-rescoring-implementation-2026-10-30]]"""
from __future__ import annotations

import os
import tempfile
import unittest

from api import node_mirror
# 토큰 채우기(`node_mirror.rebuild_colbert`)는 인코더 이름을 `encoder_client` 에서 부를 때 읽는다.
from api import encoder_client as ec

try:
    import ladybug                          # noqa: F401
    _DRIVER = True
except Exception:                           # pragma: no cover
    _DRIVER = None

DIM = 4


def _vec(*xs):
    v = list(xs) + [0.0] * (DIM - len(xs))
    n = sum(x * x for x in v) ** 0.5 or 1.0
    return [x / n for x in v]


class TokenText(unittest.TestCase):
    def test_props_arrive_as_json_text(self):
        self.assertEqual(node_mirror.token_text("T", None, "One", '{"claim": "C", "n": 2}'), "T\nOne\nC")
        self.assertEqual(node_mirror.token_text("T", "S", "", "not json"), "T\nS")

    def test_todo_skips_rows_missing_from_the_mirror(self):
        rows = [{"id": "a", "owner": "o", "ctext": "x"}, {"id": "b", "owner": "o", "ctext": "y"},
                {"id": "c", "owner": "o", "ctext": "z"}]
        state = {"o#a": (node_mirror.colbert_hash("x"), "B1"), "o#b": (node_mirror.colbert_hash("y"), "B0")}
        self.assertEqual([r["id"] for r in node_mirror.colbert_todo(rows, state, "B1")], ["b"])


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class RealStore(unittest.TestCase):
    OWNER = "user:colbert-e2e"

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._env = {}
        for k, v in {"MEMORY_STORE_BACKEND": "embedded",
                     "MEMORY_DATA_DIR": os.path.join(self._tmp, "store")}.items():
            self._env[k] = os.environ.get(k)
            os.environ[k] = v
        from api import fragment as frag
        from store.backend import open_store
        from store.config import load_config
        self.store = open_store(load_config())
        self.store.provision(dim=DIM)
        frag.merge_fragment({"owner": self.OWNER, "nodes": [
            {"id": "concept:alpha", "type": "concept", "title": "Alpha", "one_liner": "the first letter",
             "props": {"note": "kept in the token text"}},
            {"id": "episode:bravo", "type": "episode", "title": "Bravo", "date": "2026-10-30",
             "summary": "a long summary the dense mirror clips"},
        ]}, store=self.store)
        rows, _ = node_mirror.collect(self.store, self.OWNER)
        for r in rows:
            r["emb"] = _vec(1, 0) if r["id"] == "concept:alpha" else _vec(0, 1)
        node_mirror.replace(self.store, rows, "test")

    def tearDown(self):
        self.store.close()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_tokens_fill_once_and_leave_the_dense_mirror_alone(self):
        rows, _ = node_mirror.collect(self.store, self.OWNER, with_tokens=True)
        by = {r["id"]: r["ctext"] for r in rows}
        self.assertEqual(by["concept:alpha"], "Alpha\nthe first letter\nkept in the token text")
        self.assertEqual(by["episode:bravo"], "Bravo\na long summary the dense mirror clips")
        todo = node_mirror.colbert_todo(rows, node_mirror.colbert_state(self.store, self.OWNER), "B1")
        self.assertEqual(len(todo), 2)
        node_mirror.set_colbert(self.store, [{"vid": node_mirror.vec_id(self.OWNER, r["id"]), "codes": "AAAA",
                                              "hash": node_mirror.colbert_hash(r["ctext"])} for r in todo], "B1")
        self.assertEqual(node_mirror.colbert_todo(rows, node_mirror.colbert_state(self.store, self.OWNER), "B1"), [])
        self.assertEqual(len(node_mirror.colbert_todo(rows, node_mirror.colbert_state(self.store, self.OWNER), "B2")), 2)
        hits = self.store.node_vector_search(_vec(1, 0), 5, owner=self.OWNER)
        self.assertEqual(hits[0]["id"], "concept:alpha")       # dense 색인은 그대로 답한다

    def test_route_refuses_codes_from_another_codebook(self):
        from api.server import VincApp
        app = VincApp(store=self.store)
        cb = type("CB", (), {"id": "B1"})()
        real_cb, real_call = ec._colbert, ec._encode_call
        os.environ["VINC_EMBED_ENCODE"] = "http://encoder.invalid"
        try:
            ec._colbert = lambda: cb
            ec._encode_call = lambda url, texts, kind, timeout, colbert=False: {
                "colbert": ["AAAA"] * len(texts), "colbert_books": "B9"}
            res = app._node_mirror_colbert({"owner": self.OWNER})
            self.assertEqual((res["done"], res["executed"]), (0, False))
            self.assertIn("B9", res["error"])
            ec._encode_call = lambda url, texts, kind, timeout, colbert=False: {
                "colbert": ["AAAA"] * len(texts), "colbert_books": "B1"}
            res = app._node_mirror_colbert({"owner": self.OWNER})
            self.assertEqual((res["done"], res["rows"]), (2, 2))
            self.assertEqual(app._node_mirror_colbert({"owner": self.OWNER})["rows"], 0)
        finally:
            ec._colbert, ec._encode_call = real_cb, real_call
            os.environ.pop("VINC_EMBED_ENCODE", None)
            app.jobs.stop() if hasattr(app.jobs, "stop") else None

    def test_one_rebuild_fills_at_most_the_cap_and_the_next_one_continues(self):
        from api.server import VincApp
        app = VincApp(store=self.store)
        cb = type("CB", (), {"id": "B1"})()
        real = ec._colbert, ec._encode_call, node_mirror._COLBERT_FILL_MAX
        os.environ["VINC_EMBED_ENCODE"] = "http://encoder.invalid"
        try:
            ec._colbert = lambda: cb
            ec._encode_call = lambda url, texts, kind, timeout, colbert=False: {
                "colbert": ["AAAA"] * len(texts), "colbert_books": "B1"}
            node_mirror._COLBERT_FILL_MAX = 1
            first = app._node_mirror_colbert({"owner": self.OWNER})
            self.assertEqual((first["done"], first["left"]), (1, 1))
            second = app._node_mirror_colbert({"owner": self.OWNER})
            self.assertEqual((second["done"], second["left"]), (1, 0))
            self.assertEqual(app._node_mirror_colbert({"owner": self.OWNER})["rows"], 0)
        finally:
            ec._colbert, ec._encode_call, node_mirror._COLBERT_FILL_MAX = real
            os.environ.pop("VINC_EMBED_ENCODE", None)
            app.jobs.stop() if hasattr(app.jobs, "stop") else None

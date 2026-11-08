# -*- coding: utf-8 -*-
"""표준 openCypher → ladybug 철자 번역(`store.embedded_backend.to_engine_cypher`).

질의는 표준 `type(r)` 으로 쓰고 임베디드 어댑터가 `label(r)` 로 바꿔 보낸다. 잠그는 것:
함수 호출만 바꾸고 `r.type` 같은 속성·`$type` 같은 매개변수·별칭은 건드리지 않는다, 그리고
실엔진에서 표준 질의가 관계 타입을 돌려준다.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from store.embedded_backend import _import_driver, to_engine_cypher  # noqa: E402

_DRIVER, _NAME = _import_driver()


class TranslateTests(unittest.TestCase):
    def test_관계_타입_함수만_바꾼다(self):
        self.assertEqual(to_engine_cypher("MATCH ()-[r]->() RETURN type(r) AS rel"),
                         "MATCH ()-[r]->() RETURN label(r) AS rel")
        self.assertEqual(to_engine_cypher("ORDER BY type( e ), x"), "ORDER BY label( e ), x")

    def test_속성_매개변수_별칭은_그대로다(self):
        q = ("MATCH (a)-[r:ATTACHES_TO]->(b) WHERE r.type = $type "
             "RETURN r.type AS type, a.type AS ftype")
        self.assertEqual(to_engine_cypher(q), q)

    def test_type_이_없으면_같은_문자열이다(self):
        q = "MATCH (n:Concept) RETURN n.title"
        self.assertIs(to_engine_cypher(q), q)


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class EngineTests(unittest.TestCase):
    def test_표준_질의가_실엔진에서_관계_타입을_돌려준다(self):
        env = {"MEMORY_STORE_BACKEND": "embedded",
               "MEMORY_DATA_DIR": os.path.join(tempfile.mkdtemp(), "store"),
               "MEMORY_EMBEDDING_MODEL": "local-en"}
        saved = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            from store.backend import open_store
            from store.config import load_config
            cfg = load_config()
            store = open_store(cfg)
            store.provision(dim=cfg.embedding_dim)
            store.run("CREATE (:Concept {concept_id:'a', owner_id:'u', title:'a'}), "
                      "(:Concept {concept_id:'b', owner_id:'u', title:'b'})")
            store.run("MATCH (a:Concept {concept_id:'a'}), (b:Concept {concept_id:'b'}) "
                      "CREATE (a)-[:RELATED_TO]->(b)")
            rows = store.run("MATCH (:Concept)-[r]->(:Concept) RETURN type(r) AS rel")
            with store.session() as s:
                via_session = s.run("MATCH (:Concept)-[r]->(:Concept) RETURN type(r) AS rel").single()
            store.close()
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.assertEqual([r["rel"] for r in rows], ["RELATED_TO"])
        self.assertEqual(via_session["rel"], "RELATED_TO")


if __name__ == "__main__":
    unittest.main()

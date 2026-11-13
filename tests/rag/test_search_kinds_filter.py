# -*- coding: utf-8 -*-
"""`kinds` 는 노드 팔 셋(제목·의미·그래프)을 **자르기 전에** 거른다.

픽스처: 질문 토큰 셋을 다 맞추는 개념 셋과, 한 토큰만 맞추는 결정 하나. 상한 2로 자르면 결정은
어느 팔에서도 잘려 나간다. `kinds=["decision"]` 이면 그 결정이 남아야 한다. 뒤에 거르면 빈 목록이다.
인자가 없으면 오늘과 한 바이트도 다르지 않아야 한다.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from api import fragment as frag
from api import query as q
from store import embedded_backend as eb
from vinc_rag import retrieval as R

_DRIVER, _NAME = eb.import_driver()

OWNER = "user:kinds"
QUERY = "alpha beta gamma"
NEAR, FAR = [1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]
CONCEPTS = ["concept:kf-one", "concept:kf-two", "concept:kf-three"]
DECISION = "decision:kf-pick"


class ParseKinds(unittest.TestCase):
    def test_empty_means_no_filter(self):
        for v in (None, [], "", " , "):
            self.assertIsNone(R.parse_kinds(v))

    def test_list_or_comma_string_in_fixed_order(self):
        self.assertEqual(R.parse_kinds(["episode", "Decision"]), ("decision", "episode"))
        self.assertEqual(R.parse_kinds("record,concept"), ("concept", "record"))

    def test_unknown_kind_is_refused(self):
        with self.assertRaises(ValueError):
            R.parse_kinds(["decision", "bridge"])

    def test_cursor_fingerprint_moves_only_with_kinds(self):
        a = R._query_fingerprint("x", "o", None, "summary")
        self.assertEqual(a, R._query_fingerprint("x", "o", None, "summary", kinds=None))
        self.assertNotEqual(a, R._query_fingerprint("x", "o", None, "summary", kinds=("decision",)))


class TitleArmCypher(unittest.TestCase):
    class _Store:
        def __init__(self):
            self.cypher = []

        def run(self, cypher, params=None):
            self.cypher.append(cypher)
            return []

    def test_no_kinds_scans_all_four_tables(self):
        st = self._Store()
        R._node_fallback_search(st, QUERY, owner=OWNER)
        c = st.cypher[0]
        self.assertEqual(c.count("UNION ALL"), 3)
        for label in ("Concept", "Record", "Decision", "Episode"):
            self.assertIn(f"MATCH ({label[0].lower()}:{label})", c)

    def test_kinds_drop_the_other_tables_from_the_scan(self):
        st = self._Store()
        R._node_fallback_search(st, QUERY, owner=OWNER, kinds=("decision", "episode"))
        c = st.cypher[0]
        self.assertEqual(c.count("UNION ALL"), 1)
        self.assertIn("(d:Decision)", c)
        self.assertIn("(e:Episode)", c)
        self.assertNotIn("(c:Concept)", c)
        self.assertNotIn("(r:Record)", c)


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class KindsBeforeTheCut(unittest.TestCase):
    def setUp(self):
        self._env = {}
        for k, v in {"MEMORY_STORE_BACKEND": "embedded", "VINC_KEEP_VECTOR_INDEX": "off",
                     "MEMORY_DATA_DIR": os.path.join(tempfile.mkdtemp(), "store")}.items():
            self._env[k] = os.environ.get(k)
            os.environ[k] = v
        from api import node_mirror
        from store.backend import open_store
        from store.config import load_config
        self.store = open_store(load_config())
        self.store.provision(dim=4)
        frag.merge_fragment({"owner": OWNER, "nodes": [
            {"id": CONCEPTS[0], "type": "concept", "title": "Alpha beta gamma one"},
            {"id": CONCEPTS[1], "type": "concept", "title": "Alpha beta gamma two"},
            {"id": CONCEPTS[2], "type": "concept", "title": "Alpha beta gamma three"},
            {"id": DECISION, "type": "decision", "title": "Alpha choice"},
        ], "edges": [{"from": DECISION, "to": CONCEPTS[0], "type": "about",
                      "evidence": "the pick is about one"}]}, store=self.store)
        rows, _ = node_mirror.collect(self.store, OWNER)
        for r in rows:
            r["emb"] = FAR if r["kind"] == "decision" else NEAR
        self.assertTrue(node_mirror.replace(self.store, rows, "fixture")["index_ok"])

    def tearDown(self):
        self.store.close()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _ids(self, res):
        return [h["id"] for h in res.get("node_hits") or []]

    # ── 팔마다 ──────────────────────────────────────────────────────────────
    def test_title_arm_keeps_the_wanted_kind_past_the_limit(self):
        plain = [h["id"] for h in R._node_fallback_search(self.store, QUERY, limit=2, owner=OWNER)]
        self.assertNotIn(DECISION, plain)
        got = R._node_fallback_search(self.store, QUERY, limit=2, owner=OWNER, kinds=("decision",))
        self.assertEqual([h["id"] for h in got], [DECISION])

    def test_semantic_arm_filters_inside_the_store_query(self):
        plain = [h["id"] for h in self.store.node_vector_search(NEAR, 2, owner=OWNER)]
        self.assertNotIn(DECISION, plain)
        got = self.store.node_vector_search(NEAR, 2, owner=OWNER, kinds=("decision",))
        self.assertEqual([h["id"] for h in got], [DECISION])
        reader = self.store.reader_view()
        try:
            self.assertEqual([h["id"] for h in reader.node_vector_search(
                NEAR, 2, owner=OWNER, kinds=("decision",))], [DECISION])
        finally:
            reader.close()

    def test_semantic_arm_index_path_filters_too(self):
        with mock.patch.object(eb, "_NODE_VEC_EXACT_MAX_ROWS", 1):
            got = self.store.node_vector_search(NEAR, 1, owner=OWNER, kinds=("decision",))
        self.assertEqual([h["id"] for h in got], [DECISION])

    def test_graph_arm_is_skipped_unless_concepts_are_wanted(self):
        hit = {"id": CONCEPTS[2], "title": "x", "kind": "concept", "domain": "",
               "matched_in": "graph", "hit_tokens": 0, "title_tokens": 0}
        with mock.patch.object(R, "_graph_arm_search", return_value=[hit]) as arm:
            res = q.search(QUERY, top_k=2, owner=OWNER, store=self.store, kinds=["decision"])
            self.assertEqual(arm.call_count, 0)
            self.assertNotIn(CONCEPTS[2], self._ids(res))
            q.search(QUERY, top_k=2, owner=OWNER, store=self.store, kinds=["concept"])
            self.assertEqual(arm.call_count, 1)

    # ── 검색 전체 ───────────────────────────────────────────────────────────
    def test_search_lists_the_wanted_kind_that_other_kinds_starve(self):
        plain = q.search(QUERY, top_k=2, owner=OWNER, store=self.store, query_embedding=NEAR)
        self.assertNotIn(DECISION, self._ids(plain))
        got = q.search(QUERY, top_k=2, owner=OWNER, store=self.store, query_embedding=NEAR,
                       kinds=["decision"])
        self.assertEqual(self._ids(got), [DECISION])
        self.assertEqual(got["node_window"]["kinds"], ["decision"])
        self.assertIn("semantic", got["node_hits"][0].get("arms") or [got["node_hits"][0]["matched_in"]])

    def test_no_kinds_is_todays_search_exactly(self):
        plain = q.search(QUERY, top_k=2, owner=OWNER, store=self.store, query_embedding=NEAR)
        for empty in (None, []):
            self.assertEqual(q.search(QUERY, top_k=2, owner=OWNER, store=self.store,
                                      query_embedding=NEAR, kinds=empty), plain)
        self.assertNotIn("kinds", plain["node_window"])

    def test_unknown_kind_is_an_error_not_an_empty_list(self):
        res = q.search(QUERY, owner=OWNER, store=self.store, kinds=["bridge"])
        self.assertTrue(res["error"].startswith("invalid_kinds"), res.get("error"))
        self.assertEqual(res["node_hits"], [])

    # ── brief ──────────────────────────────────────────────────────────────
    def test_brief_resolves_and_offers_only_the_wanted_kind(self):
        plain = q.brief(QUERY, owner=OWNER, store=self.store, query_embedding=NEAR)
        self.assertNotEqual((plain.get("node") or {}).get("id"), DECISION)
        got = q.brief(QUERY, owner=OWNER, store=self.store, query_embedding=NEAR,
                      kinds=["decision"])
        offered = [(got.get("node") or {}).get("id")] + [c.get("id") for c in got["candidates"]]
        self.assertIn(DECISION, offered)
        self.assertTrue(all(i is None or i.startswith("decision:") for i in offered), offered)

    def test_brief_does_not_open_a_named_id_of_another_kind(self):
        got = q.brief(f"{CONCEPTS[0]} 은 무엇인가", owner=OWNER, store=self.store,
                      kinds=["decision"])
        self.assertEqual((got["status"], got.get("kinds_filtered")), ("none", 1))
        same = q.brief(f"{DECISION} 은 무엇인가", owner=OWNER, store=self.store,
                       kinds=["decision"])
        self.assertEqual((same["status"], same.get("resolved_via")), ("resolved", "id"))

    def test_brief_refuses_an_unknown_kind(self):
        got = q.brief(QUERY, owner=OWNER, store=self.store, kinds=["note"])
        self.assertTrue(got["error"].startswith("invalid_kinds"))

    def test_sidecar_passes_kinds_and_answers_400_to_a_bad_one(self):
        from api.auth import SessionAuth
        from api.server import VincApp
        app = VincApp(store=self.store, auth=SessionAuth("t"))
        try:
            with mock.patch.object(app, "_query_embedding", return_value=(None, "")):
                code, res = app.read_search({"query": QUERY, "owner": OWNER, "top_k": "2",
                                             "kinds": "decision"})
                self.assertEqual((code, self._ids(res)), (200, [DECISION]))
                code, res = app.read_search({"query": QUERY, "owner": OWNER, "kinds": "bridge"})
                self.assertEqual(code, 400)
                code, res = app.read_brief({"query": QUERY, "owner": OWNER, "kinds": "bridge"})
                self.assertEqual(code, 400)
        finally:
            app.jobs.shutdown(drain=True)


if __name__ == "__main__":
    unittest.main()

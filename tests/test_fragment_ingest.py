# -*- coding: utf-8 -*-
"""engines/fragment_ingest.py 순수 단위 테스트 — DB 불요.

가짜 세션이 MERGE 를 최소한으로 흉내내(라벨+id 컬럼 별 존재 추적) created/matched
집계, 미지원 type/필드 warnings, 엣지 (from,to) 타입쌍 검증을 실 Kùzu 없이 검증한다.
실제 임베디드 왕복은 test_embedded_backend.py(e2e) 소관.
"""
from __future__ import annotations

import contextlib
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engines import fragment_ingest as fi  # noqa: E402

_MATCH_EXIST_RE = re.compile(r"^MATCH \(n:(\w+) \{(\w+): \$id\}\) RETURN")
_MERGE_NODE_RE = re.compile(r"^MERGE \(n:(\w+) \{(\w+): \$id\}\)")
_MERGE_EDGE_RE = re.compile(
    r"^MATCH \(a:(\w+) \{(\w+): \$from_id\}\), \(b:(\w+) \{(\w+): \$to_id\}\) "
    r"MERGE \(a\)-\[:(\w+)\]->\(b\)$")


class _Result:
    def __init__(self, records):
        self._records = records

    def single(self):
        return self._records[0] if self._records else None


class _FakeSession:
    """MERGE 를 흉내내는 가짜 세션 — (label, id_col, id) 존재를 추적."""

    def __init__(self):
        self._nodes: set[tuple[str, str, str]] = set()
        self.node_writes: list[dict] = []
        self.edge_writes: list[dict] = []

    def run(self, cypher, **params):
        m = _MATCH_EXIST_RE.match(cypher)
        if m:
            label, id_col = m.groups()
            key = (label, id_col, params["id"])
            return _Result([{"id": params["id"]}] if key in self._nodes else [])
        m = _MERGE_NODE_RE.match(cypher)
        if m:
            label, id_col = m.groups()
            self._nodes.add((label, id_col, params["id"]))
            self.node_writes.append(dict(params))
            return _Result([])
        m = _MERGE_EDGE_RE.match(cypher)
        if m:
            self.edge_writes.append(dict(params))
            return _Result([])
        raise AssertionError(f"unexpected cypher: {cypher!r}")


class _FakeStore:
    def __init__(self, session):
        self._session = session

    @contextlib.contextmanager
    def session(self):
        yield self._session


class NodeMergeTests(unittest.TestCase):
    def test_concept_created_then_matched(self):
        s = _FakeSession()
        node = {"id": "concept:margiela", "type": "concept", "title": "Maison Margiela"}
        r1 = fi.merge_fragment(s, {"nodes": [node]})
        self.assertEqual(r1["merged"]["nodes"], {"created": 1, "matched": 0})
        r2 = fi.merge_fragment(s, {"nodes": [node]})
        self.assertEqual(r2["merged"]["nodes"], {"created": 0, "matched": 1})

    def test_unsupported_node_type_record_skipped_with_warning(self):
        s = _FakeSession()
        res = fi.merge_fragment(s, {"nodes": [{"id": "r1", "type": "record", "title": "T"}]})
        self.assertEqual(res["merged"]["nodes"], {"created": 0, "matched": 0})
        self.assertTrue(any("record" in w for w in res["warnings"]))

    def test_missing_title_skipped_with_warning(self):
        s = _FakeSession()
        res = fi.merge_fragment(s, {"nodes": [{"id": "c1", "type": "concept"}]})
        self.assertEqual(res["merged"]["nodes"]["created"], 0)
        self.assertTrue(any("title" in w for w in res["warnings"]))

    def test_domain_node_only_id_extra_fields_warn_but_node_still_merges(self):
        s = _FakeSession()
        res = fi.merge_fragment(s, {"nodes": [
            {"id": "fashion", "type": "domain", "title": "Fashion",
             "domain": "x", "tags": ["a"]}]})
        self.assertEqual(res["merged"]["nodes"], {"created": 1, "matched": 0})
        self.assertTrue(any("node.domain not supported" in w for w in res["warnings"]))
        self.assertTrue(any("node.tags not supported" in w for w in res["warnings"]))
        # Domain 은 name 컬럼만 세팅 — title 은 저장되지 않음(스키마에 없음).
        self.assertNotIn("title", s.node_writes[0])

    def test_props_always_warns_but_does_not_block_merge(self):
        s = _FakeSession()
        res = fi.merge_fragment(s, {"nodes": [
            {"id": "c1", "type": "concept", "title": "T", "props": {"note": "x"}}]})
        self.assertEqual(res["merged"]["nodes"]["created"], 1)
        self.assertTrue(any("props not persisted" in w for w in res["warnings"]))

    def test_owner_stamping_default_and_explicit(self):
        s = _FakeSession()
        fi.merge_fragment(s, {"nodes": [{"id": "c1", "type": "concept", "title": "T"}]})
        self.assertEqual(s.node_writes[0]["owner"], "tenant:local")

        s2 = _FakeSession()
        fi.merge_fragment(s2, {"nodes": [{"id": "c2", "type": "concept", "title": "T"}],
                               "owner": "user:me"})
        self.assertEqual(s2.node_writes[0]["owner"], "user:me")

    def test_bridge_node_has_no_owner_column(self):
        s = _FakeSession()
        fi.merge_fragment(s, {"nodes": [{"id": "b1", "type": "bridge", "title": "T"}]})
        self.assertNotIn("owner", s.node_writes[0])


class EdgeMergeTests(unittest.TestCase):
    def _concepts(self, *ids):
        return [{"id": i, "type": "concept", "title": i} for i in ids]

    def test_relates_to_between_concepts_merges(self):
        s = _FakeSession()
        res = fi.merge_fragment(s, {
            "nodes": self._concepts("concept:a", "concept:b"),
            "edges": [{"from": "concept:a", "to": "concept:b", "type": "relates_to"}],
        })
        self.assertEqual(res["merged"]["edges"], {"created": 1})
        self.assertEqual(s.edge_writes[0], {"from_id": "concept:a", "to_id": "concept:b"})

    def test_edge_endpoint_not_in_fragment_warns(self):
        s = _FakeSession()
        res = fi.merge_fragment(s, {
            "nodes": self._concepts("concept:a"),
            "edges": [{"from": "concept:a", "to": "concept:ghost", "type": "relates_to"}],
        })
        self.assertEqual(res["merged"]["edges"]["created"], 0)
        self.assertTrue(any("not declared in fragment.nodes" in w for w in res["warnings"]))

    def test_edge_type_unsupported_node_pair_warns(self):
        s = _FakeSession()
        res = fi.merge_fragment(s, {
            "nodes": [{"id": "concept:a", "type": "concept", "title": "A"},
                      {"id": "ep:1", "type": "episode", "title": "E"}],
            "edges": [{"from": "concept:a", "to": "ep:1", "type": "relates_to"}],
        })
        self.assertEqual(res["merged"]["edges"]["created"], 0)
        self.assertTrue(any("unsupported for node-type pair" in w for w in res["warnings"]))

    def test_inherits_from_always_unsupported(self):
        s = _FakeSession()
        res = fi.merge_fragment(s, {
            "nodes": self._concepts("concept:a", "concept:b"),
            "edges": [{"from": "concept:a", "to": "concept:b", "type": "inherits_from"}],
        })
        self.assertEqual(res["merged"]["edges"]["created"], 0)
        self.assertTrue(any("edge.type not supported" in w for w in res["warnings"]))

    def test_connects_via_bridge_to_concept(self):
        s = _FakeSession()
        res = fi.merge_fragment(s, {
            "nodes": [{"id": "bridge:1", "type": "bridge", "title": "B"},
                      {"id": "concept:a", "type": "concept", "title": "A"}],
            "edges": [{"from": "bridge:1", "to": "concept:a", "type": "connects_via"}],
        })
        self.assertEqual(res["merged"]["edges"], {"created": 1})


class IngestFragmentEntryPointTests(unittest.TestCase):
    def test_store_none_degrades(self):
        res = fi.ingest_fragment(None, {"nodes": []})
        self.assertFalse(res["executed"])
        self.assertEqual(res["warnings"], ["no reachable store"])

    def test_store_present_executes_via_session(self):
        s = _FakeSession()
        res = fi.ingest_fragment(_FakeStore(s), {
            "nodes": [{"id": "concept:a", "type": "concept", "title": "A"}]})
        self.assertTrue(res["executed"])
        self.assertEqual(res["merged"]["nodes"], {"created": 1, "matched": 0})


if __name__ == "__main__":
    unittest.main()

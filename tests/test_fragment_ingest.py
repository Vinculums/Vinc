# -*- coding: utf-8 -*-
"""engines/fragment_ingest.py 순수 단위 테스트 — DB 불요.

가짜 세션이 MERGE 를 최소한으로 흉내내(라벨+id 컬럼 별 존재 추적) created/matched
집계, 어휘 밖 타입의 proposed 편입(스킵 아님), 레거시 전용 테이블 vs 범용 Entity/
TYPED 라우팅을 실 Kùzu 없이 검증한다. 실제 임베디드 왕복은 test_embedded_backend.py.
"""
from __future__ import annotations

import contextlib
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engines import fragment_ingest as fi  # noqa: E402
from store.theme_store import ThemeStore  # noqa: E402

_MATCH_EXIST_RE = re.compile(r"^MATCH \(n:(\w+) \{(\w+): \$id\}\) RETURN")
_MERGE_NODE_RE = re.compile(r"^MERGE \(n:(\w+) \{(\w+): \$id\}\)")
_MERGE_EDGE_RE = re.compile(
    r"^MATCH \(a:(\w+) \{(\w+): \$from_id\}\), \(b:(\w+) \{(\w+): \$to_id\}\) "
    r"MERGE \(a\)-\[:(\w+)\]->\(b\)$")
_MERGE_TYPED_RE = re.compile(
    r"^MATCH \(a:Entity \{entity_id: \$from_id\}\), \(b:Entity \{entity_id: \$to_id\}\) "
    r"MERGE \(a\)-\[:TYPED \{type: \$type\}\]->\(b\)$")


class _Result:
    def __init__(self, records):
        self._records = records

    def single(self):
        return self._records[0] if self._records else None


class _FakeSession:
    """MERGE 를 흉내내는 가짜 세션 — (label, id_col, id) 존재를 추적. Entity/TYPED 지원."""

    def __init__(self):
        self._nodes: set[tuple[str, str, str]] = set()
        self.node_writes: list[dict] = []
        self.node_labels: list[str] = []
        self.edge_writes: list[dict] = []
        self.typed_writes: list[dict] = []

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
            self.node_labels.append(label)
            return _Result([])
        m = _MERGE_TYPED_RE.match(cypher)
        if m:
            self.typed_writes.append(dict(params))
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


def _themes(theme_id="t1"):
    ts = ThemeStore(tempfile.mkdtemp())
    ts.create_theme(theme_id, theme_id)
    return ts


class NodeMergeTests(unittest.TestCase):
    def test_concept_created_then_matched(self):
        s = _FakeSession()
        node = {"id": "concept:margiela", "type": "concept", "title": "Maison Margiela"}
        r1 = fi.merge_fragment(s, {"nodes": [node]})
        self.assertEqual(r1["merged"]["nodes"], {"created": 1, "matched": 0})
        r2 = fi.merge_fragment(s, {"nodes": [node]})
        self.assertEqual(r2["merged"]["nodes"], {"created": 0, "matched": 1})

    def test_legacy_type_routes_to_dedicated_table(self):
        s = _FakeSession()
        fi.merge_fragment(s, {"nodes": [{"id": "c1", "type": "concept", "title": "T"}]})
        self.assertEqual(s.node_labels, ["Concept"])

    def test_unknown_type_routes_to_entity_not_skipped(self):
        s = _FakeSession()
        res = fi.merge_fragment(s, {"nodes": [{"id": "x1", "type": "해체주의", "title": "T"}]})
        self.assertEqual(res["merged"]["nodes"], {"created": 1, "matched": 0})
        self.assertEqual(res["skipped"], 0)
        self.assertEqual(s.node_labels, ["Entity"])
        # Entity 는 type/theme_id 를 각인
        self.assertEqual(s.node_writes[0]["type"], "해체주의")

    def test_entity_stamps_theme_id(self):
        s = _FakeSession()
        fi.merge_fragment(s, {"nodes": [{"id": "x1", "type": "novel", "title": "T"}]},
                          theme_id="fashion", themes=_themes("fashion"))
        self.assertEqual(s.node_writes[0]["theme"], "fashion")

    def test_missing_title_skipped_with_warning(self):
        s = _FakeSession()
        res = fi.merge_fragment(s, {"nodes": [{"id": "c1", "type": "concept"}]})
        self.assertEqual(res["merged"]["nodes"]["created"], 0)
        self.assertTrue(any("title" in w for w in res["warnings"]))

    def test_missing_id_skipped_with_warning(self):
        s = _FakeSession()
        res = fi.merge_fragment(s, {"nodes": [{"type": "concept", "title": "T"}]})
        self.assertEqual(res["merged"]["nodes"]["created"], 0)
        self.assertTrue(any("id" in w for w in res["warnings"]))

    def test_domain_node_only_id_extra_fields_warn_but_node_still_merges(self):
        s = _FakeSession()
        res = fi.merge_fragment(s, {"nodes": [
            {"id": "fashion", "type": "domain", "title": "Fashion",
             "domain": "x", "tags": ["a"]}]})
        self.assertEqual(res["merged"]["nodes"], {"created": 1, "matched": 0})
        self.assertTrue(any("node.domain not supported" in w for w in res["warnings"]))
        self.assertNotIn("title", s.node_writes[0])

    def test_owner_stamping_default_and_explicit(self):
        s = _FakeSession()
        fi.merge_fragment(s, {"nodes": [{"id": "c1", "type": "concept", "title": "T"}]})
        self.assertEqual(s.node_writes[0]["owner"], "tenant:local")
        s2 = _FakeSession()
        fi.merge_fragment(s2, {"nodes": [{"id": "c2", "type": "concept", "title": "T"}],
                               "owner": "user:me"})
        self.assertEqual(s2.node_writes[0]["owner"], "user:me")


class ThemeFoldInTests(unittest.TestCase):
    def test_out_of_vocab_folds_to_proposed_with_zero_skip(self):
        ts = _themes("fashion")
        s = _FakeSession()
        res = fi.merge_fragment(s, {"nodes": [
            {"id": "x1", "type": "해체주의", "title": "A"},
            {"id": "x2", "type": "해체주의", "title": "B"}],
            "edges": [{"from": "x1", "to": "x2", "type": "영향줌"}]},
            theme_id="fashion", themes=ts)
        self.assertEqual(res["skipped"], 0)
        names = {(p["kind"], p["name"]) for p in res["proposed_types"]}
        self.assertIn(("node", "해체주의"), names)
        self.assertIn(("edge", "영향줌"), names)
        # 어휘에 실제로 편입됨 (proposed / ingest_proposed)
        t = ts.resolve_type("fashion", "node", "해체주의")
        self.assertEqual((t["status"], t["provenance"]), ("proposed", "ingest_proposed"))

    def test_fold_in_is_idempotent_across_calls(self):
        ts = _themes("t")
        s = _FakeSession()
        fi.merge_fragment(s, {"nodes": [{"id": "x1", "type": "novel", "title": "A"}]},
                          theme_id="t", themes=ts)
        res2 = fi.merge_fragment(s, {"nodes": [{"id": "x2", "type": "novel", "title": "B"}]},
                                 theme_id="t", themes=ts)
        # 두 번째 호출엔 이미 어휘에 있으므로 proposed_types 로 다시 안 올라온다
        self.assertEqual(res2["proposed_types"], [])

    def test_in_vocab_type_not_reported_as_proposed(self):
        ts = _themes("t")  # concept 는 시드된 어휘에 있음
        s = _FakeSession()
        res = fi.merge_fragment(s, {"nodes": [{"id": "c1", "type": "concept", "title": "T"}]},
                                theme_id="t", themes=ts)
        self.assertEqual(res["proposed_types"], [])


class EdgeMergeTests(unittest.TestCase):
    def _concepts(self, *ids):
        return [{"id": i, "type": "concept", "title": i} for i in ids]

    def test_relates_to_between_concepts_merges_dedicated(self):
        s = _FakeSession()
        res = fi.merge_fragment(s, {
            "nodes": self._concepts("concept:a", "concept:b"),
            "edges": [{"from": "concept:a", "to": "concept:b", "type": "relates_to"}]})
        self.assertEqual(res["merged"]["edges"], {"created": 1})
        self.assertEqual(s.edge_writes[0], {"from_id": "concept:a", "to_id": "concept:b"})

    def test_novel_edge_between_entities_merges_typed(self):
        s = _FakeSession()
        res = fi.merge_fragment(s, {
            "nodes": [{"id": "x1", "type": "T", "title": "A"},
                      {"id": "x2", "type": "T", "title": "B"}],
            "edges": [{"from": "x1", "to": "x2", "type": "영향줌"}]})
        self.assertEqual(res["merged"]["edges"], {"created": 1})
        self.assertEqual(s.typed_writes[0], {"from_id": "x1", "to_id": "x2", "type": "영향줌"})

    def test_typed_edge_exact_match_idempotent(self):
        s = _FakeSession()
        frag = {"nodes": [{"id": "x1", "type": "T", "title": "A"},
                          {"id": "x2", "type": "T", "title": "B"}],
                "edges": [{"from": "x1", "to": "x2", "type": "rel"}]}
        fi.merge_fragment(s, frag)
        fi.merge_fragment(s, frag)
        self.assertEqual(len(s.typed_writes), 2)  # 두 번 MERGE 호출(멱등 — 그래프상 1개)
        self.assertEqual(s.typed_writes[0], s.typed_writes[1])

    def test_edge_endpoint_not_in_fragment_warns(self):
        s = _FakeSession()
        res = fi.merge_fragment(s, {
            "nodes": self._concepts("concept:a"),
            "edges": [{"from": "concept:a", "to": "concept:ghost", "type": "relates_to"}]})
        self.assertEqual(res["merged"]["edges"]["created"], 0)
        self.assertTrue(any("not declared in fragment.nodes" in w for w in res["warnings"]))

    def test_novel_edge_between_legacy_nodes_is_topology_warning(self):
        # inherits_from(어휘 밖) between two concepts(전용 노드) → TYPED 불가(Entity 아님) → warning
        s = _FakeSession()
        res = fi.merge_fragment(s, {
            "nodes": self._concepts("concept:a", "concept:b"),
            "edges": [{"from": "concept:a", "to": "concept:b", "type": "inherits_from"}]})
        self.assertEqual(res["merged"]["edges"]["created"], 0)
        self.assertTrue(any("topology limitation" in w for w in res["warnings"]))

    def test_connects_via_bridge_to_concept_dedicated(self):
        s = _FakeSession()
        res = fi.merge_fragment(s, {
            "nodes": [{"id": "bridge:1", "type": "bridge", "title": "B"},
                      {"id": "concept:a", "type": "concept", "title": "A"}],
            "edges": [{"from": "bridge:1", "to": "concept:a", "type": "connects_via"}]})
        self.assertEqual(res["merged"]["edges"], {"created": 1})


class IngestFragmentEntryPointTests(unittest.TestCase):
    def test_missing_theme_id_rejected(self):
        res = fi.ingest_fragment(_FakeStore(_FakeSession()),
                                 {"nodes": [{"id": "c1", "type": "concept", "title": "T"}]})
        self.assertFalse(res["executed"])
        self.assertTrue(res["rejected"])
        self.assertTrue(any("theme_id" in w for w in res["warnings"]))

    def test_store_none_degrades(self):
        res = fi.ingest_fragment(None, {"nodes": []}, theme_id="t")
        self.assertFalse(res["executed"])
        self.assertEqual(res["warnings"], ["no reachable store"])

    def test_store_present_executes_via_session(self):
        s = _FakeSession()
        res = fi.ingest_fragment(_FakeStore(s),
                                 {"nodes": [{"id": "concept:a", "type": "concept", "title": "A"}]},
                                 theme_id="t", themes=_themes("t"))
        self.assertTrue(res["executed"])
        self.assertEqual(res["merged"]["nodes"], {"created": 1, "matched": 0})
        self.assertEqual(res["skipped"], 0)


if __name__ == "__main__":
    unittest.main()

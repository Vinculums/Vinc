"""공유 그래프 결과의 배열 순서와 기존 순위·좌표 값을 검증한다."""
from __future__ import annotations

import copy
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from api import cli, fragment, graph_read, query as q, query_base
from test_decision_type import _fresh_store, _restore

OWNER = "user:graph-order"
# 엔진 행 순서를 뒤집는 패치는 `api.query` 와 `api.query_base` 에 함께 건다. query_base 의 함수
# (주제 설명 미러 조회 등)는 `execute` 를 자기 모듈에서 찾으므로 한쪽만 걸면 그 조회는 뒤집히지 않는다.


class GraphOrdering(unittest.TestCase):
    def setUp(self):
        self.store, self.keep = _fresh_store()
        result = fragment.merge_fragment({
            "owner": OWNER,
            "nodes": [
                {"id": f"concept:{name}", "type": "concept", "title": name,
                 "domain": "vinc/other" if name == "m" else "vinc/order"}
                for name in ("z", "a", "m")
            ] + [{"id": "decision:d", "type": "decision", "title": "D",
                  "domain": "vinc/order"}],
            "edges": [
                {"from": "concept:z", "to": "concept:m", "type": "relates_to"},
                {"from": "concept:z", "to": "concept:a", "type": "prerequisite_of"},
                {"from": "decision:d", "to": "concept:z", "type": "about",
                 "evidence": "Measured order", "confidence": 0.8},
            ],
        }, store=self.store)
        self.assertFalse(result.get("partial"), result)

    def tearDown(self):
        if self.store is not None:
            self.store.close()
        _restore(self.keep)

    def test_repeated_fixed_store_reads_are_byte_equal(self):
        for seed in (None, "concept:z", "decision:d", "concept:missing"):
            with self.subTest(seed=seed):
                first = q.graph(owner=OWNER, seed=seed, depth=1, store=self.store)
                second = q.graph(owner=OWNER, seed=seed, depth=1, store=self.store)
                self.assertEqual(json.dumps(first), json.dumps(second))
                ids = [node["id"] for node in first["nodes"]]
                self.assertEqual(ids, sorted(ids))
                edges = [(edge["source"], edge["target"], edge["type"])
                         for edge in first["edges"]]
                self.assertEqual(edges, sorted(edges))

    def test_reversed_engine_rows_keep_result_bytes(self):
        original = q.execute

        def reverse_rows(*args, **kwargs):
            result = original(*args, **kwargs)
            if "rows" in result:
                result = {**result, "rows": list(reversed(result["rows"]))}
            return result

        for seed in (None, "concept:z", "decision:d"):
            with self.subTest(seed=seed):
                expected = q.graph(owner=OWNER, seed=seed, depth=1, store=self.store)
                with patch.object(q, "execute", side_effect=reverse_rows), \
                        patch.object(query_base, "execute", side_effect=reverse_rows), \
                        patch.object(graph_read, "execute", side_effect=reverse_rows):
                    actual = q.graph(owner=OWNER, seed=seed, depth=1, store=self.store)
                self.assertEqual(json.dumps(expected), json.dumps(actual))

    def test_sorting_preserves_ppr_layout_and_every_payload_value(self):
        for seed in (None, "concept:z"):
            with self.subTest(seed=seed):
                with patch.object(graph_read, "_order_graph_result"):
                    before = q.graph(owner=OWNER, seed=seed, store=self.store)
                after = q.graph(owner=OWNER, seed=seed, store=self.store)
                self.assertEqual({n["id"]: n for n in before["nodes"]},
                                 {n["id"]: n for n in after["nodes"]})
                self.assertCountEqual(before["edges"], after["edges"])
                self.assertEqual({k: v for k, v in before.items()
                                  if k not in ("nodes", "edges")},
                                 {k: v for k, v in after.items()
                                 if k not in ("nodes", "edges")})

    def test_decide_and_its_cross_domain_projection_are_byte_stable(self):
        original = q.execute

        def reverse_rows(*args, **kwargs):
            result = original(*args, **kwargs)
            if "rows" in result:
                result = {**result, "rows": list(reversed(result["rows"]))}
            return result

        for topic in ("concept:z", "decision:d"):
            with self.subTest(topic=topic):
                expected = q.decide(topic, owner=OWNER, depth=2, store=self.store)
                with patch.object(q, "execute", side_effect=reverse_rows), \
                        patch.object(query_base, "execute", side_effect=reverse_rows):
                    actual = q.decide(topic, owner=OWNER, depth=2, store=self.store)
                self.assertEqual(json.dumps(expected), json.dumps(actual))
                self.assertTrue(expected["cross_domain"])
                with patch.object(q, "_order_graph_result"):
                    before = q.decide(topic, owner=OWNER, depth=2, store=self.store)
                self.assertEqual({n["id"]: n for n in before["nodes"]},
                                 {n["id"]: n for n in expected["nodes"]})
                for field in ("edges", "cross_domain", "conflicts"):
                    self.assertCountEqual(before[field], expected[field])

    def test_parallel_edge_ties_use_all_properties_without_dropping_edges(self):
        common = {"source": "concept:z", "target": "concept:a", "type": "CONNECTS"}
        edges = [{**common, "role": "b", "evidence": "same", "confidence": 0.2},
                 {**common, "role": "a", "evidence": "same", "confidence": 0.2},
                 {**common, "role": "a", "evidence": "other", "confidence": 0.2},
                 {**common, "role": "a", "evidence": "same", "confidence": 0.9},
                 {**common, "cross_domain": True}, common]
        first = {"nodes": [{"id": "concept:z"}, {"id": "concept:a"}],
                 "edges": copy.deepcopy(edges)}
        second = {"nodes": list(reversed(copy.deepcopy(first["nodes"]))),
                  "edges": list(reversed(copy.deepcopy(edges)))}
        q._order_graph_result(first)
        q._order_graph_result(second)
        self.assertEqual(first, second)
        self.assertEqual(len(first["edges"]), len(edges))
        self.assertCountEqual(first["edges"], edges)

    def test_cli_verb_and_call_have_identical_success_bytes(self):
        self.store.close()
        self.store = None
        args = {"seed_id": "concept:z", "depth": 1}
        outputs = []
        with patch.dict("os.environ", {"VINC_OWNER": OWNER}):
            for suffix in (["node", "graph", "concept:z", "--depth", "1"],
                           ["call", "vinc_graph", "--args", json.dumps(args)],
                           ["node", "graph", "concept:z", "--depth", "1"]):
                out, err = io.StringIO(), io.StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    code = cli.main(["--target", "local", "--format", "json", *suffix])
                self.assertEqual(code, 0, err.getvalue())
                self.assertTrue(json.loads(out.getvalue())["found"])
                outputs.append(out.getvalue())
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(outputs[0], outputs[2])

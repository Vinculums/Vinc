# -*- coding: utf-8 -*-
"""종류 기준선 하네스(`rag_eval.kind_eval`) — 채점과 질문셋, 종류 먼저 거르기, 동점 깨기 스위치.

채점은 순수 함수라 스토어 없이 본다. 질문셋과 스위치는 작은 임베디드 스토어 위에서 본다:
목록 정답이 간선에서 오고 만료된 노드는 빠지는가, 대체 계보를 따라가는가, 오라클이
자르기 전에 거르는가, 그리고 같은 제목 넷의 순서가 종류 접두로 정해진다는 것(누수)과
스위치가 그것을 슬러그 순서로 바꾼다는 것.
"""
from __future__ import annotations

import os
import tempfile
import unittest

from rag_eval import kind_eval as K

try:
    import ladybug                          # noqa: F401
    _DRIVER = True
except Exception:                           # pragma: no cover
    _DRIVER = None


class Scores(unittest.TestCase):
    def test_list_scores_count_precision_over_k_and_recall_over_gold(self):
        s = K.list_scores(["decision:a", "record:x", "decision:b"], ["decision:a", "decision:b",
                                                                    "decision:c", "decision:d"],
                          5, "decision")
        self.assertEqual(s["hits"], 2)
        self.assertAlmostEqual(s["p"], 2 / 5)
        self.assertAlmostEqual(s["r"], 2 / 4)
        self.assertAlmostEqual(s["purity"], 2 / 5)

    def test_pick_scores_mark_an_old_claim_above_the_current_one(self):
        s = K.pick_scores(["record:old", "record:new"], "record:new", ["record:old"])
        self.assertEqual((s["rank_new"], s["rank_old"], s["first"], s["top3"]), (2, 1, False, True))
        self.assertTrue(s["old_above_new"])
        gone = K.pick_scores(["record:old"], "record:new", ["record:old"])
        self.assertIsNone(gone["rank_new"])
        self.assertTrue(gone["old_above_new"], "정답이 없고 대체된 노드만 있으면 그것이 앞선 것이다")

    def test_brief_pick_separates_a_wrong_resolution_from_a_list(self):
        old = K.brief_pick({"status": "resolved", "node": {"id": "d:old"}}, "d:new", ["d:old"])
        self.assertTrue(old["resolved_old"])
        self.assertFalse(old["resolved_new"])
        ch = K.brief_pick({"status": "choose", "candidates": [{"id": "d:new"}, {"id": "d:old"}]},
                          "d:new", ["d:old"])
        self.assertTrue(ch["choose_new_first"] and ch["old_offered"])

    def test_paired_counts_wins_and_losses_and_bootstrap_is_seeded(self):
        p = K.paired([0.0, 0.5, 1.0], [0.5, 0.5, 0.0])
        self.assertEqual((p["wins"], p["losses"], p["ties"]), (1, 1, 1))
        self.assertEqual(K.bootstrap_mean([0.0, 1.0] * 10), K.bootstrap_mean([0.0, 1.0] * 10))
        self.assertEqual(K.bootstrap_mean([]), [])


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치")
class OnAStore(unittest.TestCase):
    OWNER = "user:kind-eval"

    def setUp(self):
        from api import fragment as frag
        from store.backend import open_store
        from store.config import load_config
        self._tmp = tempfile.mkdtemp()
        self._env = {}
        for k, v in {"MEMORY_STORE_BACKEND": "embedded",
                     "MEMORY_DATA_DIR": os.path.join(self._tmp, "store")}.items():
            self._env[k] = os.environ.get(k)
            os.environ[k] = v
        self.store = open_store(load_config())
        self.store.provision(dim=4)
        nodes = [{"id": "concept:soil", "type": "concept", "title": "Soil moisture"}]
        edges = []
        for i in range(4):
            nodes.append({"id": f"decision:soil-{i}", "type": "decision",
                          "title": f"Soil moisture rule {i}"})
            edges.append({"from": f"decision:soil-{i}", "to": "concept:soil", "type": "about"})
        nodes[-1]["props"] = {"valid_from": "2020-01-01", "valid_until": "2021-01-01"}  # 만료
        nodes.append({"id": "record:soil-old", "type": "record", "title": "Soil moisture read 10",
                      "valid_from": "2020-01-01", "valid_until": "2021-01-01"})
        nodes.append({"id": "record:soil-new", "type": "record", "title": "Soil moisture read 12",
                      "valid_from": "2021-01-01"})
        edges.append({"from": "record:soil-new", "to": "record:soil-old", "type": "supersedes",
                      "evidence": "re-measured"})
        # 같은 제목 넷, 슬러그는 접두와 반대 순서(누수 재현).
        for kind, slug in (("concept", "zzz"), ("decision", "ccc"), ("episode", "bbb"),
                           ("record", "aaa")):
            nodes.append({"id": f"{kind}:{slug}-note", "type": kind, "title": "Tie note"})
        # current 정렬 누수: 구간 없는 개념이 더 맞아도 구간 있는 기록이 앞선다.
        nodes.append({"id": "concept:rain-gauge", "type": "concept",
                      "title": "Rain gauge calibration drift"})
        nodes.append({"id": "record:rain-memo", "type": "record", "title": "Rain memo",
                      "valid_from": "2020-01-01"})
        frag.merge_fragment({"owner": self.OWNER, "nodes": nodes, "edges": edges}, store=self.store)

    def tearDown(self):
        self.store.close()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_list_gold_comes_from_about_edges_and_drops_expired_nodes(self):
        rows = K.build_list_set(self.store, self.OWNER, min_gold=3, max_gold=10)
        row = next(r for r in rows if r["kind"] == "decision")
        self.assertEqual(row["topic"], "concept:soil")
        self.assertEqual(row["query"], "decisions about Soil moisture")
        self.assertEqual(sorted(row["gold"]), ["decision:soil-0", "decision:soil-1", "decision:soil-2"])

    def test_superseded_chain_follows_new_to_old(self):
        self.assertEqual(K.superseded_chain(self.store, self.OWNER, "record:soil-new"),
                         ["record:soil-old"])
        rows = K.build_current_set(self.store, self.OWNER, [
            {"id": "t1", "prompt": "soil read", "checks": [{"type": "sources",
                                                            "support": ["record:soil-new"]}]},
            {"id": "t2", "prompt": "x", "checks": [{"type": "sources", "support": ["concept:soil"]}]}])
        self.assertEqual(rows[0]["superseded"], ["record:soil-old"])
        self.assertIn("skip", rows[1])

    def test_oracle_filters_before_the_cut(self):
        from api import query as q
        base = q.search("soil moisture", top_k=2, owner=self.OWNER, store=self.store)
        with K.kind_prefilter(self.store, "decision"):
            got = q.search("soil moisture", top_k=2, owner=self.OWNER, store=self.store)
        ids = [h["id"] for h in got["node_hits"]]
        self.assertEqual(len(ids), 2)
        self.assertTrue(all(i.startswith("decision:") for i in ids), ids)
        self.assertNotEqual([h["id"] for h in base["node_hits"]], ids)

    def test_the_id_tie_break_orders_by_kind_and_the_switch_orders_by_slug(self):
        from api import query as q
        ids = lambda: [h["id"] for h in q.search(  # noqa: E731
            "tie note", top_k=4, owner=self.OWNER, store=self.store)["node_hits"]]
        self.assertEqual(ids(), ["concept:zzz-note", "decision:ccc-note", "episode:bbb-note",
                                 "record:aaa-note"], "오늘의 누수: 동점은 종류 접두 알파벳순")
        with K.leak_tiebreak_slug():
            self.assertEqual(ids(), ["record:aaa-note", "episode:bbb-note", "decision:ccc-note",
                                     "concept:zzz-note"])
        with K.leak_identity():
            self.assertEqual(ids()[0], "concept:zzz-note", "대조 감싸기가 순서를 바꿨다")

    def test_current_mode_puts_an_interval_first_only_inside_the_shown_window(self):
        from api import query as q
        ids = lambda k: [h["id"] for h in q.search(  # noqa: E731
            "rain gauge calibration drift", top_k=k, owner=self.OWNER, store=self.store,
            mode="current")["node_hits"]]
        self.assertEqual(ids(2), ["record:rain-memo", "concept:rain-gauge"],
                         "창 안에서는 구간이 지금인 행이 먼저")
        self.assertEqual(ids(1), ["concept:rain-gauge"],
                         "덜 맞는 기록이 창 밖에서 올라와 자리를 빼앗지 않는다")
        with K.leak_current_relevance_only():
            self.assertEqual(ids(2), ["concept:rain-gauge", "record:rain-memo"])


if __name__ == "__main__":
    unittest.main()

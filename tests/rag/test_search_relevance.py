"""VIN-343: 미승인 보정은 꺼 두고 어휘 근거·페이징·원본 순서를 보존한다."""
from __future__ import annotations

import copy
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from store.config import load_config
from vinc_rag import retrieval
from vinc_rag.search_relevance import classify_rows, load_profile, profile_state, validate_profile


def profile(**changes):
    result = {"version": 1, "id": "fixture-not-production", "status": "approved",
              "encoder_model": "local-en", "encoder_runtime": "fixture-runtime",
              "corpus_revision": "fixture-corpus", "evidence_sha256": "a" * 64,
              "max_distance": {"chunk": .4, "node": .6}}
    result.update(changes)
    return result


class ProfileTests(unittest.TestCase):
    def state(self, p=None, **kw):
        args = {"encoder_model": "local-en", "query_runtime": "fixture-runtime",
                "corpus_revision": "fixture-corpus"}
        args.update(kw)
        return profile_state(p or profile(), **args)["state"]

    def test_승인과_모델_런타임_데이터가_모두_일치해야_한다(self):
        self.assertEqual(self.state(), "active")
        self.assertEqual(self.state(profile(status="experimental")), "experimental")
        self.assertEqual(self.state(encoder_model="other"), "model_mismatch")
        for value in (None, "other"):
            with self.subTest(runtime=value):
                self.assertEqual(self.state(query_runtime=value), "runtime_unverified")
            with self.subTest(corpus=value):
                self.assertEqual(self.state(corpus_revision=value), "corpus_unverified")
        self.assertEqual(self.state(runtime_warning=True), "runtime_unverified")

    def test_유한한_종류별_한도와_근거를_요구한다(self):
        bad = [{"version": True}, {"evidence_sha256": "missing"}, {"status": "pending"},
               {"encoder_runtime": " "}, {"max_distance": {}},
               {"max_distance": {"brief": .5}}]
        bad += [{"max_distance": {"node": v}} for v in (True, -1, 3, math.nan, math.inf, "0.4")]
        for changes in bad:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_profile(profile(**changes))

    def test_잘못된_파일을_묵인하지_않는다(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "profile.json"
            file.write_text("{invalid", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_profile(str(file))

    def test_raw_distance만_쓰고_어휘_그래프_결과를_보존한다(self):
        rows = [{"id": "close", "distance": .3, "score": .00001, "arms": ["vector"]},
                {"id": "far", "distance": .7, "score": 100, "arms": ["vector"]},
                {"id": "keyword", "distance": .99, "arms": ["keyword", "vector"]},
                {"id": "title", "distance": .99, "matched_in": "title"},
                {"id": "graph", "distance": .99, "matched_in": "graph"}]
        saved = copy.deepcopy(rows)
        out = classify_rows(rows, profile(), kind="node")
        self.assertEqual(rows, saved, "원본 응답을 수정했다")
        self.assertEqual([r["id"] for r in out], [r["id"] for r in rows])
        self.assertEqual([r["relevance"]["level"] for r in out],
                         ["primary", "weak", "primary", "primary", "primary"])

    def test_한도가_없는_종류와_거리_없는_행은_미분류다(self):
        rows = [{"distance": v} for v in (None, True, math.nan, math.inf, -1, "0.1")]
        self.assertTrue(all(r["relevance"]["level"] == "unclassified"
                            for r in classify_rows(rows, profile(), kind="node")))
        self.assertEqual(classify_rows([{"distance": .8}],
                         profile(max_distance={"node": .6}), kind="chunk")[0]
                         ["relevance"]["level"], "unclassified")


class ScopedStore:
    driver_name = "fixture"
    scoped_chunk_search = True

    def run(self, *_args, **_kw):
        return []


class RetrievalContractTests(unittest.TestCase):
    def run_search(self, **kw):
        rows = [{"id": f"d#{i:05}", "doc_id": "d", "text": "body",
                 "distance": .8, "arms": ["vector"], "score": 1 / (i + 1)}
                for i in range(4)]
        cfg = load_config({"embedding_model": "local-en"})
        with patch.dict("os.environ", {"VINC_SEARCH_RELEVANCE_PROFILE": ""}), \
             patch.object(retrieval, "_chunk_arms", return_value=(rows, "", ["semantic"], False, False)), \
             patch.object(retrieval, "_node_fallback_search", return_value=[]), \
             patch.object(retrieval, "_graph_arm_search", return_value=[]) as graph:
            result = retrieval.search("fixture", top_k=2, owner="user:fixture",
                                      cfg=cfg, store=ScopedStore(), **kw)
        return result, graph

    def test_기본값은_기존_응답과_페이징을_보존한다(self):
        first, _ = self.run_search()
        self.assertNotIn("relevance_policy", first)
        self.assertTrue(all("relevance" not in r for r in first["rows"]))
        second, _ = self.run_search(cursor=first["next_cursor"])
        self.assertEqual(second["page"]["offset"], 2)
        self.assertEqual(second["node_hits_omitted"], "first_page_only")

    def test_데이터_검증_없이는_프로필이_켜지지_않는다(self):
        result, _ = self.run_search(relevance_profile=profile(), query_runtime="fixture-runtime")
        self.assertEqual(result["relevance_policy"]["state"], "corpus_unverified")
        self.assertTrue(all("relevance" not in r for r in result["rows"]))

    def test_약한_청크가_그래프를_정상_결과로_승격시키지_않는다(self):
        args = {"relevance_profile": profile(), "query_runtime": "fixture-runtime",
                "relevance_corpus_revision": "fixture-corpus"}
        first, graph = self.run_search(**args)
        self.assertEqual(first["relevance_policy"]["state"], "active")
        self.assertEqual(graph.call_args.args[1], [])
        self.assertEqual(first["total_count"], 4)
        second, _ = self.run_search(cursor=first["next_cursor"], **args)
        self.assertEqual(second["page"]["offset"], 2)
        self.assertTrue(all(r["relevance"]["level"] == "weak" for r in second["rows"]))

    def test_잘못된_프로필은_검색_결과를_지우지_않는다(self):
        result, _ = self.run_search(relevance_profile=profile(max_distance={"node": math.nan}))
        self.assertEqual(result["relevance_policy"], {"state": "invalid_profile", "error": "ValueError"})
        self.assertEqual(len(result["rows"]), 2)

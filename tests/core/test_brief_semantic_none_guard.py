"""Synthetic regressions for brief abstention; no store, encoder or evaluation data."""
from __future__ import annotations

import math
import unittest
from unittest.mock import patch

from api import query as q


def candidate(*, arms=("semantic",), distance=0.7, hit_tokens=0,
              ident="concept:synthetic-vector", title="quartz beacon"):
    return {"id": ident, "title": title, "kind": "concept", "domain": "synthetic",
            "arms": list(arms), "distance": distance, "hit_tokens": hit_tokens,
            "title_tokens": 0}


def result(*hits, **diagnostics):
    return {"executed": True, "node_hits": list(hits), "rows": [], **diagnostics}


class NoIOStore:
    def run(self, *args, **kwargs):
        raise AssertionError("a synthetic brief must not read a store")

    def close(self):
        raise AssertionError("a caller-owned synthetic store must not be closed")


class SemanticNoneGuardTests(unittest.TestCase):
    def test_graph_only_is_not_semantic_abstention(self):
        self.assertFalse(q._brief_semantic_none(result(candidate(arms=("graph",), distance=None))))

    def test_graph_evidence_survives_adding_a_distant_semantic_hit(self):
        graph = candidate(arms=("graph",), distance=None, ident="concept:synthetic-graph")
        self.assertFalse(q._brief_semantic_none(result(graph, candidate())))

    def test_graph_and_semantic_provenance_on_one_hit_is_preserved(self):
        self.assertFalse(q._brief_semantic_none(result(candidate(arms=("graph", "semantic")))))

    def test_keyword_and_semantic_provenance_on_one_hit_is_preserved(self):
        self.assertFalse(q._brief_semantic_none(result(candidate(arms=("keyword", "semantic")))))

    def test_unmeasured_provenance_does_not_become_semantic_evidence(self):
        for arms in ((), ("unclassified",), ("semantic", "unclassified")):
            with self.subTest(arms=arms):
                self.assertFalse(q._brief_semantic_none(result(candidate(arms=arms), candidate())))

    def test_distant_exclusively_semantic_hits_still_abstain(self):
        self.assertTrue(q._brief_semantic_none(result(candidate(), candidate(distance=0.8))))

    def test_one_close_semantic_hit_prevents_abstention(self):
        self.assertFalse(q._brief_semantic_none(result(candidate(), candidate(distance=0.2))))

    def test_cutoff_is_inclusive_without_changing_its_value(self):
        self.assertEqual(q._BRIEF_SEMANTIC_NONE_DISTANCE, 0.42970955319294174)
        self.assertFalse(q._brief_semantic_none(result(candidate(distance=q._BRIEF_SEMANTIC_NONE_DISTANCE))))

    def test_cosine_distance_endpoints_remain_valid(self):
        self.assertFalse(q._brief_semantic_none(result(candidate(distance=0.0))))
        self.assertTrue(q._brief_semantic_none(result(candidate(distance=2.0))))

    def test_invalid_distance_is_an_unavailable_measurement(self):
        for distance in (2.1, -0.1, math.nan, math.inf, -math.inf, True, None, "0.7"):
            with self.subTest(distance=distance):
                self.assertFalse(q._brief_semantic_none(result(candidate(distance=distance))))

    def test_every_hit_requires_a_measured_zero_token_count(self):
        for hit_tokens in (1, None, "0", False):
            with self.subTest(hit_tokens=hit_tokens):
                self.assertFalse(q._brief_semantic_none(result(candidate(hit_tokens=hit_tokens))))

    def test_backend_failure_and_rebuilding_still_prevent_abstention(self):
        for flag in ("error", "node_error", "vector_rebuilding"):
            with self.subTest(flag=flag):
                self.assertFalse(q._brief_semantic_none(result(candidate(), **{flag: True})))

    def test_failed_semantic_arm_still_prevents_abstention(self):
        for note in ("synthetic vector unavailable", q.UNSTAMPED_RUNTIME_NOTE + "; synthetic failure"):
            with self.subTest(note=note):
                self.assertFalse(q._brief_semantic_none(result(candidate(), vector_note=note)))
        self.assertFalse(q._brief_semantic_none(result(candidate(), encoder_runtime_warning=True)))

    def test_exact_legacy_runtime_notice_remains_the_existing_exception(self):
        self.assertTrue(q._brief_semantic_none(result(candidate(),
            vector_note=q.UNSTAMPED_RUNTIME_NOTE, encoder_runtime_warning=True)))

    def synthetic_brief(self, *hits):
        with patch.object(q, "search", return_value=result(*hits)) as search, \
             patch.object(q, "node_context", side_effect=AssertionError("no synthetic node read")), \
             patch.object(q, "_brief_strict_on", return_value=False), \
             patch.object(q, "_brief_expose_n", return_value=10):
            got = q.brief("amber orbit", owner="user:synthetic", cfg=object(), store=NoIOStore())
        search.assert_called_once()
        self.assertTrue(got["executed"])
        return got

    def test_brief_keeps_a_graph_only_candidate_as_choose(self):
        got = self.synthetic_brief(candidate(arms=("graph",), distance=None))
        self.assertEqual(got["status"], "choose")
        self.assertEqual(len(got["candidates"]), 1)

    def test_brief_keeps_graph_evidence_when_a_distant_vector_is_added(self):
        graph = candidate(arms=("graph",), distance=None, ident="concept:synthetic-graph", title="cobalt lantern")
        got = self.synthetic_brief(graph, candidate())
        self.assertEqual(got["status"], "choose")
        self.assertEqual({h["id"] for h in got["candidates"]}, {graph["id"], "concept:synthetic-vector"})

    def test_brief_does_not_abstain_on_invalid_distance(self):
        got = self.synthetic_brief(candidate(distance=2.1))
        self.assertEqual(got["status"], "choose")
        self.assertEqual(len(got["candidates"]), 1)

    def test_brief_still_abstains_on_distant_semantic_only_candidates(self):
        got = self.synthetic_brief(candidate())
        self.assertEqual(got["status"], "none")
        self.assertEqual(got["candidates"], [])
        self.assertEqual(got["note"], "no sufficiently close semantic candidate")

    def test_brief_keeps_a_close_semantic_candidate_as_choose(self):
        got = self.synthetic_brief(candidate(distance=0.2))
        self.assertEqual(got["status"], "choose")
        self.assertEqual(len(got["candidates"]), 1)


if __name__ == "__main__":
    unittest.main()

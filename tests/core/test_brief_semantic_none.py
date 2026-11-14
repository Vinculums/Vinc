"""VIN-327: measured no-match policy without lexical or degraded-arm rejection."""
import copy
import math
import unittest
from unittest import mock

from api import brief as brief_mod
from api import query as q


def result(distance):
    return {"executed": True, "arms": ["vector"], "node_hits": [
        {"id": "concept:a", "title": "alpha", "kind": "concept",
         "hit_tokens": 0, "title_tokens": 0, "arms": ["semantic"],
         "distance": distance}]}


class SemanticNoneTests(unittest.TestCase):
    def test_strict_measured_boundary(self):
        cutoff = 0.42970955319294174
        self.assertEqual(q._BRIEF_SEMANTIC_NONE_DISTANCE, cutoff)
        self.assertFalse(q._brief_semantic_none(result(cutoff)))
        self.assertTrue(q._brief_semantic_none(result(math.nextafter(cutoff, math.inf))))
        self.assertFalse(q._brief_semantic_none(result(0.40401591388352764)))
        self.assertTrue(q._brief_semantic_none(result(0.45540319250235584)))

    def test_nearest_semantic_candidate_wins_even_if_not_first(self):
        res = result(0.6)
        nearer = copy.deepcopy(res["node_hits"][0])
        nearer.update(id="concept:b", distance=0.2)
        res["node_hits"].append(nearer)
        self.assertFalse(q._brief_semantic_none(res))

    def test_lexical_qa_and_missing_token_counts_are_not_rejected(self):
        for count in (1, None, "0", False):
            res = result(0.6)
            res["node_hits"][0]["hit_tokens"] = count
            self.assertFalse(q._brief_semantic_none(res), count)

    def test_incomplete_or_degraded_measurements_are_not_no_match(self):
        for key in ("error", "node_error", "vector_note", "vector_rebuilding",
                    "encoder_runtime_warning"):
            res = result(0.6)
            res[key] = "unavailable"
            self.assertFalse(q._brief_semantic_none(res), key)
        for distance in (None, "0.6", True, float("nan"), float("inf")):
            self.assertFalse(q._brief_semantic_none(result(distance)), distance)
        res = result(0.6)
        res["node_hits"][0]["arms"] = ["graph"]
        self.assertFalse(q._brief_semantic_none(res))
        self.assertFalse(q._brief_semantic_none({"node_hits": []}))

    def test_brief_returns_executed_none_without_candidates_or_grounds(self):
        with mock.patch.object(brief_mod, "search", return_value=result(0.6)):
            out = q.brief("xqzvjk", owner="user:test", store=object())
        self.assertTrue(out["executed"])
        self.assertEqual(out["status"], "none")
        self.assertEqual(out["candidates"], [])
        self.assertEqual(out["grounds"], [])
        self.assertNotIn("error", out)

    def test_legacy_stamp_notice_does_not_disable_a_working_semantic_arm(self):
        res = result(0.6)
        res.update(vector_note=q.UNSTAMPED_RUNTIME_NOTE, encoder_runtime_warning=True)
        self.assertTrue(q._brief_semantic_none(res))
        with mock.patch.object(brief_mod, "search", return_value=res):
            out = q.brief("xqzvjk", owner="user:test", store=object())
        self.assertEqual(out["status"], "none")
        self.assertEqual(out["vector_note"], q.UNSTAMPED_RUNTIME_NOTE)
        res["vector_note"] += "; encoder unreachable"
        self.assertFalse(q._brief_semantic_none(res))

    def test_near_zero_token_multilingual_candidate_still_asks_back(self):
        with mock.patch.object(brief_mod, "search", return_value=result(0.4)):
            out = q.brief("다국어 질문", owner="user:test", store=object())
        self.assertEqual(out["status"], "choose")
        self.assertEqual(out["candidates"][0]["id"], "concept:a")


if __name__ == "__main__":
    unittest.main()

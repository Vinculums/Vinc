# -*- coding: utf-8 -*-
"""N1 채점기 — 세 가지가 섞이면 안 된다.

이 리포의 평가 하네스는 **조용한 0점**으로 두 번 물렸다(코퍼스에 정답 문서가 없어서
질의가 필연 miss 였는데 점수만 낮게 나왔다). 그래서 여기서 못 박는 것은 정확도 계산이
아니라 **무엇을 무엇으로 세지 않는가** 다:

1. 답이 없는 질문에 `resolved` 를 주는 것은 **오답이 아니라 정직성 고장**이다. 회수율
   분모에 섞으면 그냥 낮은 점수로 보인다.
2. 확정했는데 틀린 것과 모른다고 한 것은 **다른 고장**이다. 앞은 읽는 쪽이 믿고,
   뒤는 안 믿는다.
3. 정답 노드가 스토어에 없으면 채점을 **거부**한다. 그 행은 필연 오답이고, 세면
   "해소가 나쁘다"가 된다.

근거: [[record:no-node-level-gold-set-exists-2026-09-09]]
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import pathlib
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from eval_brief_resolution import (LOW_REUSE, check_gold, grade_one,  # noqa: E402
                                   rank_of, summarize, title_reuse, union_rank_of)


def _res(status, node=None, candidates=()):
    return {"status": status,
            "node": {"id": node} if node else {},
            "candidates": [{"id": c} for c in candidates],
            "grounds": []}


class Grading(unittest.TestCase):
    def test_a_confident_wrong_answer_is_not_the_same_as_not_knowing(self):
        row = {"gold_node_id": "concept:a", "answerable": True, "gold_kind": "concept"}
        wrong = grade_one(_res("resolved", "concept:b"), row)
        dunno = grade_one(_res("choose", None, ["concept:b"]), row)
        self.assertEqual(wrong["wrong_node"], 1.0)
        self.assertEqual(dunno["wrong_node"], 0.0, "모른다고 한 것을 오답으로 셌다")
        self.assertEqual(dunno["chose"], 1.0)
        self.assertEqual(wrong["hit"][1], 0.0)

    def test_resolving_an_unanswerable_question_is_counted_as_honesty_not_recall(self):
        row = {"gold_node_id": "", "answerable": False, "gold_kind": "none"}
        bad = grade_one(_res("resolved", "concept:anything"), row)
        ok = grade_one(_res("none"), row)
        self.assertEqual(bad["false_resolved"], 1.0)
        self.assertEqual(bad["wrong_node"], 0.0, "정직성 고장이 오답 칸에 섞였다")
        self.assertEqual(ok["false_resolved"], 0.0)
        # 답 불가 행은 회수율 분자에 절대 안 들어간다.
        self.assertEqual(bad["hit"][1], 0.0)
        self.assertEqual(ok["hit"][10], 0.0)

    def test_rank_reads_the_resolved_node_then_the_candidate_order(self):
        self.assertEqual(rank_of("concept:a", _res("resolved", "concept:a")), 1)
        self.assertEqual(rank_of("concept:a", _res("choose", None,
                                                   ["concept:x", "concept:a"])), 2)
        self.assertEqual(rank_of("concept:a", _res("none")), 0)

    def test_the_summary_splits_the_two_denominators(self):
        rows = [
            grade_one(_res("resolved", "concept:a"),
                      {"gold_node_id": "concept:a", "answerable": True,
                       "gold_kind": "concept", "language_axis": "en"}),
            grade_one(_res("resolved", "concept:z"),
                      {"gold_node_id": "concept:a", "answerable": True,
                       "gold_kind": "concept", "language_axis": "ko"}),
            grade_one(_res("resolved", "concept:q"),
                      {"gold_node_id": "", "answerable": False,
                       "gold_kind": "none", "language_axis": "en"}),
        ]
        s = summarize(rows)
        self.assertEqual(s["answerable"], 2)
        self.assertEqual(s["unanswerable"], 1)
        # 회수율은 답 가능 2건이 분모다 — 3건이 아니다.
        self.assertEqual(s["node_recall"]["R@1"], 0.5)
        self.assertEqual(s["wrong_node_rate"], 0.5)
        # 정직성은 답 불가 1건이 분모다.
        self.assertEqual(s["false_resolved_rate"], 1.0)
        self.assertEqual(s["by_axis"]["en"]["n"], 1)
        self.assertEqual(s["by_kind"]["concept"]["n"], 2)

    def test_the_two_kinds_of_unanswerable_are_counted_apart(self):
        """합친 확정률은 두 고장의 가중평균이라 어느 쪽이 움직였는지 못 본다.

        실측(2026-09-10, 81문항): 배포된 규칙의 합친 수는 0.1429 인데 갈라 보면
        주제없음 0.0000 · 속성없음 0.4286 이다. 남은 고장은 통째로 뒤쪽에 있고,
        합친 수만 보면 '거의 안 틀린다'로 읽힌다."""
        def una(status, kind):
            return grade_one(_res(status, "concept:q"),
                             {"gold_node_id": "", "answerable": False,
                              "gold_kind": "none", "language_axis": "en",
                              "unanswerable_kind": kind})
        s = summarize([una("choose", "no-topic"), una("choose", "no-topic"),
                       una("resolved", "attribute-absent")])
        b = s["false_resolved_by_kind"]
        self.assertEqual((b["no-topic"]["n"], b["no-topic"]["rate"]), (2, 0.0))
        self.assertEqual((b["attribute-absent"]["n"], b["attribute-absent"]["rate"]), (1, 1.0))
        # 합친 수는 셋의 평균이라 뒤쪽의 1.0 을 0.3333 으로 희석한다.
        self.assertEqual(s["false_resolved_rate"], 0.3333)


class UnionRank(unittest.TestCase):
    """확정한 질의의 후보는 계약상 비어 있다 — 그것을 "정답이 없다"로 세면 안 된다."""

    def test_a_gold_hidden_behind_a_confident_wrong_answer_still_counts_in_the_union(self):
        row = {"gold_node_id": "concept:a", "answerable": True, "gold_kind": "concept"}
        hits = [{"id": "record:long-title"}, {"id": "concept:a"}]
        g = grade_one(_res("resolved", "record:long-title"), row, hits)
        self.assertEqual(g["rank"], 0, "brief 는 확정하면 후보를 안 싣는다")
        self.assertEqual(g["union_rank"], 2)
        self.assertEqual(g["hit"][3], 0.0)
        self.assertEqual(g["uhit"][3], 1.0, "합집합에 있는 정답을 없다고 셌다")

    def test_the_two_recalls_are_reported_side_by_side(self):
        row = {"gold_node_id": "concept:a", "answerable": True,
               "gold_kind": "concept", "language_axis": "en"}
        s = summarize([grade_one(_res("resolved", "record:x"), row,
                                 [{"id": "record:x"}, {"id": "concept:a"}])])
        self.assertEqual(s["node_recall"]["R@3"], 0.0)
        self.assertEqual(s["union_recall"]["R@3"], 1.0)
        self.assertEqual(s["gold_in_union_rate"], 1.0)

    def test_a_gold_absent_from_the_union_is_zero_in_both(self):
        row = {"gold_node_id": "concept:a", "answerable": True, "gold_kind": "concept"}
        g = grade_one(_res("choose", None, ["concept:x"]), row, [{"id": "concept:x"}])
        self.assertEqual(union_rank_of("concept:a", [{"id": "concept:x"}]), 0)
        self.assertEqual(g["uhit"][10], 0.0)


class TitleTokenReuse(unittest.TestCase):
    """축을 비교하려면 겹침을 함께 봐야 한다 — 안 그러면 저작 차이가 언어 발견으로 읽힌다."""

    def test_a_question_carrying_no_title_token_scores_zero(self):
        self.assertEqual(title_reuse("전혀 다른 낱말로만 쓴 질문", "Deployment order"), 0.0)

    def test_a_question_quoting_the_title_scores_one(self):
        self.assertEqual(title_reuse("what is the deployment order", "deployment order"), 1.0)

    def test_an_empty_title_does_not_divide_by_zero(self):
        self.assertEqual(title_reuse("anything", ""), 0.0)

    def test_the_summary_puts_reuse_next_to_the_axis_it_could_explain(self):
        def row(axis, reuse, urank):
            g = grade_one(_res("choose", None, []),
                          {"gold_node_id": "concept:a", "answerable": True,
                           "gold_kind": "concept", "language_axis": axis,
                           "_title_reuse": reuse},
                          [{"id": "concept:a"}] if urank else [])
            return g

        s = summarize([row("en", 0.8, True), row("ko", 0.1, False)])
        ax = s["by_axis_reuse"]
        self.assertEqual(ax["en"]["median_title_reuse"], 0.8)
        self.assertEqual(ax["ko"]["median_title_reuse"], 0.1)
        self.assertEqual(ax["en"]["gold_in_union"], "1/1")
        # 겹침이 낮은 층에는 en 이 아예 없다 — 그 칸이 비어야 교란이 보인다.
        self.assertEqual(ax["en"][f"gold_in_union_at_reuse_le_{LOW_REUSE}"], "0/0")
        self.assertEqual(ax["ko"][f"gold_in_union_at_reuse_le_{LOW_REUSE}"], "0/1")


class GoldFileRules(unittest.TestCase):
    def test_an_answerable_row_without_a_gold_node_is_rejected(self):
        bad = check_gold([{"query": "q", "answerable": True}])
        self.assertTrue(any("gold_node_id" in b for b in bad))

    def test_an_unanswerable_row_carrying_a_gold_node_is_rejected(self):
        """둘 중 하나가 틀린 것이다 — 조용히 한쪽을 믿으면 그 행이 무엇을 재는지 모른다."""
        bad = check_gold([{"query": "q", "answerable": False,
                           "gold_node_id": "concept:a"}])
        self.assertTrue(any("answerable=false" in b for b in bad))

    def test_a_clean_file_passes(self):
        self.assertEqual(check_gold([
            {"query": "q", "answerable": True, "gold_node_id": "concept:a",
             "language_axis": "ko"},
            {"query": "q2", "answerable": False, "gold_node_id": "",
             "language_axis": "en", "unanswerable_kind": "no-topic"}]), [])

    def test_an_unanswerable_row_without_a_kind_is_rejected(self):
        """종류가 없으면 한 수가 두 고장을 뜻한다. 이 정답셋이 실제로 그랬다 —
        답 불가 6문항이 전부 '주제 자체가 없다' 쪽이라 확정률이 늘 0.0000 이었고,
        그동안 '주제는 있는데 속성이 없다' 쪽은 재고 있지도 않았다."""
        bad = check_gold([{"query": "q", "answerable": False, "gold_node_id": "",
                           "language_axis": "en"}])
        self.assertTrue(any("unanswerable_kind" in b for b in bad), bad)

    def test_an_unknown_kind_is_rejected(self):
        bad = check_gold([{"query": "q", "answerable": False, "gold_node_id": "",
                           "language_axis": "en", "unanswerable_kind": "dunno"}])
        self.assertTrue(any("unanswerable_kind" in b for b in bad), bad)


class SemanticArmIsOptInAndDeclared(unittest.TestCase):
    """의미 팔은 **옵트인**이고, 켰는지 껐는지가 결과에 남아야 한다.

    벡터 없는 스토어에 임베딩을 넘기면 없는 팔을 있는 척 재고, 켜 놓고 안 적으면 두 런이
    비교 가능해 보인다. 둘 다 조용한 오측이다."""

    def _gold(self):
        gold = pathlib.Path(self.tmp) / "g.jsonl"
        gold.write_text(json.dumps({"query": "q", "gold_node_id": "concept:a",
                                    "answerable": True, "gold_kind": "concept",
                                    "language_axis": "en"})
                        + "\n", encoding="utf-8")
        return gold

    def _run(self, argv_extra, spy):
        import eval_brief_resolution as e
        gold = self._gold()
        out = pathlib.Path(self.tmp) / "o.json"
        with (mock.patch.object(e, "open_store", return_value=_FakeStore()),
              mock.patch.object(e, "gold_present", return_value=[]),
              mock.patch.object(e.q, "brief", spy),
              mock.patch.object(e.q, "search",
                                lambda *a, **k: {"node_hits": []}),
              contextlib.redirect_stdout(io.StringIO())):
            rc = e.main(["--data", "x", "--owner", "o", "--gold", str(gold),
                         "--out", str(out)] + argv_extra)
        self.assertEqual(rc, 0)
        return json.loads(out.read_text(encoding="utf-8"))

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_without_an_encoder_no_embedding_is_passed_and_the_note_says_so(self):
        seen = {}

        def spy(query, **kw):
            seen["vec"] = kw.get("query_embedding")
            return _res("none")

        got = self._run([], spy)
        self.assertIsNone(seen["vec"], "벡터 없는 스토어에 임베딩을 넘겼다")
        self.assertIn("의미 팔 없음", got["summary"]["arms_note"])
        self.assertEqual(got["encoder"], "")

    def test_with_an_encoder_the_vector_reaches_brief_and_the_note_records_it(self):
        seen = {}

        def spy(query, **kw):
            seen["vec"] = kw.get("query_embedding")
            return _res("none")

        with mock.patch.dict("sys.modules", {"encoder": _fake_encoder_module()}):
            got = self._run(["--encoder", "some/model=art.onnx"], spy)
        self.assertEqual(seen["vec"], [0.5, 0.5])
        self.assertIn("의미 팔이 돌았다", got["summary"]["arms_note"])
        self.assertEqual(got["encoder"], "some/model=art.onnx")

    def test_the_summary_carries_the_hash_of_the_store_file_it_read(self):
        """지문이 다른 두 결과는 나란히 두면 안 된다 — 그 판정 재료가 요약에 있어야 한다."""
        data = pathlib.Path(self.tmp) / "hub.db"
        data.write_bytes(b"store bytes")
        got = self._run(["--data", str(data)], lambda query, **kw: _res("none"))
        self.assertEqual(got["summary"]["store_sha256"],
                         hashlib.sha256(b"store bytes").hexdigest())

    def test_a_store_with_a_leftover_wal_is_refused_before_it_opens(self):
        """스토어는 본 파일과 WAL 두 파일이다 — WAL 이 있으면 본 파일 지문은 내용이 아니다."""
        import eval_brief_resolution as e
        data = pathlib.Path(self.tmp) / "hub.db"
        data.write_bytes(b"store bytes")
        pathlib.Path(str(data) + ".wal").write_bytes(b"pending")
        opener = mock.Mock()
        err = io.StringIO()
        with (mock.patch.object(e, "open_store", opener),
              contextlib.redirect_stderr(err)):
            rc = e.main(["--data", str(data), "--owner", "o",
                         "--gold", str(self._gold())])
        self.assertEqual(rc, 1)
        opener.assert_not_called()
        self.assertIn(".wal", err.getvalue())


class _FakeStore:
    def close(self):
        pass


def _fake_encoder_module():
    mod = types.ModuleType("encoder")

    class Encoder:
        def __init__(self, model, path=None):
            self.model, self.path = model, path

        def encode(self, texts, kind=None):
            return [[0.5, 0.5] for _ in texts]

    mod.Encoder = Encoder
    return mod


if __name__ == "__main__":
    unittest.main()

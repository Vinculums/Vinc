# -*- coding: utf-8 -*-
"""Laya 술어 하네스(M1-3, VIN-405) — 모델 없이 도는 부분을 잠근다.

지키는 것: 핀과 질문 문구의 해시가 바뀌면 여기서 먼저 깨진다 · 지표는 손으로 센 값과 같다 ·
층화 분할은 결정론이고 반반이다 · 출력 파일의 핀 머리 줄은 데이터로 읽히지 않는다 ·
규칙 기준선은 문서가 적은 그대로다. laya·torch·numpy 는 import 하지 않는다.
"""
from __future__ import annotations

import json
import math
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from rag_eval.laya import metrics as M
from rag_eval.laya import pins, report, split
from rag_eval.laya.run_gold import _flat, load_calib, read_jsonl, states_for


class PinTests(unittest.TestCase):
    def test_질문_문구_해시가_고정이다(self):
        # 문구를 고치면 측정을 다시 해야 한다. 이 값을 고칠 때는 테스트 방식 문서 §2 도 같이 고친다.
        self.assertEqual(pins.questions_hash(), "500e613737d1")
        self.assertEqual(pins.REVISION, "e4e9ddf21a7b1903b7acffd8814ad4307bf63a67")
        self.assertEqual(pins.SHA256["model.safetensors"][:12], "9d628fd971b7")

    def test_질문은_전부_영어_noul_이고_기준을_적는다(self):
        for name, q in pins.QUESTIONS.items():
            self.assertEqual(q["type"], "noul", name)
            self.assertTrue(q["instructions"].isascii(), name)
            self.assertEqual(set(q["criteria"]), {"true", "false"}, name)

    def test_state_템플릿과_문맥_상한(self):
        s = pins.render_state(" A text ", "B text", "x" * 1000)
        self.assertTrue(s.startswith("A: A text\nB: B text\nContext: "))
        self.assertEqual(len(s.split("Context: ")[1]), 400)
        self.assertNotIn("Context", pins.render_state("a", "b", None))

    def test_언어_판정(self):
        self.assertEqual(pins.detect_lang("리랭커는 기다려야 한다"), "ko")
        self.assertEqual(pins.detect_lang("The reranker waits"), "en")
        self.assertEqual(pins.detect_lang("리랭커는 기다려야 한다", "The reranker waits"), "mixed")
        self.assertEqual(pins.detect_lang("리랭커 reranker 컨테이너 container"), "mixed")
        self.assertEqual(pins.detect_lang("1234"), "en")

    def test_핀_머리는_모델_없이도_만들어진다(self):
        h = pins.pin_header()
        self.assertEqual(h["revision"], pins.REVISION)
        self.assertIn("questions_hash", h)

    def test_대칭_술어만_역방향을_묻는다(self):
        pair = {"a": {"text": "a"}, "b": {"text": "b"}, "context": None}
        got = {d: preds for d, _state, preds in states_for(pair)}
        self.assertEqual(got["ab"], list(pins.PREDICATES))
        self.assertEqual(got["ba"], ["contradicts", "same_as"])


class MetricTests(unittest.TestCase):
    P = [0.9, 0.8, 0.3, 0.2, 0.6, 0.4]
    Y = [1, 1, 0, 0, 0, 1]

    def test_brier(self):
        self.assertAlmostEqual(M.brier(self.P, self.Y), 0.15, places=9)

    def test_auroc_는_순위_통계다(self):
        self.assertAlmostEqual(M.auroc(self.P, self.Y), 8 / 9, places=9)
        self.assertAlmostEqual(M.auroc([0.5, 0.5, 0.5, 0.5], [1, 0, 1, 0]), 0.5)  # 동점은 평균 순위
        self.assertTrue(math.isnan(M.auroc([0.1, 0.9], [1, 1])))

    def test_ece_완벽하면_0_이다(self):
        self.assertAlmostEqual(M.ece([1.0, 0.0, 1.0], [1, 0, 1]), 0.0)
        self.assertGreater(M.ece([0.99, 0.99], [1, 0]), 0.4)

    def test_coverage_at_error_는_보수적이다(self):
        got = M.coverage_at_error(self.P, self.Y, 0.05)
        self.assertEqual(got["coverage"], 4 / 6)
        self.assertEqual(got["error"], 0.0)
        none = M.coverage_at_error([0.99] * 4, [1, 1, 0, 0], 0.05)
        self.assertEqual(none["coverage"], 0.0)

    def test_softmax_와_평탄화(self):
        p = M.softmax([0.0, 0.0])
        self.assertAlmostEqual(p[1], 0.5)
        self.assertEqual(_flat([[1.0, 2.0], 3.0]), [1.0, 2.0, 3.0])

    def test_비대칭도와_다수_기준선(self):
        got = M.asymmetry([0.9, 0.5], [0.6, 0.5])
        self.assertAlmostEqual(got["mean"], 0.15)
        self.assertEqual(got["over_0_2"], 0.5)
        self.assertEqual(M.majority_baseline([1, 1, 0]), [1.0, 1.0, 1.0])


class SplitTests(unittest.TestCase):
    def _pairs(self, n=40):
        return [{"pair_id": f"p{i:03d}", "lang": ["ko", "en", "mixed"][i % 3], "source": ["ties", "search"][i % 2],
                 "labels": {"supports": i % 2, "contradicts": 0, "same_as": int(i % 5 == 0)}} for i in range(n)]

    def test_결정론이고_반반이다(self):
        a, b = split.make_split(self._pairs()), split.make_split(self._pairs())
        self.assertEqual(a, b)
        self.assertEqual(Counter(a.values()), Counter({"calib": 20, "eval": 20}))

    def test_층_안에서도_치우치지_않는다(self):
        pairs = self._pairs(60)
        sp = split.make_split(pairs)
        for key in {split.stratum(p) for p in pairs}:
            ids = [p["pair_id"] for p in pairs if split.stratum(p) == key]
            c = Counter(sp[i] for i in ids)
            self.assertLessEqual(abs(c["calib"] - c["eval"]), 1, key)

    def test_분할_파일은_한_번만_만들고_다시_읽는다(self):
        with tempfile.TemporaryDirectory() as d:
            gold = Path(d) / "gold.jsonl"
            gold.write_text("", encoding="utf-8")
            first = split.load_or_make(gold, self._pairs())
            self.assertTrue(split.split_path(gold).exists())
            self.assertEqual(split.load_or_make(gold, self._pairs(10)), first)


class FileFormatTests(unittest.TestCase):
    def test_핀_머리_줄은_데이터가_아니다(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "x.jsonl"
            f.write_text('# {"revision": "abc"}\n{"a": 1}\n\n# {"kind": "timing"}\n{"a": 2}\n', encoding="utf-8")
            self.assertEqual(read_jsonl(f), [{"a": 1}, {"a": 2}])

    def test_보정_파일은_술어와_언어로_색인된다(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "calib-supports-en.json").write_text(json.dumps(
                {"predicate": "supports", "lang": "en", "temperature": 2.0, "calibration_id": "x"}), encoding="utf-8")
            (Path(d) / "summary.json").write_text("[]", encoding="utf-8")
            got = load_calib(Path(d))
            self.assertEqual(list(got), [("supports", "en")])
            self.assertIsNone(load_calib(None).get(("supports", "en")))


class ReportRuleTests(unittest.TestCase):
    def test_규칙_기준선은_문서_그대로다(self):
        g = {"a_text": "Decisions are retired with valid_until and never deleted.",
             "b_text": "Decisions are retired with valid_until, never deleted.", "context": "chunk"}
        self.assertEqual(report.rule_baseline("supports", g), 1.0)       # 같은 청크면 supports
        self.assertEqual(report.rule_baseline("supports", {**g, "context": None}), 0.0)
        self.assertEqual(report.rule_baseline("same_as", g), 1.0)        # 제목 Jaccard >= 0.6
        self.assertEqual(report.rule_baseline("contradicts", g), 0.0)    # 둘 다 부정어가 있다
        self.assertEqual(report.rule_baseline("contradicts", {**g, "b_text": "A decision is kept."}), 1.0)
        self.assertEqual(report.rule_baseline("contradicts", {"a_text": "금지다", "b_text": "허용이다"}), 1.0)

    def test_게이트_수치는_사전_등록_값이다(self):
        self.assertEqual(report.GATE, {"auroc_margin": 0.05, "ece_max": 0.10, "coverage_min": 0.5})
        self.assertEqual(pins.TARGET_ERROR, 0.05)
        self.assertEqual(pins.ASYMMETRY_ABSTAIN, 0.2)


class FrozenCoverageTests(unittest.TestCase):
    """커버리지는 동결 문턱으로 세고, 비대칭 기권은 분모에 남는다(handoff 10-07 지적 둘)."""

    @staticmethod
    def _row(pid, pred, direction, p, label, th: float | None = 0.7, lang="en", truncated=False):
        return {"pair_id": pid, "direction": direction, "predicate": pred, "lang": lang, "label": label,
                "p_raw": p, "p_cal": p, "abstain_below": th, "calibration_id": "abc", "truncated": truncated}

    def _rows(self):
        R = self._row
        return [
            R("p1", "same_as", "ab", 0.90, 1), R("p1", "same_as", "ba", 0.85, 1),     # 덮음, 맞음
            R("p2", "same_as", "ab", 0.90, 0), R("p2", "same_as", "ba", 0.30, 0),     # 비대칭 0.6 → 기권
            R("p3", "same_as", "ab", 0.60, 1), R("p3", "same_as", "ba", 0.55, 1),     # conf 0.6 < 0.7 → 기권
            R("p4", "same_as", "ab", 0.20, 0), R("p4", "same_as", "ba", 0.25, 0),     # 덮음, 맞음
            R("p5", "same_as", "ab", 0.95, 1, truncated=True), R("p5", "same_as", "ba", 0.95, 1, truncated=True),
            R("p6", "same_as", "ab", 0.95, None), R("p6", "same_as", "ba", 0.95, None),  # 라벨 없음 → 제외
            R("p1", "supports", "ab", 0.90, 1, th=None), R("p2", "supports", "ab", 0.10, 0, th=None),
            R("p3", "supports", "ab", 0.80, 1, th=None), R("p4", "supports", "ab", 0.30, 0, th=None),
        ]

    def test_frozen_coverage_는_기권을_분모에_남긴다(self):
        items = [{"conf": 0.9, "correct": 1.0, "threshold": 0.7, "asym": 0.05, "has_reverse": True},
                 {"conf": 0.9, "correct": 0.0, "threshold": 0.7, "asym": 0.6, "has_reverse": True},
                 {"conf": 0.6, "correct": 1.0, "threshold": 0.7, "asym": 0.05, "has_reverse": True},
                 {"conf": 0.8, "correct": 1.0, "threshold": 0.7, "asym": None, "has_reverse": False}]
        got = report.frozen_coverage(items)
        self.assertEqual((got["n"], got["covered"], got["coverage"]), (4, 1, 0.25))
        self.assertEqual((got["abstained_asymmetry"], got["abstained_below_threshold"], got["abstained_missing_reverse"]),
                         (1, 1, 1))
        self.assertEqual(got["error"], 0.0)
        self.assertEqual(got["thresholds"], [0.7])
        none = report.frozen_coverage([{"conf": 0.9, "correct": 1.0, "threshold": None, "asym": None, "has_reverse": True}])
        self.assertEqual((none["covered"], none["no_threshold"]), (0, 1))
        self.assertTrue(math.isnan(none["error"]))

    def test_보고서는_동결_문턱으로_세고_절단_행을_뺀다(self):
        with tempfile.TemporaryDirectory() as d:
            inp = Path(d) / "eval.jsonl"
            inp.write_text('# {"revision": "x"}\n' + "\n".join(json.dumps(r) for r in self._rows()) + "\n",
                           encoding="utf-8")
            out = Path(d) / "report.md"
            self.assertEqual(report.main(["--in", str(inp), "--out", str(out)]), 0)
            res = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
            self.assertEqual(res["rows_truncated_excluded"], 2)
            self.assertEqual(res["rows_unlabeled_excluded"], 2)
            rows = {(r["predicate"], r["lang"]): r for r in res["table"]}
            same = rows[("same_as", "all")]
            self.assertEqual(same["n"], 4)                                  # 기권한 쌍도 분모에 있다
            self.assertEqual(same["frozen"]["covered"], 2)
            self.assertEqual(same["frozen"]["coverage"], 0.5)
            self.assertEqual(same["frozen"]["abstained_asymmetry"], 1)
            self.assertEqual(same["frozen"]["abstained_below_threshold"], 1)
            self.assertEqual(same["frozen"]["error"], 0.0)
            self.assertTrue(same["gate"]["coverage_ok"])
            self.assertTrue(same["gate"]["error_within_target"])
            # 실제 오류율 판정은 보고만 하고 사전 등록 셋(auroc·ece·coverage)의 pass 에 섞지 않는다
            self.assertEqual(same["gate"]["pass"],
                             same["gate"]["auroc_ok"] and same["gate"]["ece_ok"] and same["gate"]["coverage_ok"])
            self.assertEqual(same["calibration_ids"], ["abc"])
            self.assertIn("posthoc_coverage_at_error", same)                # 사후 곡선은 진단으로만 남는다
            sup = rows[("supports", "all")]
            self.assertEqual(sup["frozen"]["no_threshold"], 4)             # --calib 없이 돌린 결과
            self.assertFalse(sup["gate"]["coverage_ok"])
            self.assertEqual(sup["auroc"], 1.0)
            self.assertIn("동결 문턱 없음", out.read_text(encoding="utf-8"))


if __name__ == "__main__":
    sys.exit(unittest.main())

# -*- coding: utf-8 -*-
"""N2 하네스 — 모델 없이 도는 부분만 잰다.

호스트·채점 호출은 `claude` CLI 와 스토어 사본이 있어야 해서 CI 에서 돌지 않는다. 여기서
못 박는 것은 그 밖의 셋이다: 고정 프롬프트가 파일럿과 같은 바이트인가, 답 파서가 한국어
기권을 놓치지 않는가, 층 집계가 판정 규약을 채점자보다 믿는가.
근거: [[record:n2-pilot-brief-material-beats-top-chunks-when-the-first-node-is-right-2026-09-12]]
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from rag_eval import answer_material as n2  # noqa: E402

#: 2026-09-12 파일럿 기록이 인용하는 해시. 이 값이 바뀌면 파일럿과 비교할 수 없다 —
#: 프롬프트를 고칠 일이 생기면 새 파일(v2)로 내고 이 표는 그대로 둘 것.
PILOT_SHA = {
    "answer_v1_material.txt": "e44013b832fbcd4be64068212b054d13d7040f80eafb3571d0da469f14504495",
    "answer_v1_alone.txt": "e846d3f4aae935687327f9ad9e748b7a3214e9137ff49199970c5d5ede1788e9",
    "judge_v1.txt": "2266b17bbf9c89e41664c952b0f8825efb8ae6e15f3655681211191fa9079d86",
}


class FrozenPromptTests(unittest.TestCase):
    def test_고정_프롬프트가_파일럿과_같은_해시다(self):
        for name, want in PILOT_SHA.items():
            self.assertEqual(n2.sha(n2.PROMPTS / name), want, name)

    def test_해시는_줄끝에_흔들리지_않는다(self):
        with tempfile.TemporaryDirectory() as d:
            lf, crlf = Path(d) / "lf.txt", Path(d) / "crlf.txt"
            lf.write_bytes(b"a\nb\n")
            crlf.write_bytes(b"a\r\nb\r\n")
            self.assertEqual(n2.sha(lf), n2.sha(crlf))


class StoreHashTests(unittest.TestCase):
    def test_WAL_이_남은_스토어는_해시_대신_표지를_돌려준다(self):
        """본 파일만 해시하면 WAL 에 남은 쓰기가 지문에서 빠져 고정값과 같게 나온다."""
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "hub.db"
            db.write_bytes(b"store bytes")
            clean = n2.file_sha(db)
            Path(str(db) + ".wal").write_bytes(b"pending")
            self.assertEqual(n2.file_sha(db), "wal-present")
            self.assertNotEqual(clean, "wal-present")


class ParseTests(unittest.TestCase):
    def test_답과_인용을_가른다(self):
        ans, cited = n2.parse_answer("ANSWER: 사과다.\nCITED: concept:a, `record:b`")
        self.assertEqual(ans, "사과다.")
        self.assertEqual(cited, ["concept:a", "record:b"])

    def test_NONE_은_인용이_아니다(self):
        self.assertEqual(n2.parse_answer("ANSWER: ABSTAIN\nCITED: NONE")[1], [])


def _mat(i, stratum, answerable, gold=""):
    node = gold or "concept:x"
    return {"i": i, "stratum": stratum, "answerable": answerable, "gold": gold,
            "path_b": "resolved",
            "mat": {"a": {"rows": [{"id": "d1#0", "text": "x"}]},
                    "b": {"node": {"id": node}, "grounds": [node], "excerpts": [],
                          "links": {"items": []}}},
            "calls": {"a": 1, "b": 1, "c": 0}, "returned_bytes": {"a": 10, "b": 5, "c": 0}}


def _judged(i, arm, verdict, answer="ANSWER: x", cited=()):
    return {"i": i, "arm": arm, "answer": answer, "cited": list(cited), "verdict": verdict,
            "in_tokens": 1, "out_tokens": 1}


class ReportTests(unittest.TestCase):
    def test_답_불가에_실질_답은_규약상_환각이다(self):
        """파일럿에서 채점자가 이런 행을 0 으로 매겼다 — 규약값은 채점자와 따로 센다."""
        by = n2.grade_rows([_mat(0, "abstention", False)],
                           [_judged(0, "a", {"abstained": 0, "correct": 0, "hallucinated": 0})])
        row = by[("abstention", "a")][0]
        self.assertEqual(row["hallucinated"], 0)
        self.assertEqual(row["hallucinated_protocol"], 1)

    def test_끝에_붙은_ABSTAIN_도_기권으로_센다(self):
        by = n2.grade_rows([_mat(0, "abstention", False)],
                           [_judged(0, "c", {"abstained": 1, "correct": 1, "hallucinated": 0},
                                    answer="해당 정보를 알 수 없습니다. ABSTAIN")])
        self.assertEqual(by[("abstention", "c")][0]["abstain_literal"], 1)

    def test_작은_층에는_구간을_내지_않는다(self):
        mats = [_mat(i, "decision-rationale", True, "decision:d") for i in range(3)]
        judged = [_judged(i, arm, {"abstained": 0, "correct": 1, "hallucinated": 0})
                  for i in range(3) for arm in n2.ARMS]
        out = n2.summarize(n2.grade_rows(mats, judged))
        self.assertIn("below", out["paired_bootstrap_accuracy_delta"]["decision-rationale"])

    def test_부트스트랩은_재현된다(self):
        d = [1, 0, 1, 1, 0] * 4
        self.assertEqual(n2.boot(d), n2.boot(d))
        self.assertEqual(n2.boot([0.0] * 20), [0.0, 0.0, 0.0])


if __name__ == "__main__":
    unittest.main()

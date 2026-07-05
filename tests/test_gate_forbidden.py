# -*- coding: utf-8 -*-
"""gate_forbidden 게이트 단위 테스트 (stdlib unittest).

게이트가 no-op 이 아님을 잠근다: F1/F2/F7 검출 + F7 경계 구분
(weg-memory 금지 / weg-9000-memory 허용).
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import gate_forbidden as g  # noqa: E402


def _scan_text(name: str, content: str):
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / name).write_text(content, encoding="utf-8")
        return g.scan(Path(d))


class TestGate(unittest.TestCase):
    def test_f1_billing_metering(self):
        v = _scan_text("a.py", "usage_record = 1\ntoken_margin = 2\nUsageRecord()\n")
        gates = {x.gate for x in v}
        self.assertIn("F1", gates)
        self.assertEqual(3, len(v))

    def test_f1_allows_open_domain_billing_word(self):
        # "billing" 은 오픈도메인 데이터·QueryPlan.kind 는 정본 코드 — F1 아님(§1)
        v = _scan_text("a.py", 'domain = "billing"\nplan.kind == "recall"\ntags = ["quota"]\n')
        self.assertEqual(0, len(v))

    def test_f2_learning_copy(self):
        v = _scan_text("a.ts", '"나의 기억"\n"학습을 자산으로"\n"배움"\n')
        self.assertTrue(all(x.gate == "F2" for x in v))
        self.assertEqual(3, len(v))

    def test_f2_allows_learner_identifier(self):
        # 코드 식별자 learner_* 는 D6 전까지 허용
        v = _scan_text("a.py", "learner_pattern = load()\n")
        self.assertEqual(0, len(v))

    def test_f7_old_name_forbidden(self):
        v = _scan_text("a.py", 'path = "weg-memory/store"\n')
        self.assertEqual(1, len(v))
        self.assertEqual("F7", v[0].gate)

    def test_f7_code_provenance_allowed(self):
        # weg-9000-memory(코드 출처)는 허용 — weg-memory 부분문자열 아님
        v = _scan_text("a.py", "# weg-9000-memory 에서 추출한 엔진\n")
        self.assertEqual(0, len(v))

    def test_clean_passes(self):
        v = _scan_text("a.py", "def ingest(): return 'vinc'\n")
        self.assertEqual(0, len(v))


if __name__ == "__main__":
    unittest.main()

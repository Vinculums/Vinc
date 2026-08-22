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
    # name 은 루트 기준 상대경로다 — F9 처럼 경로로 관할이 갈리는 규칙이 있어서
    # 파일이 실제로 그 자리에 놓여야 한다(`app/src/...`).
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
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


class TestF9KoreanCopy(unittest.TestCase):
    """F9 — 한국어 사용자 가시 어휘(vinc.spec.ui-copy-ko §4)."""

    def test_forbidden_word_in_app_src_string(self):
        v = _scan_text("app/src/copy.ts", 'note: "당신에게 남습니다",\n')
        self.assertEqual(1, len(v))
        self.assertEqual("F9", v[0].gate)

    def test_all_seven_words_are_wired(self):
        src = "".join(f'x{i}: "{w}",\n' for i, w in enumerate(
            ("말뭉치", "채굴", "표면", "레일", "관문", "당신", "귀하")))
        v = _scan_text("app/src/copy.ts", src)
        self.assertEqual(7, len(v))
        self.assertEqual({"F9"}, {x.gate for x in v})

    def test_comment_in_app_src_is_not_a_violation(self):
        # 이 리포의 주석 언어는 한국어다(CLAUDE.md). "제품 표면"·"레일은 좁다" 는
        # 코드를 설명하는 말이지 화면에 나가는 카피가 아니다.
        v = _scan_text("app/src/view.ts",
                       "// 이 표면의 레일은 좁다\n/* 관문 윤곽선 */\n")
        self.assertEqual(0, len(v))

    def test_scope_is_app_src_only(self):
        # 문서·스크립트·테스트의 같은 낱말은 F9 관할 밖이다.
        v = _scan_text("docs/note.md", "표면과 레일과 관문\n")
        self.assertEqual(0, len(v))

    def test_ontology_is_held_not_forbidden(self):
        # §9.2 — 영어 쪽 제품 명사 결정 전까지 보류. 금지 목록에 없어야 한다.
        v = _scan_text("app/src/copy.ts", 'x: "온톨로지",\n')
        self.assertEqual(0, len(v))


if __name__ == "__main__":
    unittest.main()

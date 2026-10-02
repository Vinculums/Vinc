# -*- coding: utf-8 -*-
"""덱 패리티 테스트 (stdlib unittest).

`vinc.spec.ui-copy` §8 이 적어 둔 구멍 두 개를 잠근다:
  · "덱 대신 코드에 문자열을 넣어도 아무것도 실패하지 않는다"
  · "키가 고아가 돼도 아무것도 실패하지 않는다"

컴파일 타임 `Deck` 타입은 EN↔KO 누락만 잡는다. 나머지는 여태 사람 눈이었다.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import ko_copy_inventory as inv  # noqa: E402


class TestNoHangulOutsideDeck(unittest.TestCase):
    def test_no_deck_eligible_hangul_outside_copy_ts(self):
        hits = [h for h in inv.hangul_outside_deck(ROOT) if h.eligible]
        self.assertEqual(
            [], [f"{h.file}:{h.line}  {h.text}" for h in hits],
            "덱 밖에 사용자 가시 한글이 있다. copy.ts 로 옮기거나, 카피가 아니라면"
            " vinc.spec.ui-copy §7 기준으로 면제 사유를 코드로 표현할 것.")

    def test_exemptions_are_still_recognised(self):
        # 면제 판정이 통째로 죽으면 위 테스트가 조용히 전량 통과한다 — 그 상태와
        # 진짜 통과를 가른다. 면제 대상(정규식 문자 클래스·console)은 실재한다.
        verdicts = {h.verdict for h in inv.hangul_outside_deck(ROOT)}
        self.assertTrue(any(v.startswith("exempt:") for v in verdicts),
                        f"면제 판정이 하나도 안 났다: {verdicts}")

    def test_language_picker_is_exempt_but_the_exemption_is_narrow(self):
        """언어 선택기의 `한국어` 는 덱에 넣으면 안 된다 — 영어 화면에서 "Korean" 이
        되면 영어를 못 읽는 사람이 자기 언어를 못 찾는다. §7 의 "번역하면 동작이
        달라지는 것은 카피가 아니다"가 그대로 걸리는 자리다.

        함께 재는 것은 이 면제가 **얼마나 좁은가**다. `data-lang-opt` 없이 같은 문자열만 있으면 면제가
        아니어야 한다 — 안 그러면 이 면제가 한글 전체를 통과시키는 구멍이 된다."""
        picker = ('`<button class="chip" data-lang-opt="${l}">'
                  '${l === "en" ? "English" : "한국어"}</button>`')
        self.assertEqual(inv._verdict(picker),
                         "exempt: language picker names each language in itself")
        self.assertEqual(inv._verdict('const label = "한국어";'), "deck-eligible")


class TestNoOrphanDeckKeys(unittest.TestCase):
    def test_every_deck_key_has_a_call_site(self):
        dead = inv.dead_deck_keys(ROOT)
        self.assertEqual(
            [], [k for k, _, _ in dead],
            "호출부가 0 인 덱 키가 있다. 화면이 사라졌으면 키도 지울 것"
            " (vinc.spec.ui-copy §8).")


class TestKoDeckBannedStem(unittest.TestCase):
    def test_ko_deck_has_no_scattered_stem(self):
        """`흩어(진·져 있는) 지식` 은 한국어 히어로가 다 같이 쓰는 문장이라 발주자가
        금지했다. 사이트는 `gate_copy.mjs` 의 `ko-scattered` 가 막는데 앱에는 없어서
        탭 제목(`Vinc · {tagline}`)으로 새어 나갔다. 주석은 걸지 않는다.
        근거: [[vinc.spec.ui-copy-ko §9.3]]"""
        lines = (ROOT / inv._DECK).read_text(encoding="utf-8").splitlines()
        _, _, ko_s, ko_e = inv._deck_bounds(lines)
        hits = [f"copy.ts:{i}  {c.strip()}" for i, c in inv.strip_comments("\n".join(lines))
                if ko_s <= i <= ko_e and "흩어" in c]
        self.assertEqual([], hits, "KO 덱에 `흩어` 가 있다. 무엇을 담고 나누는지로 다시 쓸 것")


class TestScannerItself(unittest.TestCase):
    """스캐너가 no-op 이 아님을 잠근다."""

    def test_strips_block_and_line_and_html_comments(self):
        src = (
            'const a = "안녕";\n'
            '// 주석에 있는 한글\n'
            '/* 블록 주석\n   여러 줄 */\n'
            'const b = `<!-- 마크업 주석 -->사람`;\n'
        )
        code = {i: c for i, c in inv.strip_comments(src)}
        self.assertIn("안녕", code[1])
        self.assertNotIn("주석", code[2])
        self.assertNotIn("블록", code[3])
        self.assertNotIn("여러", code[4])
        self.assertNotIn("마크업", code[5])
        self.assertIn("사람", code[5])

    def test_url_in_string_is_not_a_line_comment(self):
        code = {i: c for i, c in inv.strip_comments('const u = "https://a/한글";\n')}
        self.assertIn("한글", code[1])

    def test_regex_literal_does_not_swallow_the_rest_of_the_file(self):
        # 문자열 추적 토크나이저가 정확히 여기서 깨졌다: `/.../g` 를 나눗셈으로
        # 읽고 그 뒤를 통째로 문자열로 오인해 주석 400건을 위반으로 올렸다.
        src = 'const re = /[^a-z]+/g;\nconst c = "실제 카피";\n'
        code = {i: c for i, c in inv.strip_comments(src)}
        self.assertIn("실제 카피", code[2])


if __name__ == "__main__":
    unittest.main()

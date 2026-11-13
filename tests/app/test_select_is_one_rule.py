# -*- coding: utf-8 -*-
"""드롭다운의 모양이 **한 곳**에서만 정해지는가.

발주자 지시 2026-09-01: 데스크탑의 드롭다운을 전부 공홈의 역할 선택(`#ivRole`)과
같게 한다. 고치기 전 상태는 <select> 넷에 규칙 셋이었고, 넷째(`#space-pick`)에는
**규칙이 아예 없어** OS 기본 드롭다운이 그대로 떴다.

그 넷째가 이 검사의 이유다. 클래스로 칠하는 동안에는 새로 태어난 <select> 가
아무것도 안 걸친 채 뜨고, **아무 규칙도 깨지지 않으므로 아무도 못 본다**. 그래서
공용 규칙을 요소 선택자에 걸었고, 여기서 고정하는 것은 그 형태 자체다:

  ① 맨몸 `select` 규칙이 있다 (없으면 새 드롭다운이 다시 벌거벗는다)
  ② 다른 규칙이 모양을 **다시 적지 않는다** — 배치만 (안 그러면 목록이 또 갈린다)
  ③ 마크업이 인라인 style 로 공용 규칙을 비켜 가지 않는다
"""
from __future__ import annotations

import re
import unittest

from _src import SRC, read

#: ⚠ 주석을 먼저 걷는다. 안 걷으면 규칙 **앞**의 주석 본문이 선택자로 딸려 들어와
#: `select` 규칙이 "…주석… select" 로 읽히고, 세 검사가 한꺼번에 거짓 실패한다
#: (첫 판이 정확히 그랬다). 줄 수는 안 쓰므로 통째로 지워도 된다.
CSS = re.sub(r"/\*.*?\*/", "", read("app.css"), flags=re.S)

#: 모양을 정하는 선언. 배치(width·min-width·margin·flex 등)는 여기 없다 — 그것은
#: 규칙마다 달라도 되고, 실제로 세 곳이 각자 다른 이유로 갖고 있다.
LOOK = ("background", "border", "border-radius", "padding", "color", "font")


def _rules_matching_select() -> list[tuple[str, str]]:
    """`… select …{ … }` 형태의 규칙을 (선택자, 본문) 으로."""
    out = []
    for m in re.finditer(r"(?m)^([^{}/\n][^{}]*?)\{([^{}]*)\}", CSS):
        sel, body = m.group(1).strip(), m.group(2)
        if re.search(r"(^|[\s,>+~])select\b", sel):
            out.append((sel, body))
    return out


class OneRuleTests(unittest.TestCase):
    def test_맨몸_select_규칙이_있다(self):
        """이것이 사라지면 새 <select> 가 다시 OS 기본으로 뜬다 — 조용히."""
        sels = [s for s, _ in _rules_matching_select()]
        self.assertIn("select", sels,
                      f"맨몸 select 규칙이 없다. 있는 것: {sels}")

    def test_공용_규칙이_공홈과_같은_토큰을_쓴다(self):
        """정본은 공홈의 `#ivRole`: 카드 바탕 · hairline 테두리 · radius-sm."""
        body = next(b for s, b in _rules_matching_select() if s == "select")
        self.assertIn("hsl(var(--card))", body)
        self.assertIn("hsl(var(--border))", body)
        self.assertIn("--radius-sm", body)

    def test_다른_규칙은_모양을_다시_안_적는다(self):
        """네 번째 목록이 생기는 자리다. 배치는 얼마든지, 모양은 한 곳에서만."""
        bad = []
        for sel, body in _rules_matching_select():
            if sel == "select" or ":focus" in sel:
                continue
            for prop in LOOK:
                if re.search(rf"(?m)(^|[;{{])\s*{re.escape(prop)}\s*:", body):
                    bad.append(f"{sel} 이 {prop} 를 다시 적는다")
        self.assertEqual(bad, [], "; ".join(bad))

    def test_마크업이_인라인_style_로_비켜_가지_않는다(self):
        """인라인 style 은 공용 규칙을 무조건 이긴다 — 그러면 규칙이 하나여도 소용없다."""
        bad = []
        for f in SRC.rglob("*.ts"):
            for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                for m in re.finditer(r"<select\b[^>]*", line):
                    if "style=" in m.group(0):
                        bad.append(f"{f.name}:{n}")
        self.assertEqual(bad, [], f"인라인 style 을 단 <select>: {bad}")


if __name__ == "__main__":
    unittest.main()

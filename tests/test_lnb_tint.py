# -*- coding: utf-8 -*-
"""좌측 트리의 주제 색 — 깊이 잉크를 밟지 않고, 지도와 같은 토글을 쓴다.

색 자체는 `app/src/view-tint.ts` 의 `tintMap` 재사용이라 여기서 다시 재지 않는다.
이 파일이 지키는 것은 **붙이는 방식**이고, 셋 다 조용히 깨지는 종류다:

  ① 줄의 `style` 은 CSS 가 **부분문자열**로 읽는다(`[style*="--ink:0"]`). 주제 색을
     `--ink:N` **앞에** 끼우거나 그 리터럴을 다시 포맷하면 깊이 잉크가 통째로 죽는데
     타입체크도 게이트도 아무 말을 안 한다.
  ② 토글이 꺼지면 줄에 `--tint` 가 아예 안 실린다 — 그때 여태의 색으로 돌아가는 것은
     CSS 의 `var(--tint, <토큰>)` 대체값 하나뿐이다. 대체값을 지우면 도트가 사라진다.
  ③ 선택 표시(`--ring`)와 주제 색은 **같은 특이도**로 도트를 다툰다. 활성 규칙이
     뒤에 오는 것만이 그 다툼의 판정이다.

앱 쪽에는 JS 테스트 러너가 없다(`app/package.json` 에 `test` 없음) — `test_view_url.py`
와 같은 자리에 두는 이유가 그것이다."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "app" / "src"
LNB = (SRC / "lnb.ts").read_text(encoding="utf-8")
CSS = (SRC / "app.css").read_text(encoding="utf-8")


class LnbTintTests(unittest.TestCase):
    def test_주제_색은_깊이_잉크_뒤에_붙는다(self):
        """문서 줄과 노드 줄 **둘 다**. 한쪽만 고치면 갈래를 바꿀 때 색이 사라진다."""
        rows = re.findall(r'style="--lvl:\$\{depth\};--ink:\$\{ink\}([^"]*)"', LNB)
        self.assertEqual(len(rows), 2, "줄 두 갈래(docRow · nodeRow)가 다 있어야 한다")
        for tail in rows:
            self.assertEqual(tail, "${tintStyle(domain)}", "잉크 리터럴 뒤에만 붙인다")

    def test_토글이_꺼지면_색을_안_싣는다(self):
        """지도와 **같은 스위치**다 — 좌측 트리만 무조건 칠하면 토글이 거짓말이 된다."""
        self.assertIn("loadPrefs().domainTint", LNB)
        self.assertRegex(LNB, r"tints\?\.get\(")      # 없으면 조회가 아니라 강제다

    def test_CSS_는_토큰_대체값을_들고_색을_읽는다(self):
        for rule in ('border: 1px solid var(--tint, hsl(var(--caption-foreground)))',
                     'background: var(--tint, hsl(var(--muted-foreground)))'):
            self.assertIn(rule, CSS, "대체값이 없으면 토글을 껐을 때 도트가 사라진다")

    def test_활성_줄의_링이_주제_색을_이긴다(self):
        """특이도가 같으므로 **순서**가 판정이다."""
        tinted = CSS.index('background: var(--tint, hsl(var(--muted-foreground)))')
        on = CSS.index('.lnb-doc.on .lnb-dot')
        self.assertLess(tinted, on, "활성 규칙이 뒤에 와야 선택 표시가 살아남는다")


if __name__ == "__main__":
    unittest.main()

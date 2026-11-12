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
  ④ 줄에 실리는 것은 CSS 변수가 아니라 **그때 모드로 계산한 HEX** 다. 그래서 테마가
     뒤집혀도 도트는 스스로 안 따라오고, 다시 그려 주지 않으면 반대 모드 색이 그대로
     남아 배경에 묻는다(실측: 굳은 dark 틴트를 light 화면에 두면 1.44~2.32:1).

앱 쪽에는 JS 테스트 러너가 없다(`app/package.json` 에 `test` 없음) — `test_view_url.py`
와 같은 자리에 두는 이유가 그것이다."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "app" / "src"
LNB = (SRC / "lnb.ts").read_text(encoding="utf-8")
CSS = (SRC / "app.css").read_text(encoding="utf-8")
MAIN = (SRC / "main.ts").read_text(encoding="utf-8")


class LnbTintTests(unittest.TestCase):
    def test_주제_색은_깊이_잉크_뒤에_붙는다(self):
        """문서 줄(docRow)과 그래프 트리의 노드 줄(gnodeRow, 2026-11-05: 문서 트리와 같은 그림으로 노드를 나열한다)."""
        rows = re.findall(r'style="--lvl:\$\{depth\};--ink:\$\{ink\}([^"]*)"', LNB)
        self.assertEqual(len(rows), 2, "문서 줄(docRow)과 노드 줄(gnodeRow) 둘이어야 한다")
        for tail in rows:
            self.assertEqual(tail, "${tintStyle(domain)}", "잉크 리터럴 뒤에만 붙인다")

    def test_주제_색은_늘_켜져_있다(self):
        """토글이 없다(2026-09-26) — 기기별 저장값이 같은 그래프를 기기마다 다른 색으로 만들었다."""
        # 색 표는 앱 전체에 하나다(topic-tint-table.ts): 트리는 자기 주제를 더하고 그 표에서 읽는다.
        self.assertIn("feedTints((tree?.domains || []).map((g) => g.domain));", LNB)
        self.assertIn("topicTint(domain, themeMode())", LNB)
        self.assertNotIn("domainTint", LNB)

    def test_주제_색_표를_짓는_곳은_하나다(self):
        """트리와 나침반이 각자 tintPlan 을 돌리면 넣는 주제 집합이 달라 한 주제가 두 색이 된다."""
        compass = (SRC / "surfaces" / "view-compass.ts").read_text(encoding="utf-8")
        table = (SRC / "topic-tint-table.ts").read_text(encoding="utf-8")
        for name, src in (("lnb.ts", LNB), ("view-compass.ts", compass)):
            self.assertNotIn("tintPlan(", src, f"{name} 가 색 표를 따로 짓는다")
        self.assertIn("tintPlan([...known], mode)", table)
        self.assertIn("addTopics(ctx.store ?? '', topics.keys());", compass)

    def test_CSS_는_토큰_대체값을_들고_색을_읽는다(self):
        for rule in ('border: 1px solid var(--tint, hsl(var(--muted-foreground)))',
                     'background: var(--tint, hsl(var(--muted-foreground)))'):
            self.assertIn(rule, CSS, "대체값이 없으면 토글을 껐을 때 도트가 사라진다")

    def test_활성_줄의_링이_주제_색을_이긴다(self):
        """특이도가 같으므로 **순서**가 판정이다."""
        tinted = CSS.index('background: var(--tint, hsl(var(--muted-foreground)))')
        on = CSS.index('.lnb-doc.on .lnb-dot')
        self.assertLess(tinted, on, "활성 규칙이 뒤에 와야 선택 표시가 살아남는다")

    def test_테마가_뒤집히면_트리를_다시_그린다(self):
        """④ — 그리고 **`applyTheme` 안**이어야 한다.

        설정 화면의 클릭 핸들러에 달면 테마 `pref` 가 `"auto"` 인 경로가 통째로 샌다:
        그때 모드를 바꾸는 것은 OS 이고(아래 matchMedia 구독) 설정 클릭이 없다.
        `applyTheme` 는 `data-theme` 를 쓰는 **유일한** 곳이라 두 경로가 여기서 만난다."""
        head = MAIN.index("export function applyTheme() {")
        body = MAIN[head:MAIN.index(chr(10) + "}", head)]
        self.assertIn("repaintLnb()", body,
                      "테마를 뒤집고 안 그리면 도트에 반대 모드 색이 남는다")
        self.assertIn(
            'matchMedia("(prefers-color-scheme: dark)").addEventListener("change", applyTheme)',
            MAIN, "OS 추종 경로가 applyTheme 를 거치지 않으면 위 보장이 반쪽이 된다")

    def test_지도를_먼저_다시_칠하고_트리를_그린다(self):
        """지도와 트리는 같은 표를 지금 모드로 읽는다. 지도를 먼저 다시 칠해 두 표면이 같은 모드의 색으로 함께 바뀌게 한다."""
        head = MAIN.index("export function applyTheme() {")
        body = MAIN[head:MAIN.index(chr(10) + "}", head)]
        self.assertLess(body.index("repaintView();"), body.index("repaintLnb();"))

    def test_Agent_색은_그릴_때마다_지금_모드로_매긴다(self):
        """Agent · 정책 색은 모드마다 다른 HEX 다. 불러올 때 한 번 매기면 테마를 뒤집어도 옛 모드 색이 남는다."""
        self.assertNotRegex(LNB, r"\n\s*agentCol = d \?", "불러오는 자리에서 색을 굳히지 않는다")
        self.assertIn("colourAgents = () => { agentCol = d ? m.agentColours(d.agents) : new Map(); polCol = m.policyColour(); };", LNB)
        paint = LNB[LNB.index("function paint() {"):]
        self.assertIn("colourAgents?.();", paint[:paint.index("body.innerHTML = paintAgentTree();")])


if __name__ == "__main__":
    unittest.main()

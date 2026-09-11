# -*- coding: utf-8 -*-
"""`renderMarkdown` 이 이스케이프를 **먼저** 한다 — `gate_escape` 의 신뢰가 여기 걸려 있다.

2026-09-11 에 `scripts/gate_escape.py` 의 SAFE 목록에 `renderMarkdown` 을 넣었다. 노드
상세가 한 줄 요약을 마크다운으로 그리는데 `esc()` 로 감싸면 마크다운이 글자로 보이기
때문이다. 그 순간부터 **그 함수가 이스케이프한다는 사실이 게이트의 전제**가 됐고,
전제는 시험이 붙잡지 않으면 다음 리팩터에 조용히 사라진다.

TypeScript 를 실행하지 않고 **소스를 읽어** 판정한다. 앱에 파이썬 쪽 테스트 러너가 없고,
여기서 지킬 것은 동작이 아니라 **구조**이기 때문이다: 태그를 만드는 보간이 이스케이프를
지나는가. 동작 시험이었다면 새 문법 하나가 늘 때마다 같은 것을 다시 확인해야 한다.
"""
from __future__ import annotations

import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "app", "src", "doc-editor.ts")


def body() -> str:
    with open(SRC, encoding="utf-8") as fh:
        s = fh.read()
    i = s.index("export function renderMarkdown")
    j = s.index("\nexport ", i + 10)
    return s[i:j]


class RenderMarkdownEscapes(unittest.TestCase):
    def test_함수가_자기_이스케이프를_갖는다(self):
        b = body()
        self.assertRegex(b, r"const esc\s*=", "지역 esc 가 사라졌다 — 게이트의 전제가 깨진다")
        for ch in ("&amp;", "&lt;", "&gt;", "&quot;"):
            self.assertIn(ch, b, f"{ch} 치환이 없다")

    def test_속성_자리는_전부_이스케이프를_지난다(self):
        """속성은 본문보다 나쁘다 — 따옴표 하나로 빠져나가면 핸들러를 붙일 수 있다."""
        sinks = re.findall(r'[a-zA-Z-]+="[^"]*\$\{([^}]+)\}', body())
        self.assertTrue(sinks, "속성 보간이 하나도 없다 — 패턴이 낡았는지 볼 것")
        for s in sinks:
            self.assertIn("esc(", s, f"속성에 이스케이프 없는 보간: {s!r}")

    def test_태그를_만드는_보간도_이스케이프를_지난다(self):
        """`inline(esc(x))` 처럼 한 겹 감싼 것도 통과다 — `inline` 은 이미 이스케이프된
        문자열만 받는다(그것이 이 함수의 내부 계약이다)."""
        bad = []
        for m in re.finditer(r"<[a-z]+[^>]*>\$\{([^}]+)\}", body()):
            expr = m.group(1).strip()
            # `c` 는 `inline()` 안쪽의 콜백 인자라 이미 이스케이프된 값이다.
            if expr in ("c",) or "esc(" in expr:
                continue
            bad.append(expr)
        self.assertEqual(bad, [], f"이스케이프를 안 지나는 보간: {bad}")

    def test_게이트가_이_함수를_신뢰하고_있다(self):
        """신뢰가 실제로 걸려 있는지 확인한다 — 목록에서 빠지면 이 파일의 이유가 사라진다.

        ⚠ **`SAFE = ` 줄만 본다.** 처음 판은 파일 전체에서 이름을 찾았는데, 그 위에 내가
        쓴 주석이 같은 이름을 담고 있어서 **목록에서 빼도 초록이었다**(변이로 확인).
        게이트의 규칙은 정규식 한 줄이고, 시험은 그 줄을 봐야 한다."""
        with open(os.path.join(ROOT, "scripts", "gate_escape.py"), encoding="utf-8") as fh:
            rule = [l for l in fh if l.startswith("SAFE = ")]
        self.assertEqual(len(rule), 1, f"SAFE 규칙 줄을 하나로 못 찾았다: {len(rule)}개")
        self.assertIn("renderMarkdown", rule[0],
                      "gate_escape 의 SAFE 에서 빠졌다면 이 시험도 함께 정리할 것")


if __name__ == "__main__":
    unittest.main()

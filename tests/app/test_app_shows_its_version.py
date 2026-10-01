# -*- coding: utf-8 -*-
"""앱이 **화면에** 자기 버전을 적는가.

🔴 2026-09-11. 웹 Vinc 가 0.9.1 인지 아닌지를 놓고 한나절이 갔다. 서빙되던 번들에는
`0.9.1+491d92e` 가 박혀 있었고 배포 로그도 같은 말을 했는데, 그 값이 **사이드카 진단
문자열에만** 쓰여서 화면을 보고는 판정할 방법이 없었다. 코드가 옳은데 확인할 수 없는
상태가 하루를 먹었다.

그래서 이 시험이 잠그는 것은 상수가 아니라 **화면에 나오는가**다. 0.9.1 의 회고가 같은
말을 한 적이 있다 — "테스트가 '부르는가'만 잠그고 '화면에 나오는가'는 안 잠갔다"(P4).
`buildId` 를 import 만 하고 안 쓰면 typecheck 도 통과한다.

TypeScript 를 실행하지 않고 소스를 읽는다. `tests/app/test_shell_size_spec.py` 와 같은 방식이고,
여기서 지킬 것이 동작이 아니라 **자리**이기 때문이다.
"""
from __future__ import annotations

import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SETTINGS = os.path.join(ROOT, "app", "src", "surfaces", "settings.ts")


def src() -> str:
    with open(SETTINGS, encoding="utf-8") as fh:
        return fh.read()


def shell_body() -> str:
    """`function shell()` 의 본문. 버전 줄은 **탭이 아니라 셸**에 있어야 한다 —
    탭 구성이 웹과 데스크탑에서 다르다(`WEB` 이 Connections·Advanced 를 숨긴다)."""
    s = src()
    i = s.index("function shell()")
    j = s.index("\n  function renderBody()", i)
    return s[i:j]


class VersionOnScreen(unittest.TestCase):
    def test_buildId_를_가져온다(self):
        self.assertRegex(src(), r'import\s*\{[^}]*\bbuildId\b[^}]*\}\s*from\s*"\.\./api"')

    def test_셸이_그것을_실제로_그린다(self):
        """import 만 하고 안 쓰면 typecheck 는 통과한다 — 그 구멍을 여기서 막는다."""
        body = shell_body()
        self.assertIn("buildId", body, "settings 셸이 버전을 그리지 않는다")
        self.assertRegex(body, r"\$\{esc\(buildId\)\}", "이스케이프를 지나야 한다")

    def test_탭_안이_아니라_셸에_있다(self):
        """탭 안에 두면 그 탭이 없는 빌드에서는 버전이 안 보인다(웹에서 두 탭이 숨는다)."""
        s = src()
        for fn in ("function renderAccount", "function renderConnections", "function renderAdvanced"):
            i = s.index(fn)
            j = s.find("\n  function ", i + 10)
            self.assertNotIn("buildId", s[i:j if j > 0 else len(s)],
                             f"{fn} 안에 있다 — 셸로 옮길 것")

    def test_그릴_자리에_스타일이_있다(self):
        """클래스만 있고 규칙이 없으면 줄이 본문과 구분되지 않는다."""
        with open(os.path.join(ROOT, "app", "src", "app.css"), encoding="utf-8") as fh:
            css = fh.read()
        self.assertIn(".settings-ver", css)

    def test_번역하지_않는다(self):
        """값이 버전 문자열이라 언어가 없다. 덱에 키를 늘리면 두 덱이 갈릴 자리만 는다."""
        self.assertNotRegex(shell_body(), r"U\.\w*[Vv]ersion")


if __name__ == "__main__":
    unittest.main()

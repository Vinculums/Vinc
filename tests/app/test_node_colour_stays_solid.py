# -*- coding: utf-8 -*-
"""노드는 어떤 상태에서도 제 색 그대로다.

정본: [[decision:a-node-keeps-its-own-colour-in-every-state]].

같은 노드가 어떤 때는 진한 색, 어떤 때는 옅은 틴트로 보였다. 틴트 팔레트가 따로
있던 것이 아니라 한 가지 색이 상태에 따라 배경 쪽으로 섞였다 — hover·점등의
물러남(배경색 혼합), 거리 안개,
그리고 주제 하나로 새로 지은 색상표(노드 카드). 이제 물러나는 것은 엣지와 라벨뿐이다.

앱에는 JS 테스트 러너가 없어(`app/package.json` 에 `test` 없음) 이 리포의 관례대로
소스를 대조한다(`test_view_url.py` · `test_lnb_tint.py` 와 같은 자리)."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app" / "src"


def _read(rel: str) -> str:
    return (APP / rel).read_text(encoding="utf-8")


def _fn(src: str, head: str) -> str:
    """`head` 로 시작하는 함수(또는 화살표) 본문 — **같은 들여쓰기**의 다음 선언 전까지.
    안쪽 줄은 더 깊이 들여써 있으므로 거기서 끊기지 않는다."""
    at = src.index(head)
    indent = at - (src.rfind("\n", 0, at) + 1)
    rest = src[at:]
    nxt = re.search(r"\n" + " " * indent + r"(const|function|let|export|type|g\.on|/\*\*)",
                    rest[len(head):])
    return rest if not nxt else rest[:len(head) + nxt.start()]


class CardTests(unittest.TestCase):
    def test_노드_카드는_지도와_같은_색상표를_읽는다(self):
        src = _read("surfaces/view.ts")
        self.assertNotIn("tintMap([nd.domain]", src)
        self.assertIn("shownTints?.get(nd.domain.trim())", src)
        # 표는 setCurNodes 한 곳에서 전량으로 짓는다 — 지도가 그 뒤에 마운트된다.
        body = _fn(src, "function setCurNodes(")
        self.assertIn("tintMap(next.map((n) => n.domain), curMode())", body)

    def test_범례가_노드_물러남을_가르치지_않는다(self):
        self.assertNotIn("tierNode", _read("copy.ts"))
        self.assertNotIn("tierNode", _read("surfaces/view.ts"))


if __name__ == "__main__":
    unittest.main()

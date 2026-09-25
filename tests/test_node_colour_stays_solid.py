# -*- coding: utf-8 -*-
"""노드는 어떤 상태에서도 제 색 그대로다.

정본: [[decision:a-node-keeps-its-own-colour-in-every-state]].

같은 노드가 어떤 때는 진한 색, 어떤 때는 옅은 틴트로 보였다. 틴트 팔레트가 따로
있던 것이 아니라 한 가지 색이 상태에 따라 배경 쪽으로 섞였다 — hover·점등의
물러남(3D 는 배경색 혼합, 2D 는 mixFloor, 타임라인·번들은 opacity), 3D 거리 안개,
그리고 주제 하나로 새로 지은 색상표(노드 카드). 이제 물러나는 것은 엣지와 라벨뿐이다.

앱에는 JS 테스트 러너가 없어(`app/package.json` 에 `test` 없음) 이 리포의 관례대로
소스를 대조한다(`test_view_url.py` · `test_lnb_tint.py` 와 같은 자리)."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app" / "src"


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


class ThreeDTests(unittest.TestCase):
    def setUp(self):
        self.src = _read("surfaces/view3d.ts")

    def test_물러남은_라벨만_건드린다(self):
        body = _fn(self.src, "const applyFade = ")
        self.assertIn("instanceof THREE.Sprite", body)
        self.assertNotIn("mixTarget", body)
        self.assertNotIn("proxy", body)

    def test_몸통은_배경색으로_섞이지_않는다(self):
        self.assertNotIn("instanceFade", self.src)
        self.assertNotIn("uFadeColor", self.src)

    def test_몸통은_안개를_받지_않는다(self):
        """깊이 단서는 엣지의 안개가 진다. 몸통이 안개를 받으면 뒤쪽 노드가 옅어진다."""
        for head in ("function instMaterial(", "function skinFor(", "function gatewayWire("):
            self.assertIn("fog: false", _fn(self.src, head), head)


class TwoDTests(unittest.TestCase):
    def setUp(self):
        self.src = _read("surfaces/view2d.ts")

    def test_물러난_노드는_색을_바꾸지_않는다(self):
        reducer = self.src[self.src.index('sigma.setSetting("nodeReducer"'):]
        reducer = reducer[:reducer.index("\n  });")]
        self.assertNotIn("mixFloor", reducer)
        self.assertIn('label: ""', reducer)

    def test_색상표는_받은_노드_전부로_짓는다(self):
        """고아 숨김으로 주제 하나가 빠지면 나머지 주제의 색이 한 칸씩 밀린다."""
        self.assertNotIn("tintMap(shown.", self.src)
        self.assertIn("tintMap(nodes.map((n) => n.domain), mode())", self.src)


class PlanarTests(unittest.TestCase):
    def test_노드_마크는_opacity_로_물러나지_않는다(self):
        css = _read("app.css")
        self.assertNotIn("--recede-node", css)
        self.assertIsNone(re.search(r"^\.pl-fade\s*\{", css, re.M),
                          "`.pl-fade` 가 노드 그룹 전체를 흐리면 마크가 틴트가 된다")
        # 이름은 여전히 물러난다.
        self.assertIn(".pl-fade .pl-lab", css)

    def test_recede_에_node_가_없다(self):
        self.assertNotIn('"node"', _read("recede.ts").split("export type RecedeKind")[1]
                         .split(";")[0])


class CardTests(unittest.TestCase):
    def test_노드_카드는_지도와_같은_색상표를_읽는다(self):
        src = _read("surfaces/view.ts")
        self.assertNotIn("tintMap([nd.domain]", src)
        self.assertIn("shownTints?.get(nd.domain.trim())", src)
        # 표는 setCurNodes 한 곳에서 전량으로 짓는다 — 모든 관점이 그 뒤에 마운트된다.
        body = _fn(src, "function setCurNodes(")
        self.assertIn("tintMap(next.map((n) => n.domain), curMode())", body)
        self.assertIn("const planarTints = shownTints;", src)

    def test_3D_는_전량_주제로_순위를_매긴다(self):
        """3D 가 받는 노드는 고아 숨김으로 걸러진 것이다. 거기서 순위를 매기면 주제
        하나가 빠질 때 나머지 색이 밀린다."""
        self.assertIn("keepCam, curNodes.map((n) => n.domain))", _read("surfaces/view.ts"))
        self.assertIn("tintMap(tintDomains ?? nodes.map((n) => n.domain), themeMode())",
                      _read("surfaces/view3d.ts"))

    def test_범례가_노드_물러남을_가르치지_않는다(self):
        self.assertNotIn("tierNode", _read("copy.ts"))
        self.assertNotIn("tierNode", _read("surfaces/view.ts"))


if __name__ == "__main__":
    unittest.main()

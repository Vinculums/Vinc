"""그래프: 지도에서 고른 노드는 카드와 옆 칸을 함께 열고, 영역 부피마다 이름표가 선다.

소스 잠금이다. 동작은 데모 빌드를 헤드리스 브라우저로 눌러 확인했다(지도 클릭 → 카드 하나 ·
옆 칸 · `#view/trace/<id>`, 다크 · 라이트에서 영역 이름표 다섯). 여기서는 그 모양이 조용히 옛
자리로 돌아가지 않게 붙잡는다:

- 지도 클릭이 다시 카드 없이 열리면 사이드바에서 고른 것과 지도에서 고른 것이 다르게 보인다.
- 이름표 텍스처가 sRGB 로 표시되지 않으면 렌더러가 한 번 더 밝혀 보석색이 바랜다.
- 이름표가 깊이 · 안개를 따르면 뒤쪽 영역의 이름이 부피에 가려지거나 흐려진다.

근거: [[decision:a-node-picked-in-the-graph-opens-its-card-and-the-side-pane-2026-10-29]]
근거: [[decision:each-area-volume-carries-its-name-at-its-area-topic-2026-10-29]]
"""
from __future__ import annotations

import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "app" / "src" / "surfaces"
VIEW = (SRC / "view.ts").read_text(encoding="utf-8")
VIEW3D = (SRC / "view3d.ts").read_text(encoding="utf-8")


def _block(src: str, start: str, end: str) -> str:
    a = src.index(start)
    return src[a:src.index(end, a)]


class MapClick(unittest.TestCase):
    def test_a_map_click_opens_the_card_and_the_side_pane(self):
        body = _block(VIEW, "function openFromMap(", "\n  }")
        self.assertIn("focusOnMap(nd, true);", body)
        self.assertIn("railFor(nd);", body)

    def test_the_click_handler_still_routes_through_it(self):
        self.assertIn("else openFromMap(nd);", VIEW)


class AreaNames(unittest.TestCase):
    def test_each_volume_gets_a_name(self):
        build = _block(VIEW3D, "const buildVolumes = () => {", "const renderedRadii")
        self.assertIn("areaLabelSprite(text, volumeColor(area), box.rc * .2, pal)", build)
        # 대표 주제 노드(경로가 가장 짧은 주제 마크) 위에 선다.
        self.assertIn('n.kind !== "domain"', build)
        self.assertIn('topicOf(n).split("/").length < topicOf(best).split("/").length', build)

    def test_the_name_keeps_its_jewel_and_stays_readable(self):
        sprite = _block(VIEW3D, "function areaLabelSprite(", "\n}\n")
        self.assertIn("tex.colorSpace = THREE.SRGBColorSpace;", sprite)
        self.assertIn("depthTest: false, fog: false, toneMapped: false", sprite)

    def test_a_theme_switch_repaints_the_names(self):
        self.assertIn("const fresh = areaLabelSprite(v.text, volumeColor(v.area), v.box.rc * .2, pal);", VIEW3D)


if __name__ == "__main__":
    unittest.main()

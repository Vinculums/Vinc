# -*- coding: utf-8 -*-
"""노드 3D 표식 정본(`scripts/design/mark3d-art.json`)의 모양 시험.

이 파일을 두 리포가 읽는다. 사이트 리포의 `scripts/gate_node_marks.mjs` 가 사이트 갤러리의 사본을
이 파일과 글자 대조하고, 이 시험은 정본 쪽이 그 대조가 믿는 모양을 지키는지 본다: 종류 여덟 개가 다
있고, 각 조각이 닫힌 SVG 요소이며, 잉크는 `currentColor` 이고 흰색(조명) 말고 직접 쓴 색이 없다.
근거: [[vinc.research.graph-vinc-orb-2026-10-28]]
"""
from __future__ import annotations

import json
import re
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ART_FILE = ROOT / "scripts" / "design" / "mark3d-art.json"
KINDS = {"concept", "decision", "record", "episode", "bridge", "topic", "area", "gateway"}
#: 사이트 사본이 베끼는 종류. 정본에서 빠지면 그쪽 게이트가 "앱에 없는 이름"으로 떨어진다.
SITE_COPIES = {"concept", "episode", "record", "decision"}


def load() -> dict[str, str]:
    return json.loads(ART_FILE.read_text(encoding="utf-8"))["art"]


class MarkArtShape(unittest.TestCase):
    def test_종류_여덟이_다_있다(self):
        self.assertEqual(set(load()), KINDS)
        self.assertTrue(SITE_COPIES <= set(load()))

    def test_조각마다_닫힌_svg_요소다(self):
        for kind, art in load().items():
            # 24x24 칸 안의 조각이라 감싸서 읽는다. 열린 태그나 깨진 속성이면 여기서 떨어진다.
            root = ET.fromstring(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">{art}</svg>')
            self.assertTrue(len(root), f"{kind}: 빈 조각")

    def test_색은_currentColor_와_조명_흰색뿐이다(self):
        for kind, art in load().items():
            colours = set(re.findall(r'(?:fill|stroke|stop-color)="([^"]*)"', art))
            self.assertTrue(colours <= {"currentColor", "none", "#fff", "url(#__GID__)"}, f"{kind}: {colours}")
            self.assertNotRegex(art, r"#[0-9a-fA-F]{3,8}(?<!#fff)\b", kind)

    def test_그라디언트_id_는_자리표시자_하나다(self):
        for kind, art in load().items():
            ids = set(re.findall(r'id="([^"]*)"', art))
            refs = set(re.findall(r"url\(#([^)]*)\)", art))
            self.assertTrue(ids <= {"__GID__"}, f"{kind}: {ids}")
            self.assertTrue(refs <= ids, f"{kind}: 정의 없는 참조 {refs - ids}")

    def test_앱에는_이_표식을_그리는_파일이_없다(self):
        # 정본이 데이터 파일로 옮겨 왔다: 같은 이름의 소스 사본이 다시 생기면 정본이 둘이 된다.
        self.assertFalse((ROOT / "app" / "src" / "surfaces" / "view-planar.ts").exists())
        for fp in (ROOT / "app" / "src").rglob("*.ts"):
            self.assertNotIn("MARK3D_ART", fp.read_text(encoding="utf-8"), str(fp))


if __name__ == "__main__":
    unittest.main()

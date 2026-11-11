# -*- coding: utf-8 -*-
"""데스크탑 앱 아이콘은 검정 판 위의 음영 링(D2)이다(로고 판 금지의 유일한 예외).

투명 검정 링은 어두운 작업표시줄·Dock 에서 사라진다. 그래서 OS 가 그리는 앱 아이콘만
판을 쓰고, 파비콘·메일·MCP 커넥터 아이콘은 투명 링 그대로다.
근거: [[decision:the-desktop-app-icon-keeps-a-black-plate-2026-09-26]] · [[vinc.design.logo]]

생성기(`scripts/design/build_logo.mjs`)와 그것이 뽑은 **실제 PNG** 를 함께 본다.
CI 는 `npm run logo:check` 를 돌리지 않으므로, 옛 생성기로 아이콘을 다시 뽑아 커밋하면
여기서만 드러난다.
"""
from __future__ import annotations

import struct
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ICONS = ROOT / "app" / "src-tauri" / "icons"


def _rgba(path: Path) -> tuple[int, int, list[bytes]]:
    """8비트 RGBA PNG 를 행 목록으로 푼다(표준 라이브러리만)."""
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", path
    at, idat = 8, b""
    w = h = 0
    while at < len(data):
        n, kind = struct.unpack(">I4s", data[at:at + 8])
        body = data[at + 8:at + 8 + n]
        if kind == b"IHDR":
            w, h, depth, colour = struct.unpack(">IIBB", body[:10])
            assert (depth, colour) == (8, 6), f"{path.name}: 8비트 RGBA 가 아니다"
        elif kind == b"IDAT":
            idat += body
        at += 12 + n
    raw, stride, rows, prev = zlib.decompress(idat), w * 4, [], bytes(w * 4)
    for y in range(h):
        f, line = raw[y * (stride + 1)], bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for x in range(stride):
            a = line[x - 4] if x >= 4 else 0
            b, c = prev[x], prev[x - 4] if x >= 4 else 0
            if f == 1:
                line[x] = (line[x] + a) & 255
            elif f == 2:
                line[x] = (line[x] + b) & 255
            elif f == 3:
                line[x] = (line[x] + (a + b) // 2) & 255
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        rows.append(bytes(line))
        prev = rows[-1]
    return w, h, rows


def _px(rows: list[bytes], x: int, y: int) -> tuple[int, ...]:
    return tuple(rows[y][x * 4:x * 4 + 4])


class AppIconPlateTests(unittest.TestCase):
    def test_생성기가_앱_아이콘에만_판을_씌운다(self):
        src = (ROOT / "scripts" / "design" / "build_logo.mjs").read_text(encoding="utf-8")
        self.assertIn('<rect width="180" height="180" rx="40" fill="#000000"/>', src)
        self.assertIn("writeFileSync(source, plated)", src, "Tauri 아이콘의 원본이 판 SVG 가 아니다")
        # 판 위의 링은 음영판(D2)이다. 파비콘은 micro 벌의 투명 링, MCP 래스터는 기본 벌의 투명 링이다.
        self.assertIn("shaded/vinc-ring-d2-on-black.svg", src)
        self.assertIn("png(tightMicro, 48)", src)
        self.assertIn("png(tight, 128)", src)

    def test_앱_아이콘_PNG_에_검정_판이_있다(self):
        """가장자리 가운데는 판(불투명 검정), 중심은 밝은 무채색 구체(D2 음영), 모서리는 둥글려 투명하다."""
        for name in ("icon.png", "32x32.png", "128x128.png"):
            with self.subTest(name=name):
                w, h, rows = _rgba(ICONS / name)
                r, g, b, a = _px(rows, w // 2, 1)
                self.assertGreater(a, 200, "위 가장자리가 투명하다 — 판이 없다")
                self.assertLess(max(r, g, b), 40, "판이 검정이 아니다")
                r, g, b, a = _px(rows, w // 2, h // 2)
                self.assertGreater(min(r, g, b), 150, "가운데 구체가 밝지 않다")
                self.assertGreater(a, 200, "가운데 구체가 투명하다")
                self.assertLess(max(r, g, b) - min(r, g, b), 8, "가운데 구체가 무채색이 아니다")
                self.assertLess(_px(rows, 0, 0)[3], 60, "모서리가 둥글지 않다")


if __name__ == "__main__":
    unittest.main()

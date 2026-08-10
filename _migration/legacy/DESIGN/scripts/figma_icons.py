#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Figma 임포트용 아이콘 변환기 — `currentColor` → 정본 잉크 hex.

## 왜 필요한가 (근본 원인)
`assets/icons/` 의 원본 SVG 는 모두 `stroke="currentColor"` / `fill="currentColor"`
로 색을 그린다. 이건 **WebView(Tauri UI)에서는 정상**이다 — 소비 측 CSS `color`
(= `--foreground` 잉크 토큰)를 상속하기 때문. 그러나 **Figma 는 `currentColor` 를
해소하지 못한다**(CSS 캐스케이드 컨텍스트가 없음). 그 결과 임포트 시:

  1. `currentColor` 가 순검정(#000)으로 폴백되거나(→ D-S02 순검정 금지 위반), 또는
  2. 페인트가 아예 드롭돼 도형이 **투명/사라짐** — 즉 "아이콘이 깨진다".

## 고치는 법
Figma 로 가져갈 사본에서만 `currentColor` 를 **정본 잉크 hex**(primitive.ink =
`#14110D`, 라이트)로 치환한다. 원본 SVG(currentColor)는 그대로 둔다 — 코드/WebView
소비 경로의 SSoT 이므로. Figma 에서는 임포트 후 이 hex 를 `ink` 색 변수에 바인딩하면
토큰과 다시 연동된다(다크는 `d_text` = `#EAE5DD`).

## 사용
    python DESIGN/scripts/figma_icons.py                 # 라이트(ink) 변형 생성
    python DESIGN/scripts/figma_icons.py --theme dark    # 다크(d_text) 변형
    python DESIGN/scripts/figma_icons.py --check         # 원본에 currentColor 잔존 검사(0 exit)

stdlib 전용(디자인 엔진과 동일 규약 — 순수 Python, 의존성 0).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DESIGN = HERE.parent                      # Vinc-docs/DESIGN
TOKENS = DESIGN / "design-tokens.json"
SRC = DESIGN / "assets" / "icons"
OUT = DESIGN / "assets" / "icons-figma"

# 테마 → primitive 토큰(아이콘 잉크). Figma 는 단색이라 테마별 사본을 낸다.
THEME_PRIMITIVE = {"light": "ink", "dark": "d_text"}


def _ink_hex(theme: str) -> str:
    """정본(design-tokens.json)에서 테마별 아이콘 잉크 hex 를 읽는다(SSoT)."""
    tokens = json.loads(TOKENS.read_text(encoding="utf-8"))
    prim = tokens["primitive"][THEME_PRIMITIVE[theme]]
    return prim["$value"]


def _to_figma(svg: str, hex_color: str) -> str:
    """currentColor 를 구체 hex 로 치환(대소문자 무시). 그 외 마크업은 불변."""
    return re.sub(r"currentColor", hex_color, svg, flags=re.IGNORECASE)


def check() -> int:
    """원본에 currentColor 가 남아있는지(WebView SSoT 규약) 확인 — 리포트만."""
    files = sorted(SRC.glob("*.svg"))
    with_cc = [f.name for f in files if "currentColor" in f.read_text(encoding="utf-8")]
    print(f"[check] 원본 {len(files)}개 중 currentColor 사용 {len(with_cc)}개 "
          f"(WebView 소비 경로 정상 — Figma 는 별도 변환 필요).")
    return 0


def generate(theme: str) -> int:
    hex_color = _ink_hex(theme)
    files = sorted(SRC.glob("*.svg"))
    if not files:
        print(f"[error] 원본 아이콘 없음: {SRC}", file=sys.stderr)
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    n = 0
    for f in files:
        figma_svg = _to_figma(f.read_text(encoding="utf-8"), hex_color)
        if "currentColor" in figma_svg:   # 방어: 치환 누락 없어야 함
            print(f"[warn] {f.name}: currentColor 잔존", file=sys.stderr)
        (OUT / f.name).write_text(figma_svg, encoding="utf-8")
        n += 1
    print(f"[ok] {n}개 아이콘 → {OUT.relative_to(DESIGN.parent)} "
          f"(currentColor → {hex_color}, theme={theme})")
    print("    Figma: 이 폴더를 드래그 임포트 → 각 프레임을 컴포넌트화(ic/*) → "
          "fill 을 'ink' 색 변수에 바인딩(토큰 재연동).")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Figma 임포트용 아이콘 변환(currentColor → ink hex)")
    ap.add_argument("--theme", choices=("light", "dark"), default="light")
    ap.add_argument("--check", action="store_true", help="원본 currentColor 사용 리포트만")
    args = ap.parse_args(argv)
    if args.check:
        return check()
    return generate(args.theme)


if __name__ == "__main__":
    raise SystemExit(main())

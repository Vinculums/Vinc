# -*- coding: utf-8 -*-
"""scope_tokens.py — tokens.css의 셀렉터를 스킨 속성으로 스코핑한다.

웹 Vinc/데스크탑 앱은 같은 번들에서 런타임 `data-skin`으로 테마를 고른다.
데스크탑 토큰은 :root 기본, 웹 토큰은 이 스크립트로 `:root[data-skin="web"]`
스코프를 입혀 함께 로드한다(속성 셀렉터가 우선).

usage: python -X utf8 scripts/scope_tokens.py --src DESIGN/tokens.css --out dist/app/tokens.web.css --skin web
"""
from __future__ import annotations
import argparse, pathlib, sys

def scope(css: str, skin: str) -> str:
    out = []
    for line in css.splitlines():
        if line.startswith(":root {"):
            out.append(f':root[data-skin="{skin}"] {{')
        elif line.startswith('[data-theme="dark"] {'):
            out.append(f':root[data-skin="{skin}"][data-theme="dark"] {{')
        else:
            out.append(line)
    header = (f"/* AUTO-GENERATED — scope_tokens.py (skin={skin}). Do not hand-edit.\n"
              f"   원본: 허브 tokens.css → data-skin 스코프 재작성. */\n")
    return header + "\n".join(out) + ("\n" if not css.endswith("\n") else "")

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--skin", default="web")
    a = ap.parse_args()
    src = pathlib.Path(a.src).read_text(encoding="utf-8")
    result = scope(src, a.skin)
    outp = pathlib.Path(a.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(result, encoding="utf-8", newline="\n")
    print(f"Generated: {outp}")
    return 0

if __name__ == "__main__":
    sys.exit(main())

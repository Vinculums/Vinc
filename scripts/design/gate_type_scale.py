#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""글씨 크기 게이트 — 앱의 글씨 크기는 토큰(--fs-*)에서 온다.

    python -X utf8 scripts/design/gate_type_scale.py          # app/src 검사
    python -X utf8 scripts/design/gate_type_scale.py <dir>    # 다른 뿌리

exit 0=통과 · 1=위반.

무엇을 보나:

  ✅ CSS(`*.css`)와 TS(`*.ts`, 인라인 style 문자열)의 `font-size:` 값이
     `var(--fs-<이름>)` 이고 그 이름이 토큰 생성물(`tokens.desktop.css`)에 정의돼 있다.
  ✅ `em`·`%`·`inherit`·`calc(...)` 처럼 부모를 따르는 값은 체계 밖이 아니다.
  ✅ 숫자(rem·px)를 직접 쓴 줄은 위반이다. 정당하면 그 줄에 `fs-allow: <이유>` 주석을 단다
     — 예외는 **건수와 이유가 늘 출력된다**(조용한 예외를 만들지 않는다, `gate_surface` 의
     `ds-allow` 와 같은 방식).
  ✅ 정의되지 않은 토큰(`var(--fs-md, 1rem)` 처럼 대체값으로 조용히 넘어가는 것)도 위반이다.
  ❌ 생성물(토큰 사본)과 동기화 사본(`vinc-loaders.css`)은 보지 않는다. 그 정본은 따로 있다.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT = ROOT / "app" / "src"
TOKENS = "tokens.desktop.css"
SKIP = {"tokens.desktop.css", "tokens.web.css", "vinc-loaders.css"}

DECL = re.compile(r"font-size\s*:\s*([^;}\"'`]+)")
LITERAL = re.compile(r"^-?[0-9]*\.?[0-9]+(rem|px)$")
VAR = re.compile(r"^var\(--fs-([a-z0-9-]+)(\s*,[^)]*)?\)$")
RELATIVE = re.compile(r"^(-?[0-9]*\.?[0-9]+(em|%)|inherit|initial|unset|smaller|larger|calc\(.*\))$")
ALLOW = re.compile(r"fs-allow:\s*([^*]+?)\s*(\*/|$)")


def defined_sizes(root: Path) -> set[str]:
    path = next(root.rglob(TOKENS), None)
    if path is None:
        raise SystemExit(f"[gate_type_scale] {TOKENS} 를 {root} 아래에서 못 찾았다")
    return set(re.findall(r"--fs-([a-z0-9-]+)\s*:", path.read_text(encoding="utf-8")))


def check_text(text: str, path: str, sizes: set[str]) -> tuple[list[str], list[str]]:
    """(위반, 예외) — 각각 `path:line: 값 [이유]` 줄."""
    bad, allowed = [], []
    for no, line in enumerate(text.split("\n"), 1):
        for m in DECL.finditer(line):
            value = m.group(1).strip().removesuffix("!important").strip()
            if RELATIVE.match(value):
                continue
            v = VAR.match(value)
            if v and v.group(1) in sizes and not v.group(2):
                continue
            why = ("정의되지 않은 토큰" if v and v.group(1) not in sizes
                   else "토큰 뒤 대체값(정의된 토큰에는 필요 없다)" if v
                   else "직접 쓴 값" if LITERAL.match(value) else "알 수 없는 값")
            a = ALLOW.search(line)
            if a and not v:
                allowed.append(f"{path}:{no}: {value}  [{a.group(1)}]")
            else:
                bad.append(f"{path}:{no}: {value}  ({why})")
    return bad, allowed


def main(argv: list[str]) -> int:
    root = Path(argv[0]) if argv else DEFAULT
    sizes = defined_sizes(root)
    bad, allowed = [], []
    for path in sorted(root.rglob("*")):
        if path.suffix not in {".css", ".ts"} or path.name in SKIP or path.name.endswith(".d.ts"):
            continue
        b, a = check_text(path.read_text(encoding="utf-8"), str(path.relative_to(ROOT if root.is_relative_to(ROOT) else root)), sizes)
        bad += b
        allowed += a
    sizes_txt = " ".join(f"--fs-{s}" for s in sorted(sizes))
    if bad:
        print(f"[gate_type_scale] FAIL — 글씨 크기가 체계 밖이다 {len(bad)}건 (토큰: {sizes_txt})")
        print("\n".join("  " + x for x in bad))
        print("  토큰으로 바꾸거나, 정당하면 그 줄에 `fs-allow: <이유>` 주석을 단다.")
        return 1
    print(f"[gate_type_scale] PASS — 체계 밖 0건 · 예외 {len(allowed)}건 (토큰: {sizes_txt})")
    print("\n".join("  " + x for x in allowed))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

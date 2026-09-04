# -*- coding: utf-8 -*-
"""gen_motion.py — SSoT 의 `motion` 그룹을 토큰 CSS 에 끼워 넣는다.

lime-design 엔진이 내는 그룹은 고정돼 있다(radius·space·fs·lh·font·shadow — HUB-NOTES §5).
곡선·시간(모션)은 엔진 밖이라, 이 스크립트가 `design-tokens.json["motion"]` 을 읽어
생성된 CSS 의 첫 블록 안 `/* Shape */` 바로 앞에 `/* Motion */` 절을 넣는다. 같은 값이
site·desktop·web 셋에 똑같이 들어간다(스킨은 색이 갈리지 모션은 갈리지 않는다).

⚠ DESIGN/tokens.css 에는 쓰지 않는다 — 그 파일은 엔진 산출물이고 `gen_tokens --check` 가
바이트 단위로 대조한다. dist/ 사본에만 넣는다(build_dist.py 가 순서를 맡는다).

usage: python -X utf8 scripts/gen_motion.py --tokens DESIGN/design-tokens.json --src dist/site/site-tokens.css [--out 같은파일]
"""
from __future__ import annotations
import argparse, json, pathlib, sys

MARK = "  /* Shape */"
HEAD = "  /* Motion — 곡선·시간(gen_motion.py, SSoT motion 그룹). 등장·퇴장·작은 UI 는 ease-out,\n" \
       "     화면 위 이동은 ease-in-out, 큰 면(패널·시트)은 drawer, 메뉴·모달 성장은 settle */"


def motion_block(tokens: dict) -> str:
    m = tokens.get("motion")
    if not isinstance(m, dict):
        raise SystemExit("[gen_motion] design-tokens.json 에 motion 그룹이 없다")
    lines = [HEAD]
    for k, v in m.items():
        if k.startswith("$") or k == "note":
            continue
        if not isinstance(v, str) or not v.strip():
            raise SystemExit(f"[gen_motion] motion.{k} 는 평문 문자열이어야 한다(엔진 규약과 같다): {v!r}")
        lines.append(f"  --{k}: {v};")
    return "\n".join(lines) + "\n\n"


def inject(css: str, block: str) -> str:
    if "/* Motion" in css:
        raise SystemExit("[gen_motion] 이미 Motion 절이 있다 — 재생성 산출물에서 다시 시작할 것")
    i = css.find(MARK)
    if i < 0:
        raise SystemExit(f"[gen_motion] 삽입 지점({MARK.strip()})을 못 찾았다")
    return css[:i] + block + css[i:]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tokens", required=True)
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", help="기본은 --src 제자리")
    a = ap.parse_args()
    tokens = json.loads(pathlib.Path(a.tokens).read_text(encoding="utf-8"))
    src = pathlib.Path(a.src)
    css = inject(src.read_text(encoding="utf-8"), motion_block(tokens))
    out = pathlib.Path(a.out) if a.out else src
    out.write_text(css, encoding="utf-8", newline="\n")
    print(f"Motion injected: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

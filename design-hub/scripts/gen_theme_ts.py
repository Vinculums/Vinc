# -*- coding: utf-8 -*-
"""gen_theme_ts.py — SSoT + atlasScene → app/src/theme.ts (three.js/Sigma용 hex).

WebGL은 CSS 변수를 못 읽으므로, 같은 토큰 트리에서 스킨×모드별 숫자 hex 모듈을
생성해 atlas3d.ts/atlas.ts의 하드코딩 팔레트를 대체한다(Deep Signal 스펙).

usage: python -X utf8 scripts/gen_theme_ts.py \
         --tokens DESIGN/design-tokens.json \
         --scene imports/figma-variables-2026-07-19.json \
         --out dist/app/theme.ts
"""
from __future__ import annotations
import argparse, json, pathlib, sys

SEMANTIC_KEYS = [
    ("background", "background"), ("surface", "muted"), ("card", "card"),
    ("line", "border"), ("text", "foreground"), ("textSoft", "muted-foreground"),
    ("textMute", "caption-foreground"), ("ring", "ring"),
    ("primary", "primary"), ("primaryText", "primary-foreground"), ("link", "link"),
]

def resolve(tokens: dict, mode: str, name: str) -> str:
    raw = tokens["semantic"][mode][name]
    if raw.startswith("{"):
        return tokens["primitive"][raw[1:-1]]["$value"]
    return raw

def ts_hex(h: str) -> str:
    return "0x" + h.lstrip("#").upper()

def prim(tokens: dict, name: str) -> str:
    return tokens["primitive"][name]["$value"]

def link_ramp(tokens: dict, mode: str) -> dict:
    """Atlas 링크 농도 램프 — heavy→fade. 파생 셰이드(SSoT 프리미티브에서 유도).

    ⚠ 여기는 **텍스트 토큰을 재사용하면 안 된다**. 구판은 base=d_mute·cross=
    indigo_700·fade=d_line 처럼 캡션·CTA·보더 토큰을 빌려 썼는데, 그 토큰들은
    자기 용도(텍스트·면)에서는 충분해도 **다크 배경 위 가는 선**으로는 미달이었다
    (d_mute = APCA Lc 28.2 < 30, indigo_700 = 9.6, d_line = 0). 그래서 선 전용
    프리미티브(slate_*·peri_500·grain_300)를 따로 둔다 — 텍스트 토큰을 밝히면
    본문 대비가 함께 흔들리기 때문이다. 검증은 `Vinc/scripts/gate_contrast.py`.
    """
    if mode == "dark":
        return {"heavy": prim(tokens, "peri_200"), "mid": prim(tokens, "peri_400"),
                "base": prim(tokens, "slate_500"), "cross": prim(tokens, "slate_400"),
                "fade": prim(tokens, "slate_700")}
    return {"heavy": prim(tokens, "ink_900"), "mid": prim(tokens, "ink_600"),
            "base": prim(tokens, "ink_400"), "cross": prim(tokens, "peri_500"),
            "fade": prim(tokens, "grain_300")}

def label_css(tokens: dict, mode: str) -> str:
    fg = resolve(tokens, mode, "foreground")
    bg = resolve(tokens, mode, "card")
    bd = resolve(tokens, mode, "border")
    return f"color:{fg};background:{bg};border:1px solid {bd}"

def emit(tokens: dict, scene: dict) -> str:
    L = []
    L.append("// AUTO-GENERATED — Vinc-design/scripts/gen_theme_ts.py. Do not hand-edit.")
    L.append("// SSoT: Vinc-design/DESIGN/design-tokens.json + imports atlasScene.")
    L.append("// WebGL(three.js/Sigma)용 팔레트 — CSS 변수와 동일 원천에서 생성.")
    L.append("")
    L.append("export type ThemeMode = 'light' | 'dark';")
    L.append("")
    L.append("export const palette = {")
    for mode in ("light", "dark"):
        L.append(f"  {mode}: {{")
        for ts_name, sem in SEMANTIC_KEYS:
            hexv = resolve(tokens, mode, sem)
            L.append(f"    {ts_name}: {ts_hex(hexv)}, // {hexv} ← semantic.{mode}.{sem}")
        ramp = link_ramp(tokens, mode)
        ramp_ts = ", ".join(f"{k}: '{v}'" for k, v in ramp.items())
        L.append(f"    linkRamp: {{ {ramp_ts} }},")
        L.append(f"    labelCss: '{label_css(tokens, mode)}',")
        L.append("  },")
    L.append("} as const;")
    L.append("")
    L.append("export function hexStr(n: number): string { return '#' + n.toString(16).padStart(6, '0'); }")
    L.append("")
    # atlasScene 은 **SSoT(design-tokens.json)가 정본**이다. 예전에는 imports 의
    # Figma 덤프에서만 읽었는데, 설계 결정으로 값을 바꾸면서 "2026-07-19 수동 수입"
    # 이라 적힌 파일에 쓰는 것은 출처를 속이는 일이다. 덤프는 폴백으로만 남긴다.
    sc = tokens.get("atlasScene") or scene["atlasScene"]
    L.append("// Atlas 성좌 씬 — 글로우는 그래프 노드 전용(발주자 규칙)")
    L.append("// 소비처는 atlas3d.ts 의 다크 글로우 분기 하나뿐 — fill·stroke·glowOpacity 만 읽는다.")
    L.append("export const atlasScene = {")
    L.append("  node: {")
    for tier, spec in sc["node"].items():
        stroke = f", stroke: {ts_hex(spec['stroke'])}" if "stroke" in spec else ""
        L.append(f"    {tier}: {{ fill: {ts_hex(spec['fill'])}{stroke}, glowOpacity: {spec['glowOpacity']} }},")
    L.append("  },")
    L.append("} as const;")
    L.append("")
    L.append("export function themePalette(mode: ThemeMode) { return palette[mode]; }")
    return "\n".join(L) + "\n"

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tokens", required=True)
    ap.add_argument("--scene", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    tokens = json.loads(pathlib.Path(a.tokens).read_text(encoding="utf-8"))
    scene = json.loads(pathlib.Path(a.scene).read_text(encoding="utf-8"))
    outp = pathlib.Path(a.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(emit(tokens, scene), encoding="utf-8", newline="\n")
    print(f"Generated: {outp}")
    return 0

if __name__ == "__main__":
    sys.exit(main())

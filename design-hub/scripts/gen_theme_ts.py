# -*- coding: utf-8 -*-
"""gen_theme_ts.py — SSoT + viewScene → app/src/theme.ts (three.js/Sigma용 hex).

WebGL은 CSS 변수를 못 읽으므로, 같은 토큰 트리에서 스킨×모드별 숫자 hex 모듈을
생성해 view3d.ts/view.ts의 하드코딩 팔레트를 대체한다(Deep Signal 스펙).

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
    ("textMute", "caption-foreground"), ("ring", "ring"), ("focus", "focus"),
    ("primary", "primary"), ("primaryText", "primary-foreground"), ("link", "link"),
]

def resolve(tokens: dict, mode: str, name: str) -> str:
    raw = tokens["semantic"][mode][name]
    if raw.startswith("{"):
        return tokens["primitive"][raw[1:-1]]["$value"]
    return raw

def ts_hex(h: str) -> str:
    return "0x" + h.lstrip("#").upper()

def scene_hex(tokens: dict, v: str) -> str:
    """viewScene 값은 hex 이거나 `{primitive}` 참조다."""
    return ts_hex(prim(tokens, v[1:-1]) if v.startswith("{") else v)

def prim(tokens: dict, name: str) -> str:
    return tokens["primitive"][name]["$value"]

def link_ramp(tokens: dict, mode: str) -> dict:
    """View 링크 농도 램프 — heavy→fade. 파생 셰이드(SSoT 프리미티브에서 유도).

    ⚠ 여기는 **텍스트 토큰을 재사용하면 안 된다**. 구판은 base=d_mute·cross=
    indigo_700·fade=d_line 처럼 캡션·CTA·보더 토큰을 빌려 썼는데, 그 토큰들은
    자기 용도(텍스트·면)에서는 충분해도 **다크 배경 위 가는 선**으로는 미달이었다
    (d_mute = APCA Lc 28.2 < 30, indigo_700 = 9.6, d_line = 0). 그래서 선 전용
    프리미티브(gray_*·grain_300)를 따로 둔다 — 텍스트 토큰을 밝히면
    본문 대비가 함께 흔들리기 때문이다. 검증은 `Vinc/scripts/gate_contrast.py`.
    2026-09-24 부터 무채색이다(peri·slate 폐기). 다크 mid 만 d_soft 를 빌리는데,
    그 값이 3D 마크 mid 티어의 링과 같은 자리라서다.
    """
    if mode == "dark":
        return {"heavy": prim(tokens, "gray_200"), "mid": prim(tokens, "d_soft"),
                "base": prim(tokens, "gray_500"), "cross": prim(tokens, "gray_400"),
                "fade": prim(tokens, "gray_700")}
    return {"heavy": prim(tokens, "ink_900"), "mid": prim(tokens, "ink_600"),
            "base": prim(tokens, "ink_400"), "cross": prim(tokens, "gray_600"),
            "fade": prim(tokens, "grain_300")}

def label_css(tokens: dict, mode: str) -> str:
    fg = resolve(tokens, mode, "foreground")
    bg = resolve(tokens, mode, "card")
    bd = resolve(tokens, mode, "border")
    return f"color:{fg};background:{bg};border:1px solid {bd}"

def emit(tokens: dict, scene: dict) -> str:
    L = []
    L.append("// AUTO-GENERATED — Vinc/design-hub/scripts/gen_theme_ts.py. Do not hand-edit.")
    L.append("// SSoT: Vinc/design-hub/DESIGN/design-tokens.json + imports viewScene.")
    L.append("// Palette for WebGL (three.js/Sigma), generated from the same source as the CSS variables.")
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
    # viewScene 은 **SSoT(design-tokens.json)가 정본**이다. 예전에는 imports 의
    # Figma 덤프에서만 읽었는데, 설계 결정으로 값을 바꾸면서 "2026-07-19 수동 수입"
    # 이라 적힌 파일에 쓰는 것은 출처를 속이는 일이다. 덤프는 폴백으로만 남긴다.
    sc = tokens.get("viewScene") or scene["viewScene"]
    L.append("// View 성좌 씬 — 글로우는 그래프 노드 전용(발주자 규칙)")
    L.append("// 소비처는 view3d.ts 의 다크 글로우 분기 하나뿐 — fill·stroke·glowOpacity 만 읽는다.")
    L.append("export const viewScene = {")
    L.append("  node: {")
    for tier, spec in sc["node"].items():
        stroke = f", stroke: {scene_hex(tokens, spec['stroke'])}" if "stroke" in spec else ""
        L.append(f"    {tier}: {{ fill: {scene_hex(tokens, spec['fill'])}{stroke}, glowOpacity: {spec['glowOpacity']} }},")
    L.append("  },")
    L.append("} as const;")
    L.append("")
    # domainTint — 열린 층(사용자 토글)이라 뜻을 나르지 않는다. 그래도 s·l 은 토큰에
    # 둔다: 이 두 값이 틴트의 대비 바닥이라 앱 코드의 리터럴로 두면 게이트 밖으로 샌다.
    dt = tokens.get("domainTint")
    if dt:
        L.append("// 도메인 틴트(열린 층) — 색상환 각도는 주제 문자열 해시, 채도·명도는 여기 고정.")
        L.append("// 소비처는 app/src/view-tint.ts 하나뿐.")
        L.append("export const domainTint = {")
        for mode in ("light", "dark"):
            L.append(f"  {mode}: {{ s: {dt[mode]['s']}, l: {dt[mode]['l']} }},")
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

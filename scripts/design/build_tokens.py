# -*- coding: utf-8 -*-
"""build_tokens.py — 토큰 스냅샷에서 소비처의 토큰 파일 넷을 만든다(엔진 없이, 순서 고정).

값의 정본은 그래프다(`vinc/design/tokens/*` 의 concept 노드). `pull_tokens.py` 가 그
노드들을 `tokens.json` 스냅샷으로 내려받고, 이 파일이 스냅샷 하나만 읽어 만든다:

  app/src/tokens.desktop.css          :root 기본(데스크톱) + 모션
  app/src/tokens.web.css              같은 CSS 를 `data-skin="web"` 로 스코프 + 모션
  app/src/theme.ts                    WebGL(three.js)용 16진 정수 팔레트
  Vinc-homepage/site/assets/tokens.css  데스크톱과 같은 본문(사이트)

나르고 대조하는 것은 `sync_out.py` 다. 이 파일은 문자열만 만든다(쓰지 않는다).
근거: [[decision:design-token-values-live-in-the-graph-and-the-hub-directory-is-gone-2026-10-29]]
"""
from __future__ import annotations

import colorsys
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
SNAPSHOT = HERE / "tokens.json"

DARK_SELECTOR = '[data-theme="dark"]'


def load(path: pathlib.Path = SNAPSHOT) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def prim(tokens: dict, name: str) -> str:
    return tokens["primitive"][name]["$value"]


def resolve(tokens: dict, mode: str, name: str) -> str:
    """의미 토큰은 기본 토큰을 한 단계로 가리킨다(`{grain_100}`). 사슬은 받지 않는다."""
    raw = tokens["semantic"][mode][name]
    return prim(tokens, raw[1:-1]) if raw.startswith("{") else raw


def hsl_channels(hexv: str) -> str:
    """`#F2F2F0` → `60 7% 95%`. CSS 가 `hsl(var(--x))` 로 감싸 쓰는 채널 표기."""
    r, g, b = (int(hexv.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4))
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    return f"{round(h * 360) % 360} {round(s * 100)}% {round(l * 100)}%"


def _with_tenant(tokens: dict, tenant: str | None) -> dict:
    """테넌트 덮어쓰기는 기본 토큰의 잎만 바꾼다(의미 층은 손대지 않는다)."""
    if not tenant:
        return tokens
    over = (tokens.get("tenants", {}).get(tenant) or {}).get("primitive") or {}
    if not over:
        return tokens
    merged = dict(tokens)
    merged["primitive"] = {**tokens["primitive"], **over}
    return merged


def _semantic_lines(tokens: dict, mode: str) -> list[str]:
    out = []
    for name, raw in tokens["semantic"][mode].items():
        hexv = resolve(tokens, mode, name)
        out.append(f"  --{name}: {hsl_channels(hexv)}; /* {hexv.upper()}  ← {raw} */")
    return out


def _group(tokens: dict, key: str, prefix: str) -> list[str]:
    out = []
    for k, v in tokens.get(key, {}).items():
        name = prefix if k == "DEFAULT" else f"{prefix}-{k}"
        out.append(f"  --{name}: {v};")
    return out


def base_css(tokens: dict, header: str) -> str:
    """색(라이트·다크)·모양·간격·글자·깊이. 모션은 `motion_css` 가 따로 끼운다."""
    L = [header.rstrip("\n"), ":root {",
         "  /* Colors (semantic, light) — resolved from primitive references */"]
    L += _semantic_lines(tokens, "light")
    L += ["", "  /* Shape */"] + _group(tokens, "radius", "radius")
    L += ["", "  /* Spacing (customer portal baseline — generous) */"] + _group(tokens, "space", "space")
    L += ["", "  /* Typography */"] + _group(tokens, "fs", "fs") + _group(tokens, "lh", "lh")
    L += _group(tokens, "font", "font")
    L += ["", "  /* Depth */"] + _group(tokens, "shadow", "shadow")
    L += ["}", "", "/* Dark — opt-in (light-first). Enable via <html data-theme=\"dark\">. */",
          f"{DARK_SELECTOR} {{"]
    L += _semantic_lines(tokens, "dark")
    if tokens.get("shadowDark"):
        L += ["", "  /* Depth (dark overrides) */"] + _group(tokens, "shadowDark", "shadow")
    L.append("}")
    return "\n".join(L) + "\n"


MOTION_MARK = "  /* Shape */"
MOTION_HEAD = ("  /* Motion — 곡선·시간(build_tokens.py, 토큰 motion 묶음). 등장·퇴장·작은 UI 는 ease-out,\n"
               "     화면 위 이동은 ease-in-out, 큰 면(패널·시트)은 drawer, 메뉴·모달 성장은 settle */")


def with_motion(css: str, tokens: dict) -> str:
    """첫 블록의 `/* Shape */` 바로 앞에 모션 절을 넣는다. 값은 모든 스킨에 같다."""
    lines = [MOTION_HEAD]
    for k, v in tokens["motion"].items():
        if k.startswith("$") or k == "note":
            continue
        if not isinstance(v, str) or not v.strip():
            raise SystemExit(f"[build_tokens] motion.{k} 는 평문 문자열이어야 한다: {v!r}")
        lines.append(f"  --{k}: {v};")
    i = css.find(MOTION_MARK)
    if i < 0:
        raise SystemExit("[build_tokens] 모션을 끼울 자리(/* Shape */)를 못 찾았다")
    return css[:i] + "\n".join(lines) + "\n\n" + css[i:]


def scoped(css: str, skin: str, header: str) -> str:
    """`:root` 와 다크 블록을 `data-skin` 속성으로 스코프한다(속성 셀렉터가 이긴다)."""
    out = []
    for line in css.splitlines():
        if line.startswith(":root {"):
            out.append(f':root[data-skin="{skin}"] {{')
        elif line.startswith(f"{DARK_SELECTOR} {{"):
            out.append(f':root[data-skin="{skin}"]{DARK_SELECTOR} {{')
        else:
            out.append(line)
    return header + "\n".join(out) + "\n"


# ── theme.ts ────────────────────────────────────────────────────────────
SEMANTIC_KEYS = [
    ("background", "background"), ("surface", "muted"), ("card", "card"),
    ("line", "border"), ("text", "foreground"), ("textSoft", "muted-foreground"),
    ("textMute", "caption-foreground"), ("ring", "ring"), ("focus", "focus"),
    ("primary", "primary"), ("primaryText", "primary-foreground"), ("link", "link"),
    ("point", "point"), ("pointText", "point-text"),
]


def _ts_hex(h: str) -> str:
    return "0x" + h.lstrip("#").upper()


def _scene_hex(tokens: dict, v: str) -> str:
    return _ts_hex(prim(tokens, v[1:-1]) if v.startswith("{") else v)


def link_ramp(tokens: dict, mode: str) -> dict:
    """View 링크 농도 램프(heavy→fade). 선 전용 기본 토큰만 쓴다: 텍스트 토큰은 가는 선으로는
    대비가 모자라고, 그것을 밝히면 본문 대비가 함께 흔들린다. 검증은 `scripts/gate_contrast.py`."""
    if mode == "dark":
        return {"heavy": prim(tokens, "gray_200"), "mid": prim(tokens, "d_soft"),
                "base": prim(tokens, "gray_500"), "cross": prim(tokens, "gray_400"),
                "fade": prim(tokens, "gray_700")}
    return {"heavy": prim(tokens, "ink_900"), "mid": prim(tokens, "ink_600"),
            "base": prim(tokens, "ink_400"), "cross": prim(tokens, "gray_600"),
            "fade": prim(tokens, "grain_300")}


def theme_ts(tokens: dict, header: list[str]) -> str:
    L = list(header)
    L += ["", "export type ThemeMode = 'light' | 'dark';", "", "export const palette = {"]
    for mode in ("light", "dark"):
        L.append(f"  {mode}: {{")
        for ts_name, sem in SEMANTIC_KEYS:
            hexv = resolve(tokens, mode, sem)
            L.append(f"    {ts_name}: {_ts_hex(hexv)}, // {hexv} ← semantic.{mode}.{sem}")
        ramp = ", ".join(f"{k}: '{v}'" for k, v in link_ramp(tokens, mode).items())
        L.append(f"    linkRamp: {{ {ramp} }},")
        fg, bg, bd = (resolve(tokens, mode, k) for k in ("foreground", "card", "border"))
        L.append(f"    labelCss: 'color:{fg};background:{bg};border:1px solid {bd}',")
        L.append("  },")
    L += ["} as const;", "",
          "export function hexStr(n: number): string { return '#' + n.toString(16).padStart(6, '0'); }",
          "",
          "// View 성좌 씬 — 글로우는 그래프 노드 전용(발주자 규칙)",
          "// 소비처는 view3d.ts 의 다크 글로우 분기 하나뿐 — fill·stroke·glowOpacity 만 읽는다.",
          "export const viewScene = {", "  node: {"]
    for tier, spec in tokens["viewScene"]["node"].items():
        stroke = f", stroke: {_scene_hex(tokens, spec['stroke'])}" if "stroke" in spec else ""
        L.append(f"    {tier}: {{ fill: {_scene_hex(tokens, spec['fill'])}{stroke}, "
                 f"glowOpacity: {spec['glowOpacity']} }},")
    L += ["  },", "} as const;", ""]
    tj = tokens.get("topicJewels")
    if tj:
        L.append("// 주제 색 — 공홈 조형의 보석색(루비·사파이어·에메랄드·호박·자수정). 소비처는 app/src/view-tint.ts.")
        L.append("export const topicJewels = {")
        for mode in ("light", "dark"):
            L.append(f"  {mode}: [{', '.join(_ts_hex(h) for h in tj[mode])}],")
        L += ["} as const;", ""]
    L.append("export function themePalette(mode: ThemeMode) { return palette[mode]; }")
    return "\n".join(L) + "\n"


# ── 머리말 ──────────────────────────────────────────────────────────────
CSS_HEADER = ("/* AUTO-GENERATED by Vinc/scripts/design/build_tokens.py. Do not hand-edit.\n"
              "   Values: the Vinc graph (concept:token.*), snapshot scripts/design/tokens.json"
              " · mode: light-first · color: HSL channels. */\n")
WEB_HEADER = ("/* AUTO-GENERATED by Vinc/scripts/design/build_tokens.py (skin=web). Do not hand-edit.\n"
              "   The same token CSS, scoped to data-skin=\"web\". */\n")
TS_HEADER = [
    "// AUTO-GENERATED by Vinc/scripts/design/build_tokens.py. Do not hand-edit.",
    "// Values: the Vinc graph (concept:token.*), snapshot Vinc/scripts/design/tokens.json.",
    "// Palette for WebGL (three.js), generated from the same source as the CSS variables.",
]


def outputs(tokens: dict) -> dict[str, str]:
    """생성물 이름 → 본문(각인 전). 이름은 `sync_out.py` 의 MAP 이 소비 경로로 푼다."""
    site = with_motion(base_css(tokens, CSS_HEADER), tokens)
    desktop = with_motion(base_css(_with_tenant(tokens, "desktop"), CSS_HEADER), tokens)
    web = with_motion(scoped(base_css(tokens, CSS_HEADER), "web", WEB_HEADER), tokens)
    return {
        "site-tokens.css": site,
        "tokens.desktop.css": desktop,
        "tokens.web.css": web,
        "theme.ts": theme_ts(tokens, TS_HEADER),
    }

// AUTO-GENERATED — Vinc/design-hub/scripts/gen_theme_ts.py. Do not hand-edit.
// SSoT: Vinc/design-hub/DESIGN/design-tokens.json + imports viewScene.
// Palette for WebGL (three.js/Sigma), generated from the same source as the CSS variables.

export type ThemeMode = 'light' | 'dark';

export const palette = {
  light: {
    background: 0xF2F2F0, // #F2F2F0 ← semantic.light.background
    surface: 0xE9E9E6, // #E9E9E6 ← semantic.light.muted
    card: 0xFBFBFA, // #FBFBFA ← semantic.light.card
    line: 0xDEDEDA, // #DEDEDA ← semantic.light.border
    text: 0x17171A, // #17171A ← semantic.light.foreground
    textSoft: 0x55565E, // #55565E ← semantic.light.muted-foreground
    textMute: 0x82838D, // #82838D ← semantic.light.caption-foreground
    ring: 0x8F96D9, // #8F96D9 ← semantic.light.ring
    focus: 0x32355C, // #32355C ← semantic.light.focus
    primary: 0x32355C, // #32355C ← semantic.light.primary
    primaryText: 0xF2F2F0, // #F2F2F0 ← semantic.light.primary-foreground
    link: 0x4A4F8C, // #4A4F8C ← semantic.light.link
    linkRamp: { heavy: '#17171A', mid: '#55565E', base: '#82838D', cross: '#7E86D3', fade: '#D1D2D6' },
    labelCss: 'color:#17171A;background:#FBFBFA;border:1px solid #DEDEDA',
  },
  dark: {
    background: 0x0C0D13, // #0C0D13 ← semantic.dark.background
    surface: 0x12141D, // #12141D ← semantic.dark.muted
    card: 0x191C2A, // #191C2A ← semantic.dark.card
    line: 0x262A3D, // #262A3D ← semantic.dark.border
    text: 0xE8E7EF, // #E8E7EF ← semantic.dark.foreground
    textSoft: 0xA7A9BF, // #A7A9BF ← semantic.dark.muted-foreground
    textMute: 0x6E7189, // #6E7189 ← semantic.dark.caption-foreground
    ring: 0x8F96D9, // #8F96D9 ← semantic.dark.ring
    focus: 0x8F96D9, // #8F96D9 ← semantic.dark.focus
    primary: 0x3A4070, // #3A4070 ← semantic.dark.primary
    primaryText: 0xB9BEF0, // #B9BEF0 ← semantic.dark.primary-foreground
    link: 0x8F96D9, // #8F96D9 ← semantic.dark.link
    linkRamp: { heavy: '#B9BEF0', mid: '#8F96D9', base: '#747790', cross: '#9496A9', fade: '#525466' },
    labelCss: 'color:#E8E7EF;background:#191C2A;border:1px solid #262A3D',
  },
} as const;

export function hexStr(n: number): string { return '#' + n.toString(16).padStart(6, '0'); }

// View 성좌 씬 — 글로우는 그래프 노드 전용(발주자 규칙)
// 소비처는 view3d.ts 의 다크 글로우 분기 하나뿐 — fill·stroke·glowOpacity 만 읽는다.
export const viewScene = {
  node: {
    focus: { fill: 0xB9BEF0, glowOpacity: 0.9 },
    near: { fill: 0x8F96D9, glowOpacity: 0.5 },
    mid: { fill: 0x3A4070, stroke: 0x8F96D9, glowOpacity: 0.35 },
    far: { fill: 0x191C2A, stroke: 0x9496A9, glowOpacity: 0.18 },
  },
} as const;

// 도메인 틴트(열린 층) — 색상환 각도는 주제 문자열 해시, 채도·명도는 여기 고정.
// 소비처는 app/src/view-tint.ts 하나뿐.
export const domainTint = {
  light: { s: 0.44, l: 0.34 },
  dark: { s: 0.4, l: 0.72 },
} as const;

export function themePalette(mode: ThemeMode) { return palette[mode]; }

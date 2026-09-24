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
    ring: 0x82838D, // #82838D ← semantic.light.ring
    focus: 0x17171A, // #17171A ← semantic.light.focus
    primary: 0x17171A, // #17171A ← semantic.light.primary
    primaryText: 0xF2F2F0, // #F2F2F0 ← semantic.light.primary-foreground
    link: 0x17171A, // #17171A ← semantic.light.link
    linkRamp: { heavy: '#17171A', mid: '#55565E', base: '#82838D', cross: '#6E6E6A', fade: '#D1D2D6' },
    labelCss: 'color:#17171A;background:#FBFBFA;border:1px solid #DEDEDA',
  },
  dark: {
    background: 0x0A0A0A, // #0A0A0A ← semantic.dark.background
    surface: 0x121212, // #121212 ← semantic.dark.muted
    card: 0x1A1A1A, // #1A1A1A ← semantic.dark.card
    line: 0x2A2A2A, // #2A2A2A ← semantic.dark.border
    text: 0xEDEDEB, // #EDEDEB ← semantic.dark.foreground
    textSoft: 0xA8A8A4, // #A8A8A4 ← semantic.dark.muted-foreground
    textMute: 0x8C8C88, // #8C8C88 ← semantic.dark.caption-foreground
    ring: 0xA8A8A4, // #A8A8A4 ← semantic.dark.ring
    focus: 0xEDEDEB, // #EDEDEB ← semantic.dark.focus
    primary: 0xEDEDEB, // #EDEDEB ← semantic.dark.primary
    primaryText: 0x0A0A0A, // #0A0A0A ← semantic.dark.primary-foreground
    link: 0xEDEDEB, // #EDEDEB ← semantic.dark.link
    linkRamp: { heavy: '#C8C8C4', mid: '#A8A8A4', base: '#7A7A76', cross: '#969692', fade: '#565652' },
    labelCss: 'color:#EDEDEB;background:#1A1A1A;border:1px solid #2A2A2A',
  },
} as const;

export function hexStr(n: number): string { return '#' + n.toString(16).padStart(6, '0'); }

// View 성좌 씬 — 글로우는 그래프 노드 전용(발주자 규칙)
// 소비처는 view3d.ts 의 다크 글로우 분기 하나뿐 — fill·stroke·glowOpacity 만 읽는다.
export const viewScene = {
  node: {
    focus: { fill: 0xFFFFFF, glowOpacity: 0.9 },
    near: { fill: 0xC8C8C4, glowOpacity: 0.5 },
    mid: { fill: 0x3A3A38, stroke: 0xA8A8A4, glowOpacity: 0.35 },
    far: { fill: 0x1A1A1A, stroke: 0x969692, glowOpacity: 0.18 },
  },
} as const;

// 도메인 틴트(열린 층) — 색상환 각도는 주제 문자열 해시, 채도·명도는 여기 고정.
// 소비처는 app/src/view-tint.ts 하나뿐.
export const domainTint = {
  light: { s: 0.44, l: 0.34 },
  dark: { s: 0.4, l: 0.72 },
} as const;

export function themePalette(mode: ThemeMode) { return palette[mode]; }

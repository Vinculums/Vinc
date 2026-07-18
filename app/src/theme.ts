// AUTO-GENERATED — Vinc-design/scripts/gen_theme_ts.py. Do not hand-edit.
// SSoT: Vinc-design/DESIGN/design-tokens.json + imports atlasScene.
// WebGL(three.js/Sigma)용 팔레트 — CSS 변수와 동일 원천에서 생성.

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
    primary: 0x32355C, // #32355C ← semantic.light.primary
    primaryText: 0xF2F2F0, // #F2F2F0 ← semantic.light.primary-foreground
    link: 0x4A4F8C, // #4A4F8C ← semantic.light.link
    linkRamp: { heavy: '#17171A', mid: '#55565E', base: '#82838D', cross: '#8F96D9', fade: '#DEDEDA' },
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
    primary: 0x3A4070, // #3A4070 ← semantic.dark.primary
    primaryText: 0xB9BEF0, // #B9BEF0 ← semantic.dark.primary-foreground
    link: 0x8F96D9, // #8F96D9 ← semantic.dark.link
    linkRamp: { heavy: '#B9BEF0', mid: '#8F96D9', base: '#6E7189', cross: '#3A4070', fade: '#262A3D' },
    labelCss: 'color:#E8E7EF;background:#191C2A;border:1px solid #262A3D',
  },
} as const;

export function hexStr(n: number): string { return '#' + n.toString(16).padStart(6, '0'); }

// Atlas 성좌 씬 — 글로우는 그래프 노드 전용(발주자 규칙)
export const atlasScene = {
  node: {
    focus: { fill: 0xB9BEF0, glowRadius: 30, glowOpacity: 0.9 },
    near: { fill: 0x8F96D9, glowRadius: 16, glowOpacity: 0.5 },
    mid: { fill: 0x3A4070, stroke: 0x8F96D9, glowRadius: 10, glowOpacity: 0.35 },
    far: { fill: 0x191C2A, stroke: 0x6E7189, glowRadius: 6, glowOpacity: 0.18 },
  },
  edge: { color: 0x8F96D9, opacityFocus: 0.5, opacityBase: 0.28, opacityFar: 0.14 },
  grid: { color: 0x8F96D9, opacity: 0.05 },
} as const;

export function themePalette(mode: ThemeMode) { return palette[mode]; }

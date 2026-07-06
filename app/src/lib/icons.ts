// Notation 아이콘 레지스트리 — Vinc-docs/DESIGN/assets/icons 세트(43개)를 앱으로 벤더링.
//
// SVG 는 모두 `currentColor` 로 stroke/fill 을 그린다. 이 방식은 WebView 에서
// **정상 동작한다** — 소비 측 CSS `color`(= --foreground 잉크 토큰)를 상속하기 때문.
// (같은 SVG 를 Figma 로 가져오면 currentColor 가 해소되지 않아 깨진다 — 별도 분석 참조.)
//
// 인라인 주입(dangerouslySetInnerHTML)로 DOM 에 심어 currentColor 상속을 살린다.
// <img src>/background 로 쓰면 상속이 끊겨 색이 죽으므로 금지.

const modules = import.meta.glob("../assets/icons/*.svg", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

function nameFromPath(path: string): string {
  return path.split("/").pop()!.replace(/\.svg$/, "");
}

/** 아이콘명 → 원본 SVG 마크업. */
export const ICONS: Record<string, string> = Object.fromEntries(
  Object.entries(modules).map(([path, svg]) => [nameFromPath(path), svg]),
);

export type IconName = keyof typeof ICONS & string;

export const ICON_NAMES = Object.keys(ICONS).sort() as IconName[];

/** 화면·상태에서 참조하는 시맨틱 별칭(README 조립표와 정합). */
export const ICON_ALIAS = {
  // 내비 (8화면)
  login: "login",
  onboarding: "onboarding",
  ingest: "ingest",
  recall: "recall",
  domains: "domains",
  graph: "graph",
  search: "search",
  packExport: "pack-export",
  settings: "settings",
  // 상태 3단계 (색 아닌 형태)
  statusFull: "disc",
  statusHalf: "half-disc",
  statusEmpty: "ring",
  // UI 필수
  check: "check",
  close: "x",
  alert: "alert",
  play: "play",
  pause: "pause",
  upload: "upload",
  download: "download",
  lock: "lock",
  edit: "edit",
  sparkle: "sparkle",
  sync: "sync",
  undo: "undo",
  pin: "pin",
  decide: "decide",
  chevronRight: "chevron-right",
  chevronLeft: "chevron-left",
} as const satisfies Record<string, IconName>;

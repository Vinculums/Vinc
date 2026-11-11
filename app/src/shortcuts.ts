/** 단축키 표 — `?` 시트와 설정의 단축키 분류가 **같은 한 벌**을 읽는다.
 *
 *  두 곳이 각자 적으면 한쪽만 고쳐져 갈린다. 표면 키의 범위도 여기 있는 표면 순서에서
 *  나온다: 라우터(`main.ts` 의 ORDER)가 이 순서를 그대로 쓰므로, 표면이 늘거나 줄면 표와
 *  숫자 단축키가 함께 움직인다. 여기 없는 키(묶인 핸들러가 없는 키)는 적지 않는다. */
import { COPY } from "./copy";

/** 숫자 단축키의 표면 순서. `main.ts` 가 이 목록으로 ORDER 를 만든다. */
export const SURFACE_ORDER = ["home", "view", "search"] as const;

export type Shortcut = { keys: string; label: string };

export function shortcutList(): Shortcut[] {
  return [
    { keys: SURFACE_ORDER.map((_, i) => String(i + 1)).join(" · "), label: COPY.ui.palette.goSurface },
    { keys: "⌘/Ctrl K", label: COPY.ui.topbar.searchLabel },
    { keys: "⌘/Ctrl ⇧ K", label: COPY.ui.palette.cmdPalette },
    { keys: "⌘/Ctrl N", label: COPY.author.paletteDoc },
    { keys: "⌘/Ctrl ⇧ N", label: COPY.author.paletteNode },
    { keys: "Esc", label: COPY.ui.palette.back },
    { keys: "?", label: COPY.ui.palette.shortcutsOpen },
  ];
}

/** Notation icon set (Figma node 29:56 — components; SVG SSoT:
 *  Vinc-docs/DESIGN/assets/icons). 24×24, currentColor, stroke 1.5/fill ink. */
import ingest from "./icons/ingest.svg?raw";
import recall from "./icons/recall.svg?raw";
import graph from "./icons/graph.svg?raw";
import search from "./icons/search.svg?raw";
import packExport from "./icons/pack-export.svg?raw";
import settings from "./icons/settings.svg?raw";
import dot from "./icons/dot.svg?raw";
import halfDisc from "./icons/half-disc.svg?raw";
import ring from "./icons/ring.svg?raw";
import pin from "./icons/pin.svg?raw";
import alert from "./icons/alert.svg?raw";
import check from "./icons/check.svg?raw";
import chevronLeft from "./icons/chevron-left.svg?raw";
import dotGrid from "./icons/dot-grid.svg?raw";
import domains from "./icons/domains.svg?raw";
import decide from "./icons/decide.svg?raw";
import lock from "./icons/lock.svg?raw";
import sparkle from "./icons/sparkle.svg?raw";
import x from "./icons/x.svg?raw";
import upload from "./icons/upload.svg?raw";
// 들여오기·공유 전용(D67) — 구 upload(위 화살표)는 **들여오기인데 내보내기처럼**
// 읽혔고, pack-export 와 나란히 두면 둘 다 화살표라 실루엣으로 구분되지 않았다.
// 안으로 = 트레이로 내려오는 화살표 · 공유 = 갈라져 나가는 노드(형태 자체가 다름).
import packIn from "./icons/pack-in.svg?raw";
import shareNodes from "./icons/share-nodes.svg?raw";

const REG: Record<string, string> = {
  ingest, recall, graph, search, "pack-export": packExport, settings,
  dot, "half-disc": halfDisc, ring, pin, alert, check,
  "chevron-left": chevronLeft, "dot-grid": dotGrid, domains, decide,
  lock, sparkle, x, upload, "pack-in": packIn, "share-nodes": shareNodes,
  // ⚠ **자리만 잡아 둔 이름이다.** 이 앱에는 MCP 공식 마크가 없다(디자인 허브에도
  // 없다). 타사 상표라 가져오는 것이 사용 조건 판단을 먼저 요구하므로, 그때까지
  // 뜻이 가장 가까운 share-nodes(갈라져 나가는 노드 = 이 노드와 이어진 것들을
  // 건넨다)를 빌려 쓴다. 부르는 쪽은 이미 `icon("mcp")` 라고 적으므로 **교체는
  // 여기 한 줄**이다 — `import mcp from "./icons/mcp.svg?raw"` 를 더하고 값만 바꾼다.
  mcp: shareNodes,
};

/** Inline an icon. Size in px (default 20). */
export function icon(name: keyof typeof REG | string, size = 20): string {
  const svg = REG[name] || REG.dot;
  return `<span class="icon" style="--icon-size:${size}px" aria-hidden="true">${svg}</span>`;
}

/** Record/node state mark (Figma: ● filled · ◐ half · ○ empty — never color). */
export function stateDot(state: "filled" | "half" | "empty", size = 14): string {
  const name = state === "filled" ? "dot" : state === "half" ? "half-disc" : "ring";
  return icon(name, size);
}

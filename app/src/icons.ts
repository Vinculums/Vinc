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

const REG: Record<string, string> = {
  ingest, recall, graph, search, "pack-export": packExport, settings,
  dot, "half-disc": halfDisc, ring, pin, alert, check,
  "chevron-left": chevronLeft, "dot-grid": dotGrid, domains, decide,
  lock, sparkle, x, upload,
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

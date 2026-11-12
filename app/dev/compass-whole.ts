/** 그래프 탭의 나침반을 백엔드 없이 큰 합성 그래프로 띄우는 손 확인 판. CI 는 돌리지 않는다.
 *
 *  셸은 진짜다: `index.html` 의 `#shell` 을 꺼내 세우고 앱의 `mountTopbar` · `mountLnb` 를 부른 뒤, view.ts 와 같은 배선으로
 *  `mountCompass` 를 `#view-canvas` 에 세운다(LNB 트리 · 머리의 도구 · 검색 거르개). 바꿔 끼우는 것은 백엔드와 데이터뿐이다.
 *
 *      npm run dev  →  /dev/compass-whole.html?n=2500&topics=170&theme=dark&lang=en&whole=1
 *
 *  n: 노드 수(기본 2500) · topics: 주제 수(기본 170) · seed · theme: dark|light · lang: en|ko · whole=1 이면 마운트 뒤 전체 보기 단추를 누르고,
 *  start=whole 이면 처음부터 전체 보기로 마운트한다.
 *  측정 값은 `window.__harness` 에 남는다(마운트 · 첫 그림 시각). */
import "../src/app.css";
import { api, cfg } from "../src/api";
import type { NavTreeRes } from "../src/api";
import { mountLnb, setLnbGraphSource, repaintLnbGraph, setLnbGraphActive } from "../src/lnb";
import { mountTopbar, setTopbarTools, setSearchFilter } from "../src/topbar";
import { mountCompass, compassDataFrom } from "../src/surfaces/view-compass";
import { synthGraph } from "./compass-whole-data";

const params = new URLSearchParams(location.search);
const num = (k: string, d: number) => { const v = Number(params.get(k)); return Number.isFinite(v) && v > 0 ? v : d; };
const H: Record<string, unknown> = {};
(window as unknown as { __harness: unknown }).__harness = H;

async function main(): Promise<void> {
  cfg.lang = params.get("lang") === "ko" ? "ko" : "en";
  document.documentElement.lang = cfg.lang;
  document.documentElement.dataset.skin = "desktop";
  document.documentElement.dataset.theme = params.get("theme") === "light" ? "light" : "dark";
  location.hash = "#view";
  try { localStorage.setItem("vinc.shell.lnb", "open"); localStorage.removeItem("vinc.pins:harness"); } catch { /* 저장소가 없으면 기본이다 */ }
  const realFetch = window.fetch.bind(window);
  const page = await (await realFetch("/index.html")).text();
  const shell = new DOMParser().parseFromString(page, "text/html").getElementById("shell")!;
  document.body.prepend(document.importNode(shell, true));
  document.getElementById("shell")!.dataset.surface = "view";
  window.fetch = async () => new Response(JSON.stringify({ executed: false }), { status: 200, headers: { "content-type": "application/json" } });
  (api as unknown as { navTree: () => Promise<NavTreeRes> }).navTree = async () => ({ executed: true, unfiled: [], domains: [] } as unknown as NavTreeRes);
  mountTopbar();
  mountLnb();

  const t0 = performance.now();
  const g = synthGraph({ nodes: num("n", 2500), topics: num("topics", 170), seed: num("seed", 7), lang: cfg.lang });
  H.generated = { nodes: g.nodes.length, edges: g.edges.length, ms: Math.round(performance.now() - t0) };
  const surface = document.getElementById("surface")!;
  surface.innerHTML = `<div id="view-canvas"></div>`;
  const canvas = surface.querySelector<HTMLElement>("#view-canvas")!;
  const data = compassDataFrom(g.nodes, g.edges);
  const m0 = performance.now();
  let ready = false;
  // start=whole: 보던 자리(전체 보기)를 이어 받는 마운트 = 마운트부터 전체 보기의 첫 그림까지를 잰다
  const restore = params.get("start") === "whole" ? { path: ["topic:"], hist: [], hi: -1, mode: "whole", filt: [], more: [], koff: [], tsel: null, view: null, query: "" } : null;
  const cmp = mountCompass(canvas, data, { store: "harness", restore, onTree: (o) => repaintLnbGraph(o), onOpen: (id) => { H.opened = id; },
    onChange: () => { if (ready) setLnbGraphActive(cmp.centre()); } });
  ready = true;
  H.mountMs = Math.round(performance.now() - m0);
  setLnbGraphSource({ groups: () => cmp.tree(), open: (id) => cmp.open(id), mark: (k) => cmp.mark(k), dock: cmp.side });
  setTopbarTools(cmp.tools);
  setLnbGraphActive(cmp.centre());
  setSearchFilter((q) => cmp.filter(q));
  (window as unknown as { __cmp: unknown }).__cmp = cmp;
  await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
  H.firstPaintMs = Math.round(performance.now() - m0);
  if (params.get("whole") === "1") {
    const w0 = performance.now();
    cmp.tools.querySelector<HTMLButtonElement>("[data-whole]")?.click();
    H.wholeSyncMs = Math.round(performance.now() - w0);
    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
    H.wholePaintMs = Math.round(performance.now() - w0);
  }
  document.body.dataset.ready = "1";
}
void main();

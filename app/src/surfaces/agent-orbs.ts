/** Agent 관계 그래프의 노드 몸통 — 그래프 보기와 같은 Vinc orb(`view-orbs3d.ts`)를 평면 배치 위에 그린다.
 *
 *  노드는 3D 몸통 그대로(개념 = 빛 구름 유리구, 결정 = 결정을 담은 유리구, 상태 고리도 셰이더의 것),
 *  보여 주는 것은 평면이다: 카메라가 아주 멀리서 좁은 화각으로 정면을 본다. 그러면 원근이 거의 없어
 *  SVG 의 좌표(px)와 화면이 한 점씩 맞는다. 선·이름표·누르는 자리는 위의 SVG 가 맡는다.
 *  색은 그래프 보기와 같은 주제 보석(`tintPlan`)이다. 근거: [[vinc.research.graph-vinc-orb-2026-10-28]] */
import * as THREE from "three";
import { OrbLayer, ORB_STATE, orbKindOf, orbSeed } from "./view-orbs3d";
import { tintPlan } from "../view-tint";
import { palette as signal, type ThemeMode } from "../theme";
import type { MapNode } from "../agent-ontology";

export interface OrbNode extends MapNode { domain: string }

/** 그래프 보기와 같은 노출(view3d EXPOSURE). */
const EXPOSURE: Record<ThemeMode, number> = { dark: 1.06, light: 1.02 };
const FOV = 8;
const themeMode = (): ThemeMode => (document.documentElement.dataset.theme === "dark" ? "dark" : "light");

let current: { stop: () => void; select: (id: string | null) => void } | null = null;

/** 고른 노드만 바꾼다. 캔버스와 WebGL 문맥은 그대로 둔다(새로 만들면 첫 장면까지 비어 깜빡인다). */
export function selectOrb(id: string | null): void { current?.select(id); }

/** 캔버스 하나에 몸통을 그린다. 다시 그리면 앞의 것을 먼저 치운다. WebGL 이 없으면 false(SVG 점이 그대로 남는다). */
export function mountOrbs(canvas: HTMLCanvasElement, nodes: OrbNode[], width: number, height: number,
                          selected: string | null): boolean {
  current?.stop();
  current = null;
  let renderer: THREE.WebGLRenderer;
  try {
    renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true, powerPreference: "low-power" });
  } catch {
    return false;
  }
  const mode = themeMode();
  renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
  renderer.setSize(width, height, false);
  renderer.setClearColor(0x000000, 0);
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = EXPOSURE[mode];

  const dist = (height / 2) / Math.tan((FOV / 2) * Math.PI / 180);
  const camera = new THREE.PerspectiveCamera(FOV, width / height, dist * 0.5, dist * 2);
  camera.position.set(width / 2, -height / 2, dist);
  camera.lookAt(width / 2, -height / 2, 0);

  const scene = new THREE.Scene();
  const layer = new OrbLayer(nodes.length, mode === "light");
  const plan = tintPlan(nodes.map((n) => n.domain), mode);
  const ink = signal[mode].text;
  const colours = nodes.map((n) => { const hex = plan.topics.get(n.domain); return new THREE.Color(hex ? parseInt(hex.slice(1), 16) : ink); });
  const place = (sel: string | null) => nodes.forEach((n, i) => {
    const state = n.id === sel ? ORB_STATE.selected : n.kind === "agent" ? ORB_STATE.active : ORB_STATE.idle;
    layer.set(i, n.x, -n.y, 0, n.r, orbKindOf(n.kind === "policy" ? "decision" : "concept"), state, orbSeed(n.id), colours[i]);
  });
  place(selected);
  scene.add(layer.body, layer.halo);

  // 안의 빛이 천천히 흐른다. 움직임을 줄인 사용자에게는 한 번만 그린다. 캔버스가 문서에서 빠지면 멈춘다.
  const still = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  let raf = 0;
  const t0 = performance.now();
  const frame = () => {
    if (!canvas.isConnected) { stop(); return; }
    layer.flush((performance.now() - t0) / 1000, dist);
    renderer.render(scene, camera);
    if (!still) raf = requestAnimationFrame(frame);
  };
  const stop = () => {
    cancelAnimationFrame(raf);
    scene.remove(layer.body, layer.halo);
    layer.dispose();
    renderer.dispose();
  };
  current = { stop, select: (id) => { place(id); if (still) frame(); } };
  frame();
  return true;
}

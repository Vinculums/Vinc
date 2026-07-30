/** Atlas 3D renderer (DEC D21 revised — full 3D, owner decision 2026-07-10).
 *
 *  Monochrome 3D discipline (spec: vinc.d.atlas-visual-language §1.1):
 *  depth comes from shading and atmosphere, never hue — matte ink Lambert
 *  materials (ink/ink_soft/ink_mute), paper background + fog, one neutral
 *  directional light. The single point color marks "the current focus" only.
 *
 *  Node primitives = type (D35): sphere (Concept) · small regular hex prism
 *  (Episode/Record — all edges equal) · octahedron (Bridge) · sphere + ring
 *  (L0 Domain). Gateways are translucent. Sidecar supplies deterministic
 *  x/y/z (D17) — positions are pinned (fx/fy/fz), no client force simulation. */
import ForceGraph3D, { type ForceGraph3DInstance } from "3d-force-graph";
import * as THREE from "three";
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js";
import { loadPrefs, type AtlasPrefs } from "../atlas-prefs";
import { palette as signal, atlasScene, type ThemeMode } from "../theme";

const SCALE = 140;   // sidecar unit-circle coords → scene units

/** Signal 팔레트 — 토큰과 같은 원천(Vinc-design 허브 gen_theme_ts 생성물).
 *  다크(기본) = Deep Signal: 딥 차콜 위 페리윙클 포커스. 라이트 = 그레인+잉크.
 *  themePalette()가 유일한 런타임 seam — 하드코딩 hex 금지. */
type Palette = {
  ink: number; inkMute: number; point: number; fog: number;
  link: { heavy: string; mid: string; base: string; cross: string; fade: string };
  label: string;
};
function themePalette(): Palette {
  const mode: ThemeMode =
    document.documentElement.dataset.theme === "dark" ? "dark" : "light";
  const p = signal[mode];
  return {
    ink: p.text, inkMute: p.textMute,
    point: mode === "dark" ? p.ring : p.primary,   // 다크는 페리윙클(가시성), 라이트는 로고 인디고
    fog: p.background,
    link: p.linkRamp, label: p.labelCss,
  };
}

export type N3 = {
  id: string; label: string; domain?: string; degree?: number;
  kind?: string;              // concept | episode | record | bridge | domain
  gateway?: boolean; focus?: boolean; records?: number;
  pinned?: boolean;           // D32 — pin 은 허브 승격(크기 가중; 레이아웃은 사이드카)
  x?: number; y?: number; z?: number;
  fx?: number; fy?: number; fz?: number;
};
export type L3 = { source: string; target: string; type?: string; cross?: boolean; weight?: number };

// D40 — 글래스 노드(발주자 지시): 프로스티드 유리. 완전 투명은 실루엣
// (형태=타입)을 무너뜨리므로 프로스트 불투명도를 기본으로 두고, 클리어코트
// 하이라이트 + 환경맵 반사가 "유리 느낌"을 만든다. 색은 모노크롬 유지.
const NODE_OP = 0.72;   // 일반 노드 프로스트
const GATE_OP = 0.35;   // 게이트웨이 — 더 투명("여기 소속 아님")

function material(color: number, opacity = 1): THREE.Material {
  return new THREE.MeshPhysicalMaterial({
    color, transparent: true, opacity,
    roughness: 0.18, metalness: 0,
    clearcoat: 1, clearcoatRoughness: 0.15,
    envMapIntensity: 0.9,
  });
}

// ── 성좌 글로우(Figma 92:211, 토큰=theme.ts atlasScene) — 깊이 계층별 헤일로.
//    다크(박물관 다크뷰) 전용: focus 글로우 최대 → near → mid(링) → far 페이드.
//    계층 = 초점(seed)으로부터의 홉 거리(초점 없는 층은 허브도 degree 근사). ──
type Tier = "focus" | "near" | "mid" | "far";
let _glowTex: THREE.Texture | null = null;
function glowTexture(): THREE.Texture {
  if (_glowTex) return _glowTex;
  const s = 128, cv = document.createElement("canvas"); cv.width = cv.height = s;
  const ctx = cv.getContext("2d")!;
  const g = ctx.createRadialGradient(s / 2, s / 2, 0, s / 2, s / 2, s / 2);
  g.addColorStop(0, "rgba(255,255,255,1)");
  g.addColorStop(0.22, "rgba(255,255,255,0.55)");
  g.addColorStop(1, "rgba(255,255,255,0)");
  ctx.fillStyle = g; ctx.fillRect(0, 0, s, s);
  _glowTex = new THREE.CanvasTexture(cv);
  return _glowTex;
}
function glowSprite(color: number, size: number, opacity: number): THREE.Sprite {
  const mat = new THREE.SpriteMaterial({ map: glowTexture(), color, transparent: true,
    opacity, depthWrite: false, blending: THREE.AdditiveBlending });
  mat.userData.base = opacity;
  const sp = new THREE.Sprite(mat); sp.scale.set(size, size, 1);
  return sp;
}
function assignTiers(nodes: N3[], links: L3[]) {
  const idOf = (x: any) => (typeof x === "object" ? x.id : x);
  const nbr = new Map<string, Set<string>>();
  for (const l of links) {
    const s = idOf(l.source), t = idOf(l.target);
    (nbr.get(s) || nbr.set(s, new Set()).get(s)!).add(t);
    (nbr.get(t) || nbr.set(t, new Set()).get(t)!).add(s);
  }
  const focusIds = nodes.filter((n) => n.focus).map((n) => n.id);
  if (focusIds.length) {   // 초점 있는 뷰(L2) — BFS 홉 거리로 계층
    const hop = new Map<string, number>();
    let frontier = new Set(focusIds), d = 0;
    while (frontier.size && d < 8) {
      const next = new Set<string>();
      for (const id of frontier) {
        if (!hop.has(id)) hop.set(id, d);
        for (const m of nbr.get(id) || []) if (!hop.has(m)) next.add(m);
      }
      frontier = next; d++;
    }
    for (const n of nodes) {
      const h = hop.get(n.id);
      (n as any).__tier = (h === 0 ? "focus" : h === 1 ? "near" : h === 2 ? "mid" : "far") as Tier;
    }
  } else {   // 초점 없는 뷰(L0/L1) — 허브도(degree)로 깊이(성좌감 유지, 고아만 far)
    for (const n of nodes) {
      const deg = n.degree || 0;
      (n as any).__tier = (n.pinned || deg >= 8 ? "near" : deg >= 2 ? "mid" : "far") as Tier;
    }
  }
}

/** 환경맵(유리 반사원) — 렌더러당 1회 생성해 씬에 심는다. */
function applyEnvironment(renderer: THREE.WebGLRenderer, scene: THREE.Scene) {
  const pmrem = new THREE.PMREMGenerator(renderer);
  scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
}

/** 상시 라벨 스프라이트 — 캔버스에 글자를 구워 카메라를 향하게 둔다(빌보드).
 *  DOM 라벨(nodeLabel)은 hover 전용이라 상시 표시에 쓸 수 없다. 색은 잉크 하나. */
function labelSprite(text: string, pal: Palette): THREE.Sprite {
  const pad = 8, fs = 34;
  const cv = document.createElement("canvas");
  const ctx = cv.getContext("2d")!;
  ctx.font = `600 ${fs}px system-ui, sans-serif`;
  const cut = text.length > 22 ? text.slice(0, 21) + "…" : text;
  cv.width = Math.ceil(ctx.measureText(cut).width) + pad * 2;
  cv.height = fs + pad * 2;
  const c2 = cv.getContext("2d")!;
  c2.font = `600 ${fs}px system-ui, sans-serif`;
  c2.textBaseline = "middle";
  c2.fillStyle = `#${pal.ink.toString(16).padStart(6, "0")}`;
  c2.fillText(cut, pad, cv.height / 2);
  const tex = new THREE.CanvasTexture(cv);
  tex.minFilter = THREE.LinearFilter;
  const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false }));
  const scale = 0.34;
  sp.scale.set(cv.width * scale, cv.height * scale, 1);
  sp.position.set(cv.width * scale * 0.5 + 8, 10, 0);
  return sp;
}

/** 노드 렌더 반경 — nodeObject(기하)와 화살촉 오프셋(D33)이 공유하는 단일 정의. */
function nodeRadius(n: N3, prefs: AtlasPrefs): number {
  // D31 열린 층 — 크기 스케일만 사용자 선택(√연결수 ↔ 균일); 형태=타입은 닫힌 층.
  let r = n.kind === "domain"
    ? 6 + 2.2 * Math.sqrt(n.records || 1)
    : prefs.size === "uniform" ? 5
    : Math.min(3 + 1.4 * Math.sqrt(n.degree || 1), 12);
  if (n.pinned) r *= 1.25;   // pin = 허브 승격(D32) — 확정된 노드는 한 급 크게
  // 2D 는 깊이 겹침 여지가 없어 조밀 — 반경을 줄여 겹침 비율 자체를 낮춘다
  if (prefs.view === "2d" && n.kind !== "domain") r *= 0.7;
  return r;
}

function nodeObject(n: N3, prefs: AtlasPrefs, pal: Palette = themePalette(), glow = false): THREE.Object3D {
  const dark = document.documentElement.dataset.theme === "dark";
  const r = nodeRadius(n, prefs);
  let geo: THREE.BufferGeometry;
  switch (n.kind) {
    case "domain":   // 구 + 적도 링(D35 개정 — 이중육각 계보는 기록물로 이관)
    case "concept":  // 구 — 최다 노드종에 최소 잉크 형태(시각 비대 완화, D35)
      geo = new THREE.SphereGeometry(r, 20, 14); break;
    case "bridge":   // 정팔면체(마름모의 입체화)
      geo = new THREE.OctahedronGeometry(r); break;
    default: {       // episode/record/entity — 작은 정육각기둥(변 = 높이, D35).
      const s = r * 0.75;
      geo = new THREE.CylinderGeometry(s, s, s, 6); break;
    }
  }

  // ── 성좌 글로우(Figma 92:211) — 다크 전용, domain 제외. 계층 fill + 링 + 헤일로. ──
  if (dark && glow && n.kind !== "domain") {
    const tier = ((n as any).__tier || "near") as Tier;
    const spec: any = atlasScene.node[tier];
    const stroke: number | undefined = spec.stroke;
    const fill = n.gateway ? pal.inkMute : (n.focus ? atlasScene.node.focus.fill : spec.fill);
    const baseOp = n.gateway ? GATE_OP : 1;
    const mat = new THREE.MeshBasicMaterial({ color: fill, transparent: true, opacity: baseOp });
    mat.userData.base = baseOp;
    const mesh = new THREE.Mesh(geo, mat);
    if (stroke !== undefined) {   // mid/far — 링(stroke 색)으로 어두운 fill 을 가시화
      const ringMat = new THREE.MeshBasicMaterial({ color: stroke, transparent: true, opacity: 0.95 });
      ringMat.userData.base = 0.95;
      const ring = new THREE.Mesh(new THREE.TorusGeometry(r * 1.3, Math.max(r * 0.12, 0.4), 6, 20), ringMat);
      if (prefs.view !== "2d") ring.rotation.x = Math.PI / 2;
      mesh.add(ring);
    }
    const glowColor = stroke ?? spec.fill;
    const k = tier === "focus" ? 6 : tier === "near" ? 4.5 : tier === "mid" ? 3.5 : 3;
    mesh.add(glowSprite(glowColor, r * k, spec.glowOpacity));
    if (prefs.view === "2d" && n.kind !== "concept" && n.kind !== "bridge") mesh.rotation.x = Math.PI / 2;
    return mesh;
  }

  // ── 기본(라이트 or 글로우 미적용: 미니 그리드) — 글래스 잉크(D40). ──
  const opacity = n.gateway ? GATE_OP : NODE_OP;
  const color = n.focus ? pal.point : n.gateway ? pal.inkMute : pal.ink;
  const mat = material(color, opacity);
  mat.userData.base = opacity;
  const mesh = new THREE.Mesh(geo, mat);
  if (n.kind === "domain") {
    const ringMat = material(pal.inkMute, 0.85); ringMat.userData.base = 0.85;
    const ring = new THREE.Mesh(new THREE.TorusGeometry(r * 1.45, r * 0.06, 8, 24), ringMat);
    if (prefs.view !== "2d") ring.rotation.x = Math.PI / 2;
    mesh.add(ring);
  }
  // 2D 모드: 기둥류(기록물)를 카메라 쪽으로 눕혀 육각 "면"이 정면을 보게(D31 §1.5).
  if (prefs.view === "2d" && n.kind !== "domain" && n.kind !== "concept" && n.kind !== "bridge")
    mesh.rotation.x = Math.PI / 2;
  return mesh;
}

/** 카메라 fly-to (spec: 클릭 = 카메라 fly-to). 2D 평면화에서는 시축을 유지한 직진.
 *
 *  V4 — 거리는 **노드 반경에 비례**한다. 구 공식 `42 + degree` 는 허브(연결 33)에서
 *  오히려 가까워져 카메라가 클러스터 내부로 파고들었다(실측: 화면이 거대 구체와
 *  엣지 벽이 되어 공간 감각이 사라졌다). 반경의 일정 배수로 띄운다.
 *
 *  클릭 핸들러 안에 있던 것을 D70 에서 꺼냈다 — 좌측 트리·검색 착지도 같은 비행을
 *  써야 하는데, 안에 있으면 수식이 두 벌이 된다. */
export function flyTo3d(g: any, n: any, prefs: AtlasPrefs, ms = 700) {
  if (!n) return;
  const flat = prefs.view === "2d";
  const d = Math.max(70, nodeRadius(n, prefs) * 9);
  if (flat) g.cameraPosition({ x: n.fx, y: n.fy, z: d * 2.2 }, { x: n.fx, y: n.fy, z: 0 }, ms);
  else g.cameraPosition({ x: n.fx + d, y: n.fy + d * 0.6, z: n.fz + d }, { x: n.fx, y: n.fy, z: n.fz }, ms);
}

/** 마운트된 그래프에서 id 로 노드를 찾는다(없으면 undefined — 호출측이 강등 판단). */
export function findNode3d(g: any, id: string): any {
  return (g?.graphData?.().nodes || []).find((n: any) => n.id === id);
}

export function mount3d(el: HTMLElement, nodes: N3[], links: L3[], handlers: {
  onClick?: (n: N3) => void; onRightClick?: (n: N3, ev: MouseEvent) => void;
  onLinkClick?: (l: any) => void;
}, prefs: AtlasPrefs = loadPrefs()): ForceGraph3DInstance {
  const flat = prefs.view === "2d";        // D31 — 동일 렌더러의 z 평면화
  const S = SCALE * prefs.spread;
  for (const n of nodes) {   // pin deterministic sidecar coords (D17)
    if (n.x === undefined) continue;
    // 2D 는 직교 투영이 아닌 경사 사영으로 접는다: z 분리를 y 에 보존해
    // z 로만 갈라져 있던(같은 클러스터) 노드들이 평면에서도 겹치지 않는다.
    // 좌표는 동일 사이드카 정본 — 사영 계수만 상수(결정론 유지).
    // 렌더 좌표(x/y/z)에 직접 기록한다 — fx 핀만 두고 엔진 틱의 핀→좌표 복사에
    // 의존하면, digest(비동기)가 메시를 원점에 재생성한 뒤 cooldown 0 으로 틱 없이
    // 멈추는 타이밍에 전 노드가 (0,0,0)에 뭉친다(세부 뷰 2D 미적용의 실측 원인).
    // fx/fy/fz 는 "엔진이 돌아도 못 움직이게" 하는 핀으로 병기한다.
    const px = n.x * S;
    const py = (flat ? (n.y || 0) + (n.z || 0) * 0.55 : (n.y || 0)) * S;
    const pz = flat ? 0 : (n.z || 0) * S;
    n.x = px; n.y = py; n.z = pz;
    n.fx = px; n.fy = py; n.fz = pz;
  }
  // 캔버스 투명(alpha) — 환경 대기(body 그라데이션)가 그래프 뒤로 비친다(D37 보강).
  // 다크는 반전 팔레트(D39): 어두운 배경 + 밝은 잉크 마크 — 양 테마 모두 투명.
  const darkTheme = document.documentElement.dataset.theme === "dark";
  const pal = themePalette();
  // 상시 라벨 대상 — 2D 와 같은 규칙(연결수 상위 8, 고아 제외).
  const hubIds = new Set(nodes.slice()
    .sort((a, b) => (b.degree || 0) - (a.degree || 0) || (a.id < b.id ? -1 : 1))
    .slice(0, 8).filter((n) => (n.degree || 0) > 0).map((n) => n.id));
  assignTiers(nodes, links);   // 성좌 글로우 깊이 계층(92:211) — nodeObject 가 __tier 를 읽는다
  // 세터 체인은 설정 → graphData 마지막: digest 1회에 메시 생성과 카운트다운 리셋이
  // 같은 update 에서 처리되게 해 재생성 경합을 없앤다. 크기는 컨테이너 실측으로 명시
  // (기본값은 마운트 순간 스냅샷이라 스크롤바 등장 뒤 컨테이너보다 커져 넘친다 — 실측).
  const g = new ForceGraph3D(el, { rendererConfig: { alpha: true, antialias: true } } as any)
    .backgroundColor("rgba(0,0,0,0)")
    .showNavInfo(false)
    .cooldownTicks(0)                       // 좌표는 사이드카 정본 — 시뮬레이션 없음
    .nodeThreeObject((n: any) => {
      const mesh = nodeObject(n, prefs, pal, true);
      // V7 — 허브(연결수 상위)는 **상시 라벨**을 단다. 2D 가 이미 쓰는 문법인데
      // 3D 만 hover 를 기다려, 42노드 렌즈가 "이름 없는 별자리"였다(실측: 한 번에
      // 하나씩 짚어야만 이름을 알 수 있었다). 나머지는 그대로 hover 로 뜬다.
      if (prefs.labels !== "none" && hubIds.has(n.id)) mesh.add(labelSprite(n.label, pal));
      return mesh;
    })
    .nodeLabel((n: any) => prefs.labels === "none" ? "" :
      `<span style="${pal.label};border-radius:6px;padding:2px 8px;font-family:system-ui">${n.gateway ? (n.domain + " › ") : ""}${n.label}</span>`)
    // D33 — 엣지 = 헤어라인: linkWidth 미지정(0) = 튜브 메시가 아닌 THREE.Line.
    // 뚱뚱한 반투명 튜브를 제거하고, 가중치는 굵기 대신 잉크 농도로 옮긴다.
    // 완만한 곡률로 겹친 선을 갈라 읽히게 한다(연결의 "부드러움").
    .linkOpacity(0.85)
    .linkCurvature(0.18)
    // 방향 화살촉 — prerequisite/derives/inherits 만 (spec §1.2); 표시 여부는 D31 열린 층
    .linkDirectionalArrowLength((l: any) => prefs.arrows &&
      ["PREREQUISITE_OF", "DERIVES_FROM", "INHERITS_FROM"].includes(l.type || "") ? 2.6 : 0)
    // 화살촉을 도착 노드 "표면 바로 밖"에 띄운다(D33) — 고정 94% 는 큰 노드
    // (허브)에서 메시 안에 파묻힌다. 좌표는 핀 고정이라 선분 길이가 결정론.
    .linkDirectionalArrowRelPos((l: any) => {
      const s = typeof l.source === "object" ? l.source : null;
      const t = typeof l.target === "object" ? l.target : null;
      if (!s || !t || s.fx === undefined || t.fx === undefined) return 0.94;
      const len = Math.hypot(t.fx - s.fx, t.fy - s.fy, (t.fz || 0) - (s.fz || 0));
      if (!len) return 0.94;
      return Math.max(0.5, 1 - (nodeRadius(t, prefs) + 2.5) / len);
    })
    .graphData({ nodes, links });
  if (el.clientWidth > 1 && el.clientHeight > 1) {
    g.width(el.clientWidth).height(el.clientHeight);
  }

  // 대기(fog = paper) + 무채 조명 보정 — fog 는 3D 깊이 단서라 2D 에선 끈다
  const scene = g.scene();
  const r3 = (g as any).renderer?.();
  if (r3) applyEnvironment(r3, scene);   // 유리 반사원(D40)
  scene.fog = flat ? null : new THREE.Fog(pal.fog, S * 2.6, S * 9);   // 원경이 배경으로 "서서히" 잦아듦
  g.lights([
    new THREE.AmbientLight(0xffffff, darkTheme ? 0.9 : 1.1),
    // 반구광(하늘=따뜻한 종이빛 / 지면=중간 잉크) — 플랫 셰이딩 면들에 부드러운
    // 명암 그라데이션을 깔아 "점토" 대신 "판재" 인상을 만든다(D33). 다크는 감광.
    new THREE.HemisphereLight(0xf0f1fa, 0x565b74, darkTheme ? 0.7 : 0.9),
    (() => { const d = new THREE.DirectionalLight(0xf0f1fa, darkTheme ? 1.1 : 1.5); d.position.set(60, 120, 80); return d; })(),
  ]);
  const controls: any = g.controls();
  if (controls) {
    controls.enableDamping = true; controls.autoRotate = false;
    controls.enableRotate = !flat;          // 2D: 팬/줌만 — 회전 잠금
  }
  if (flat) {
    // 정면 카메라 — 화면 x/y 가 곧 레이아웃 x/y
    g.cameraPosition({ x: 0, y: 0, z: S * 2.2 }, { x: 0, y: 0, z: 0 }, 0);
  } else {
    // 초기 프레이밍: 오블리크 각도에서 전체를 담는다(정면 뷰는 육각기둥이 사각으로 보임)
    g.cameraPosition({ x: S * 1.1, y: S * 0.9, z: S * 2.1 }, { x: 0, y: 0, z: 0 }, 0);
  }
  // 결정론 프레이밍(구 setTimeout 250ms 제거): digest(비동기)로 메시가 생길 때까지
  // rAF 로 대기 → d3ReheatSimulation() 으로 위치 동기화 프레임을 1회 강제한다.
  // cooldown 0 의 정지 분기에서도 three-forcegraph 는 노드·링크 위치를 갱신하고,
  // 좌표는 핀 고정이라 이동은 없다(원점 뭉침의 결정적 해소). 그 다음 프레임에 프레이밍.
  let dead = false;
  const settle = (tries: number) => {
    if (dead) return;
    const ns: any[] = g.graphData().nodes;
    if (ns.length && !ns.some((n) => n.__threeObj) && tries < 60) {
      requestAnimationFrame(() => settle(tries + 1)); return;
    }
    (g as any).d3ReheatSimulation?.();
    requestAnimationFrame(() => { if (!dead) (g as any).zoomToFit?.(400, 60); });
  };
  requestAnimationFrame(() => settle(0));
  // 컨테이너 추적 리사이즈 — 창 크기 변경·스크롤바 등장에도 캔버스가 컨테이너와 일치.
  const ro = new ResizeObserver(() => {
    if (!dead && el.clientWidth > 1 && el.clientHeight > 1)
      g.width(el.clientWidth).height(el.clientHeight);
  });
  ro.observe(el);
  const baseDestructor = (g as any)._destructor?.bind(g);
  (g as any)._destructor = () => { dead = true; ro.disconnect(); baseDestructor?.(); };
  // dev 전용 진단 핸들(프로덕션 번들에서는 상수 폴딩으로 제거됨) — 메시 위치·카메라
  // 상태를 수치로 검증하는 데 쓴다(2026-07 세부 뷰 원점 뭉침 회귀 감시).
  if (import.meta.env.DEV) (window as any).__g3 = g;

  if (handlers.onClick) g.onNodeClick((n: any) => {
    flyTo3d(g, n, prefs);
    handlers.onClick!(n);
  });
  if (handlers.onRightClick)
    g.onNodeRightClick((n: any, ev: MouseEvent) => handlers.onRightClick!(n, ev));
  if (handlers.onLinkClick) g.onLinkClick((l: any) => handlers.onLinkClick!(l));

  // hover: 이웃 유지·비이웃 대기 후퇴 — 노드는 rAF 이징 페이드(스냅 금지),
  // 엣지는 hover 노드 비인접분을 paper 로 후퇴(집중 효과 완성, D32).
  const neighbors = new Map<string, Set<string>>();
  for (const l of links) {
    const s = typeof l.source === "object" ? (l.source as any).id : l.source;
    const t = typeof l.target === "object" ? (l.target as any).id : l.target;
    (neighbors.get(s) || neighbors.set(s, new Set()).get(s)!).add(t);
    (neighbors.get(t) || neighbors.set(t, new Set()).get(t)!).add(s);
  }
  let hoverId: string | null = null;
  const linkColorFn = (l: any) => {
    const s = typeof l.source === "object" ? l.source.id : l.source;
    const t = typeof l.target === "object" ? l.target.id : l.target;
    if (hoverId && s !== hoverId && t !== hoverId) return pal.link.fade;   // 배경으로 후퇴
    // D33 — 농도 = 가중치(굵기 개정): 가중 엣지(L0 ties)는 세기에 따라 진하게,
    // 무가중 크로스도메인은 저농도(구 "세선"의 등가), 기본은 중간 잉크. 다크는 반전(D39).
    if (l.weight) return l.weight >= 6 ? pal.link.heavy : l.weight >= 2 ? pal.link.mid : pal.link.base;
    return l.cross ? pal.link.cross : pal.link.base;
  };
  g.linkColor(linkColorFn);
  const fadeTargets = new Map<THREE.Material, number>();
  let raf = 0;
  const tween = () => {
    let live = false;
    fadeTargets.forEach((target, mat) => {
      const d = target - mat.opacity;
      if (Math.abs(d) > 0.01) { mat.opacity += d * 0.25; live = true; }
      else mat.opacity = target;
    });
    raf = live ? requestAnimationFrame(tween) : 0;
  };
  g.onNodeHover((n: any) => {
    hoverId = n ? n.id : null;
    const keep = n ? new Set([n.id, ...(neighbors.get(n.id) || [])]) : null;
    g.graphData().nodes.forEach((m: any) => {
      const obj = m.__threeObj as THREE.Mesh | undefined;
      if (!obj) return;
      const fade = keep ? !keep.has(m.id) : false;
      obj.traverse((c: THREE.Object3D) => {
        const mat = (c as any).material as THREE.Material | undefined;
        if (mat && "opacity" in mat) {   // mesh fill·링·글로우 스프라이트 공통 — 각자 저장한 base 로 복귀
          const b = (mat.userData?.base as number | undefined) ?? (m.gateway ? GATE_OP : NODE_OP);
          mat.transparent = true; fadeTargets.set(mat, fade ? b * 0.15 : b);
        }
      });
    });
    if (!raf) raf = requestAnimationFrame(tween);
    g.linkColor(linkColorFn);   // 접근자 재적용 → 링크색 재평가
    el.style.cursor = n ? "pointer" : "";
  });
  return g;
}

/** Multiples 그리드 3D 미니(D34) — WebGL 컨텍스트는 **1개**: 단일 렌더러가
 *  카드별 씬을 setScissor/setViewport 로 나눠 그린다(브라우저 컨텍스트 상한
 *  ~16 회피). 미니는 정적 오블리크 컷 + hover 시 같은 id 를 전 카드 동시
 *  점등(brushing & linking, 포인트색). 캔버스는 pointer-events:none — 카드
 *  헤더 버튼 클릭은 그대로 통과하고 hover 는 컨테이너 리스너로 받는다. */
export function mountMinis3d(
  container: HTMLElement,
  cells: { el: HTMLElement; nodes: N3[]; links: L3[] }[],
  prefs: AtlasPrefs = loadPrefs(),
): { highlight: (id: string | null) => void; dispose: () => void } {
  const flat = prefs.view === "2d";
  const pal = themePalette();
  const S = 60;
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(window.devicePixelRatio || 1);
  renderer.setScissorTest(true);
  Object.assign(renderer.domElement.style,
    { position: "absolute", inset: "0", pointerEvents: "none" });
  container.style.position = "relative";
  container.appendChild(renderer.domElement);
  const pmrem = new THREE.PMREMGenerator(renderer);
  const envTex = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;   // D40

  type Cell = { scene: THREE.Scene; camera: THREE.PerspectiveCamera;
                el: HTMLElement; meshes: Map<string, THREE.Mesh>;
                rad: number; pad: number; dir: THREE.Vector3 };
  const built: Cell[] = cells.map((c) => {
    const scene = new THREE.Scene();
    scene.environment = envTex;   // 유리 반사원(D40)
    scene.add(new THREE.AmbientLight(0xffffff, 1.1));
    scene.add(new THREE.HemisphereLight(0xf0f1fa, 0x565b74, 0.9));
    const dl = new THREE.DirectionalLight(0xf0f1fa, 1.5);
    dl.position.set(30, 60, 40);
    scene.add(dl);
    // 셀 로컬 정규화: 무게중심을 원점으로(클러스터 오프셋 제거 — z 포함, 3D 컷에서
    // z 오프셋이 크면 rad 가 부풀어 프레이밍이 어긋난다)
    const n0 = Math.max(c.nodes.length, 1);
    const cx = c.nodes.reduce((a, n) => a + (n.x || 0), 0) / n0;
    const cy = c.nodes.reduce((a, n) => a + (n.y || 0), 0) / n0;
    const cz = flat ? 0 : c.nodes.reduce((a, n) => a + (n.z || 0), 0) / n0;
    const pos = new Map<string, THREE.Vector3>();
    const meshes = new Map<string, THREE.Mesh>();
    let rad = 1;
    let maxMeshR = 0;   // 노드 중심 반경에 더할 메시 반경 마진(미니 스케일 0.5 반영)
    for (const n of c.nodes) {
      const p = new THREE.Vector3(((n.x || 0) - cx) * S, ((n.y || 0) - cy) * S,
                                  flat ? 0 : ((n.z || 0) - cz) * S);
      pos.set(n.id, p);
      rad = Math.max(rad, p.length());
      maxMeshR = Math.max(maxMeshR, nodeRadius(n, prefs) * 0.5);
      const m = nodeObject(n, prefs, pal) as THREE.Mesh;
      m.scale.setScalar(0.5);            // 미니 마크는 절반 크기
      m.position.copy(p);
      (m as any).userData.nid = n.id;
      (m as any).userData.base = n.focus ? pal.point : n.gateway ? pal.inkMute : pal.ink;
      meshes.set(n.id, m);
      scene.add(m);
    }
    for (const l of c.links) {
      const a = pos.get(typeof l.source === "object" ? (l.source as any).id : l.source);
      const b = pos.get(typeof l.target === "object" ? (l.target as any).id : l.target);
      if (!a || !b) continue;
      // 본뷰와 동일한 완만한 곡률 — 중점을 수직 방향으로 살짝 띄운 2차 베지어
      const mid = a.clone().add(b).multiplyScalar(0.5)
        .add(new THREE.Vector3(0, 0, flat ? 0 : a.distanceTo(b) * 0.12)
          .add(new THREE.Vector3(0, flat ? a.distanceTo(b) * 0.1 : 0, 0)));
      const curve = new THREE.QuadraticBezierCurve3(a, mid, b);
      const geo = new THREE.BufferGeometry().setFromPoints(curve.getPoints(16));
      scene.add(new THREE.Line(geo, new THREE.LineBasicMaterial(
        { color: l.cross ? pal.link.cross : pal.link.base, transparent: true, opacity: 0.75 })));
    }
    const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 4000);
    // 시선 방향만 여기서 고정 — 거리는 셀 aspect 를 아는 renderAll 에서 확정한다.
    const dir = flat ? new THREE.Vector3(0, 0, 1)
                     : new THREE.Vector3(1.5, 1.1, 2.6).normalize();
    camera.position.copy(dir).multiplyScalar(rad * 3);   // 잠정값
    camera.lookAt(0, 0, 0);
    return { scene, camera, el: c.el, meshes, rad, pad: maxMeshR + 2, dir };
  });

  function renderAll() {
    const crect = container.getBoundingClientRect();
    if (crect.width < 2 || crect.height < 2) return;
    renderer.setSize(crect.width, crect.height, false);
    for (const cell of built) {
      const r = cell.el.getBoundingClientRect();
      if (r.width < 2 || r.height < 2) continue;
      const left = r.left - crect.left;
      const bottom = crect.bottom - r.bottom;
      renderer.setViewport(left, bottom, r.width, r.height);
      renderer.setScissor(left, bottom, r.width, r.height);
      const aspect = r.width / Math.max(r.height, 1);
      // aspect 반영 프레이밍 — 세로 fov 만으로 잡은 고정 거리(구 rad*3)는 세로형
      // 카드에서 가로 시야가 rad 보다 좁아져 좌우 노드가 카드 밖으로 잘렸다(실측).
      // 가로·세로 중 좁은 축이 (rad+pad) 를 담도록 거리를 확정한다.
      const fitDist = ((cell.rad + cell.pad) * 1.12) /
        (Math.tan(THREE.MathUtils.degToRad(cell.camera.fov / 2)) * Math.min(1, aspect));
      cell.camera.position.copy(cell.dir).multiplyScalar(fitDist);
      cell.camera.lookAt(0, 0, 0);
      cell.camera.aspect = aspect;
      cell.camera.updateProjectionMatrix();
      renderer.render(cell.scene, cell.camera);
    }
  }
  // 최초 레이아웃 확정 후 1회 + 컨테이너/창 리사이즈 시 — 미니는 정적 컷(조용한 그리드)
  requestAnimationFrame(renderAll);
  const onResize = () => renderAll();
  window.addEventListener("resize", onResize);
  const ro = new ResizeObserver(onResize);
  ro.observe(container);
  // dev 전용 진단 핸들(프로덕션 번들 제거) — 그리드 fit(투영좌표⊂카드 rect) 수치 검증용.
  if (import.meta.env.DEV) (window as any).__minis = { built, renderAll };

  function highlight(id: string | null) {
    for (const cell of built)
      cell.meshes.forEach((m, nid) => {
        m.traverse((c2: THREE.Object3D) => {
          const mat = (c2 as THREE.Mesh).material as THREE.MeshStandardMaterial | undefined;
          if (!mat) return;
          if (id && nid === id) mat.color.setHex(pal.point);
          else mat.color.setHex((m as any).userData.base ?? pal.ink);
        });
      });
    renderAll();
  }
  // hover 판정: 셀 rect → NDC → 레이캐스트, 같은 id 전 카드 점등
  const ray = new THREE.Raycaster();
  const onMove = (e: MouseEvent) => {
    let hit: string | null = null;
    for (const cell of built) {
      const r = cell.el.getBoundingClientRect();
      if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom)
        continue;
      const ndc = new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1,
                                    -((e.clientY - r.top) / r.height) * 2 + 1);
      ray.setFromCamera(ndc, cell.camera);
      const hits = ray.intersectObjects([...cell.meshes.values()], true);
      for (const h of hits) {
        let o: THREE.Object3D | null = h.object;
        while (o && !(o as any).userData?.nid) o = o.parent;
        if (o) { hit = (o as any).userData.nid; break; }
      }
      break;
    }
    highlight(hit);
  };
  container.addEventListener("mousemove", onMove);

  return {
    highlight,
    dispose() {
      window.removeEventListener("resize", onResize);
      ro.disconnect();
      container.removeEventListener("mousemove", onMove);
      renderer.dispose();
      renderer.domElement.remove();
    },
  };
}

/** Shift+드래그 화면 사각 선택(Phase B lasso) — 3D 노드의 화면 투영 좌표로 포함
 *  판정한다. 드래그 동안 orbit 컨트롤을 잠근다. */
export function enableLasso(g: any, el: HTMLElement, onSelect: (ids: string[]) => void) {
  let box: HTMLDivElement | null = null;
  let start: { x: number; y: number } | null = null;
  el.addEventListener("mousedown", (e) => {
    if (!e.shiftKey) return;
    e.preventDefault(); e.stopPropagation();
    const controls: any = g.controls(); if (controls) controls.enabled = false;
    const r = el.getBoundingClientRect();
    start = { x: e.clientX - r.left, y: e.clientY - r.top };
    box = document.createElement("div");
    box.style.cssText = "position:absolute;border:1px dashed #8f96d9;background:rgba(143,150,217,.08);z-index:5;pointer-events:none";
    el.appendChild(box);
  }, true);
  el.addEventListener("mousemove", (e) => {
    if (!start || !box) return;
    const r = el.getBoundingClientRect();
    const x = e.clientX - r.left, y = e.clientY - r.top;
    box.style.left = Math.min(x, start.x) + "px";
    box.style.top = Math.min(y, start.y) + "px";
    box.style.width = Math.abs(x - start.x) + "px";
    box.style.height = Math.abs(y - start.y) + "px";
  });
  el.addEventListener("mouseup", (e) => {
    if (!start || !box) return;
    const r = el.getBoundingClientRect();
    const x2 = e.clientX - r.left, y2 = e.clientY - r.top;
    const [x0, x1] = [Math.min(start.x, x2), Math.max(start.x, x2)];
    const [y0, y1] = [Math.min(start.y, y2), Math.max(start.y, y2)];
    box.remove(); box = null; start = null;
    const controls: any = g.controls(); if (controls) controls.enabled = true;
    if (x1 - x0 < 8 || y1 - y0 < 8) return;   // 미세 드래그 무시
    const ids = g.graphData().nodes.filter((n: any) => {
      const s2 = g.graph2ScreenCoords(n.fx ?? n.x, n.fy ?? n.y, n.fz ?? n.z);
      return s2.x >= x0 && s2.x <= x1 && s2.y >= y0 && s2.y <= y1;
    }).map((n: any) => n.id);
    if (ids.length) onSelect(ids);
  });
}

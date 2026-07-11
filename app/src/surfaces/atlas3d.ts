/** Atlas 3D renderer (DEC D21 revised — full 3D, owner decision 2026-07-10).
 *
 *  Monochrome 3D discipline (spec: vinc.d.atlas-visual-language §1.1):
 *  depth comes from shading and atmosphere, never hue — matte ink Lambert
 *  materials (ink/ink_soft/ink_mute), paper background + fog, one neutral
 *  directional light. The single point color marks "the current focus" only.
 *
 *  Node primitives = type: hex prism (Concept) · sphere (Episode/Record) ·
 *  octahedron (Bridge) · large hex prism + ring (L0 Domain). Gateways are
 *  translucent. Sidecar supplies deterministic x/y/z (D17) — positions are
 *  pinned (fx/fy/fz), no client force simulation. */
import ForceGraph3D, { type ForceGraph3DInstance } from "3d-force-graph";
import * as THREE from "three";
import { loadPrefs, type AtlasPrefs } from "../atlas-prefs";

const PAPER = 0xefeae1;
const INK = 0x14110d;
const INK_SOFT = 0x47423a;
const INK_MUTE = 0x6a6252;
const POINT = 0x3e4c63;
const SCALE = 140;   // sidecar unit-circle coords → scene units

export type N3 = {
  id: string; label: string; domain?: string; degree?: number;
  kind?: string;              // concept | episode | record | bridge | domain
  gateway?: boolean; focus?: boolean; records?: number;
  pinned?: boolean;           // D32 — pin 은 허브 승격(크기 가중; 레이아웃은 사이드카)
  x?: number; y?: number; z?: number;
  fx?: number; fy?: number; fz?: number;
};
export type L3 = { source: string; target: string; type?: string; cross?: boolean; weight?: number };

function material(color: number, opacity = 1): THREE.Material {
  // D33 — 무광 Lambert(밋밋한 점토 질감)를 플랫 셰이딩 Standard 로 교체:
  // 면 단위 음영으로 각(육각·팔면체의 판형 인상)이 서고, 잉크 도장 같은
  // 단단한 표면이 된다. 색은 그대로 모노크롬(닫힌 층).
  return new THREE.MeshStandardMaterial({
    color, roughness: 0.55, metalness: 0.12, flatShading: true,
    transparent: opacity < 1, opacity,
  });
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

function nodeObject(n: N3, prefs: AtlasPrefs): THREE.Object3D {
  const opacity = n.gateway ? 0.45 : 1;
  const color = n.focus ? POINT : n.gateway ? INK_MUTE : INK;
  const r = nodeRadius(n, prefs);
  let geo: THREE.BufferGeometry;
  switch (n.kind) {
    case "domain":   // 대형 육각기둥 + 외곽 링(이중육각의 입체화)
    case "concept":  // 육각기둥
      geo = new THREE.CylinderGeometry(r, r, r * 0.75, 6); break;
    case "bridge":   // 정팔면체(마름모의 입체화)
      geo = new THREE.OctahedronGeometry(r); break;
    default:         // episode/record — 구
      geo = new THREE.SphereGeometry(r, 20, 14);
  }
  const mesh = new THREE.Mesh(geo, material(color, opacity));
  if (n.kind === "domain") {
    const ring = new THREE.Mesh(
      new THREE.TorusGeometry(r * 1.45, r * 0.06, 8, 6),   // 6각 토러스 링
      material(INK_MUTE, 0.9));
    ring.rotation.x = Math.PI / 2;
    mesh.add(ring);
  }
  // 2D 모드: 기둥류를 카메라 쪽으로 눕혀 육각 "면"이 정면을 보게 — 형태=타입
  // 판독을 평면에서도 유지 (D31 §1.5: 2D 는 동일 렌더러의 z 평면화).
  if (prefs.view === "2d" && (n.kind === "concept" || n.kind === "domain"))
    mesh.rotation.x = Math.PI / 2;
  return mesh;
}

export function mount3d(el: HTMLElement, nodes: N3[], links: L3[], handlers: {
  onClick?: (n: N3) => void; onRightClick?: (n: N3) => void;
  onLinkClick?: (l: any) => void;
}, prefs: AtlasPrefs = loadPrefs()): ForceGraph3DInstance {
  const flat = prefs.view === "2d";        // D31 — 동일 렌더러의 z 평면화
  const S = SCALE * prefs.spread;
  for (const n of nodes) {   // pin deterministic sidecar coords (D17)
    if (n.x === undefined) continue;
    // 2D 는 직교 투영이 아닌 경사 사영으로 접는다: z 분리를 y 에 보존해
    // z 로만 갈라져 있던(같은 클러스터) 노드들이 평면에서도 겹치지 않는다.
    // 좌표는 동일 사이드카 정본 — 사영 계수만 상수(결정론 유지).
    n.fx = n.x * S;
    n.fy = flat ? ((n.y || 0) + (n.z || 0) * 0.55) * S : (n.y || 0) * S;
    n.fz = flat ? 0 : (n.z || 0) * S;
  }
  const g = new ForceGraph3D(el)
    .graphData({ nodes, links })
    .backgroundColor("#efeae1")
    .showNavInfo(false)
    .cooldownTicks(0)                       // 좌표는 사이드카 정본 — 시뮬레이션 없음
    .nodeThreeObject((n: any) => nodeObject(n, prefs))
    .nodeLabel((n: any) => prefs.labels === "none" ? "" :
      `<span style="color:#14110d;background:#f7f4ef;border:1px solid #ded8cc;
       border-radius:6px;padding:2px 8px;font-family:system-ui">${n.gateway ? (n.domain + " › ") : ""}${n.label}</span>`)
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
    });

  // 대기(fog = paper) + 무채 조명 보정 — fog 는 3D 깊이 단서라 2D 에선 끈다
  const scene = g.scene();
  scene.fog = flat ? null : new THREE.Fog(PAPER, S * 2.6, S * 9);   // 원경이 종이로 "서서히" 잦아듦
  g.lights([
    new THREE.AmbientLight(0xffffff, 1.1),
    // 반구광(하늘=따뜻한 종이빛 / 지면=중간 잉크) — 플랫 셰이딩 면들에 부드러운
    // 명암 그라데이션을 깔아 "점토" 대신 "판재" 인상을 만든다(D33).
    new THREE.HemisphereLight(0xfff8ec, 0x9a917d, 0.9),
    (() => { const d = new THREE.DirectionalLight(0xfff8ec, 1.5); d.position.set(60, 120, 80); return d; })(),
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
  setTimeout(() => (g as any).zoomToFit?.(500, 60), 250);

  if (handlers.onClick) g.onNodeClick((n: any) => {
    // 클릭 = fly-to + 위임 (spec: 클릭 = 카메라 fly-to); 2D 는 시축 유지 직진
    const d = 42 + (n.degree || 1);
    if (flat) g.cameraPosition({ x: n.fx, y: n.fy, z: d * 2.2 }, { x: n.fx, y: n.fy, z: 0 }, 700);
    else g.cameraPosition({ x: n.fx + d, y: n.fy + d * 0.6, z: n.fz + d }, { x: n.fx, y: n.fy, z: n.fz }, 700);
    handlers.onClick!(n);
  });
  if (handlers.onRightClick) g.onNodeRightClick((n: any) => handlers.onRightClick!(n));
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
    if (hoverId && s !== hoverId && t !== hoverId) return "#e2dccf";   // paper 후퇴
    // D33 — 농도 = 가중치(굵기 개정): 가중 엣지(L0 ties)는 세기에 따라 진하게,
    // 무가중 크로스도메인은 저농도(구 "세선"의 등가), 기본은 중간 잉크.
    if (l.weight) return l.weight >= 6 ? "#38332a" : l.weight >= 2 ? "#6f6754" : "#8d8574";
    return l.cross ? "#b3a992" : "#8d8574";
  };
  g.linkColor(linkColorFn);
  const fadeTargets = new Map<THREE.MeshLambertMaterial, number>();
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
      const base = m.gateway ? 0.45 : 1;
      obj.traverse((c: THREE.Object3D) => {
        const mat = (c as THREE.Mesh).material as THREE.MeshLambertMaterial | undefined;
        if (mat) { mat.transparent = true; fadeTargets.set(mat, fade ? 0.12 : base); }
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
  const S = 60;
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(window.devicePixelRatio || 1);
  renderer.setScissorTest(true);
  Object.assign(renderer.domElement.style,
    { position: "absolute", inset: "0", pointerEvents: "none" });
  container.style.position = "relative";
  container.appendChild(renderer.domElement);

  type Cell = { scene: THREE.Scene; camera: THREE.PerspectiveCamera;
                el: HTMLElement; meshes: Map<string, THREE.Mesh> };
  const built: Cell[] = cells.map((c) => {
    const scene = new THREE.Scene();
    scene.add(new THREE.AmbientLight(0xffffff, 1.1));
    scene.add(new THREE.HemisphereLight(0xfff8ec, 0x9a917d, 0.9));
    const dl = new THREE.DirectionalLight(0xfff8ec, 1.5);
    dl.position.set(30, 60, 40);
    scene.add(dl);
    // 셀 로컬 정규화: 무게중심을 원점으로(클러스터 오프셋 제거)
    const n0 = Math.max(c.nodes.length, 1);
    const cx = c.nodes.reduce((a, n) => a + (n.x || 0), 0) / n0;
    const cy = c.nodes.reduce((a, n) => a + (n.y || 0), 0) / n0;
    const pos = new Map<string, THREE.Vector3>();
    const meshes = new Map<string, THREE.Mesh>();
    let rad = 1;
    for (const n of c.nodes) {
      const p = new THREE.Vector3(((n.x || 0) - cx) * S, ((n.y || 0) - cy) * S,
                                  flat ? 0 : (n.z || 0) * S);
      pos.set(n.id, p);
      rad = Math.max(rad, p.length());
      const m = nodeObject(n, prefs) as THREE.Mesh;
      m.scale.setScalar(0.5);            // 미니 마크는 절반 크기
      m.position.copy(p);
      (m as any).userData.nid = n.id;
      (m as any).userData.base = n.focus ? POINT : n.gateway ? INK_MUTE : INK;
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
        { color: l.cross ? 0xb3a992 : 0x8d8574, transparent: true, opacity: 0.75 })));
    }
    const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 4000);
    if (flat) camera.position.set(0, 0, rad * 3.0);
    else camera.position.set(rad * 1.5, rad * 1.1, rad * 2.6);
    camera.lookAt(0, 0, 0);
    return { scene, camera, el: c.el, meshes };
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
      cell.camera.aspect = r.width / Math.max(r.height, 1);
      cell.camera.updateProjectionMatrix();
      renderer.render(cell.scene, cell.camera);
    }
  }
  // 최초 레이아웃 확정 후 1회 + 리사이즈 시 — 미니는 정적 컷(조용한 그리드)
  requestAnimationFrame(renderAll);
  const onResize = () => renderAll();
  window.addEventListener("resize", onResize);

  function highlight(id: string | null) {
    for (const cell of built)
      cell.meshes.forEach((m, nid) => {
        m.traverse((c2: THREE.Object3D) => {
          const mat = (c2 as THREE.Mesh).material as THREE.MeshStandardMaterial | undefined;
          if (!mat) return;
          if (id && nid === id) mat.color.setHex(POINT);
          else mat.color.setHex((m as any).userData.base ?? INK);
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
    box.style.cssText = "position:absolute;border:1px dashed #3e4c63;background:rgba(62,76,99,.08);z-index:5;pointer-events:none";
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

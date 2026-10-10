/** View 3D renderer (DEC D21 revised — full 3D, owner decision 2026-07-10).
 *
 *  Monochrome 3D discipline (as-built: [[vinc.reference.view-controls]]):
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
import { loadPrefs, DIRECTED_RELS, type ViewPrefs } from "../view-prefs";
import { palette as signal, viewScene, hexStr, type ThemeMode } from "../theme";
import { tintMap } from "../view-tint";
import { recede } from "../recede";
import { edgeKey, type Spot } from "../spotlight";
import { aimAt, restRadius, zoomStep } from "../camera-aim";
import { HIT_MIN_PX, wireDragConnect, type DragEnd, type DragHit } from "./drag-connect";
import { nonOverlappingLabels, type LabelBox } from "../view-labels";
import type { ViewRoute } from "../view-route";
import { RoutedLinkObject } from "./view-routes3d";

const SCALE = 140;   // sidecar unit-circle coords → scene units

/** 입자 흐름(2026-08-14) — 선을 지우고 그 궤적 위로 **입자만** 흘린다.
 *
 *  헤어볼의 정체는 관계가 아니라 교차하는 **선**이다. 선을 지우면 교차가 안 쌓이고,
 *  흐르는 방향이 곧 엣지 방향이라 화살촉 없이도 방향이 읽힌다.
 *
 *  급소 둘(둘 다 실측으로 확인한 라이브러리 계약이다):
 *   ① `linkVisibility(false)` 로 선을 끄면 **입자도 함께 사라진다** — three-forcegraph 는
 *      photon 을 `visibleLinks` 위에서만 digest 한다. 그래서 선은 **색의 알파**로 지운다:
 *      선 재질의 불투명도는 `linkOpacity × colorAlpha(color)` 인데 입자는
 *      `linkOpacity × 3` 이라 색 알파를 안 탄다. 알파 0 이 선만 지우고 입자는 남긴다.
 *   ② 입자는 source→target 으로만 흐른다. 그래서 **방향 3종에만** 단다 —
 *      무방향(RELATED_TO·CONNECTS)에 달면 없는 방향을 있다고 말하게 된다. */
const ALPHA0 = "00";                      // #RRGGBB + 알파 → tinycolor 가 8자리 hex 로 읽는다
const PARTICLES_PER_LINK = 2;
const PARTICLE_SPEED = 0.006;             // 링크 길이 대비 프레임당 이동분
const PARTICLE_WIDTH = 1.1;
const isDirectedLink = (l: { type?: string }) => DIRECTED_RELS.includes(l.type || "");

/** Signal 팔레트 — 토큰과 같은 원천(Vinc-design 허브 gen_theme_ts 생성물).
 *  다크(기본) = 무채색 검정 위 흰 포커스. 라이트 = 그레인+잉크(2026-09-24 흑백).
 *  themePalette()가 유일한 런타임 seam — 하드코딩 hex 금지. */
type Palette = {
  ink: number; inkMute: number; point: number; fog: number; primary: number;
  /** 유리 스튜디오(studioEnv)의 회색 판들 — 전부 토큰이다(히어로의 무채색 램프와 같은 단). */
  soft: number; line: number; card: number;
  link: { heavy: string; mid: string; base: string; cross: string; fade: string };
  label: string;
};
// 조명과 재질의 기준색. 하늘은 라이트 배경 토큰을 빌리고, 흰색과 지면만 리터럴이다.
// ds-allow: 흰색은 곱셈 항등원(틴트 없음)이고 지면은 무채색 gray_700 이다(2026-09-24 흑백 규칙,
// 예전 값 0xf0f1fa·0x565b74 는 푸른 기가 있었다). 팔레트에 조명용 항목이 없어 여기 둔다.
const WHITE = 0xffffff, GROUND = 0x565652;
const SKY = signal.light.background;
const themeMode = (): ThemeMode =>
  document.documentElement.dataset.theme === "dark" ? "dark" : "light";
function themePalette(): Palette {
  const mode = themeMode();
  const p = signal[mode];
  return {
    ink: p.text, inkMute: p.textMute, primary: p.primary,
    // 초점색은 이제 **토큰**이다(`semantic.*.focus`). 값은 그대로(다크 페리윙클 ·
    // 라이트 로고 인디고)지만, 여기서 모드 분기를 손으로 쓰던 동안 같은 초점이
    // 타임라인에서는 `--ring`(양 모드 페리윙클)이라 라이트에서 두 색이었다.
    point: p.focus,
    fog: p.background,
    soft: p.textSoft, line: p.line, card: p.card,
    link: p.linkRamp, label: p.labelCss,
  };
}

export type N3 = {
  id: string; label: string; domain?: string; degree?: number;
  kind?: string;              // concept | decision | episode | record | bridge | domain
  gateway?: boolean; focus?: boolean; records?: number;
  pinned?: boolean;           // D32 — pin 은 허브 승격(크기 가중; 레이아웃은 사이드카)
  x?: number; y?: number; z?: number;
  fx?: number; fy?: number; fz?: number;
};
// D77/M4 — `weight` 는 사문이었다: 렌더러가 농도 램프를 그 값으로 갈랐는데
// `toL3` 가 한 번도 싣지 않아(사이드카 `shape_graph` 도 보내지 않는다) heavy/mid
// 갈래가 **도달 불가능**했다. 삭제하는 대신 실제로 흐르는 신호로 갈아 끼운다 —
// `confidence`(E-G6, 0~1)는 백엔드가 이미 엣지에 싣고 GraphEdge 로 앱까지 온다.
export type L3 = { source: string; target: string; type?: string; cross?: boolean;
  id?: string; confidence?: number; evidence?: string; role?: string;
  route?: ViewRoute; presentation?: boolean };

// 재질의 이력: D77(2026-08-03)이 노드의 유리를 은퇴시켰고, 2026-08-15 에 반무광 MeshPhysical 로
// 돌아왔고, 2026-09-26 에 히어로와 같은 유광 유리가 됐다(아래 GLASS). D37 Liquid Glass 는
// 크롬 층의 것이라 이 흐름과 무관하다.
//: 정사면체의 고정 자세. 앱 노드는 자전하지 않으므로 이 각도가 곧 실루엣이다 —
//: face·view dot 0.76 / 0.21 로 두 면의 음영이 갈려 입체로 읽힌다(시안 §2).
const DOC_ROT: [number, number, number] = [-0.5, 0.6, 0.2];

// ── 대량 노드 인스턴싱 ──────────────────────────────────────────────────────
// 노드마다 개별 지오메트리 + MeshPhysicalMaterial 로 그리면 드로우콜과 셰이딩 비용이
// 노드 수에 비례한다. 입자 모드를 고르면 F6 유휴 정지를 못 받아 그 비용을 매 프레임
// 낸다(기본은 아니다 — edgeShowFor). 그래서 몸통이 종류(구·팔면체·사면체)만 다른 노드는 종류당
// InstancedMesh 하나로 묶는다. gateway·domain·focus 는 형태가 특수하고 수가 적어
// 개별 메시로 남긴다. 재질 파라미터(색 제외)는 skinFor("body")와 같다 — 2026-08-15 의
// MeshPhysical 결정은 그대로 두고 그리는 방식만 바꾼다.
// 근거·기각안: [[decision:view-3d-nodes-are-instanced-and-recede-by-blending-to-the-background]]
// 실측: [[record:view-3d-instancing-frame-cost-measured-2026-09-18]]
type InstKind = "sphere" | "bridge" | "doc";
const instKindOf = (n: N3): InstKind =>
  n.kind === "concept" || n.kind === "decision" ? "sphere"
  : n.kind === "bridge" ? "bridge" : "doc";
//: 인스턴싱 대상에서 뺀다 — gateway(윤곽선, 이미 저가) · domain(겹껍질 특수형) ·
//: focus(초점색 특례, 뷰당 보통 0~1개).
const canInstance = (n: N3): boolean => !n.gateway && n.kind !== "domain" && !n.focus;
const UNIT_GEOM: Record<InstKind, THREE.BufferGeometry> = {
  sphere: new THREE.SphereGeometry(1, 32, 22),   // 유리 하이라이트가 각지지 않을 해상도(히어로와 같다)
  bridge: new THREE.OctahedronGeometry(1.06),
  doc: new THREE.TetrahedronGeometry(1.22),
};
const INST_ROT: Record<InstKind, THREE.Quaternion> = {
  sphere: new THREE.Quaternion(),
  bridge: new THREE.Quaternion().setFromEuler(new THREE.Euler(0, 0.5, 0)),
  doc: new THREE.Quaternion().setFromEuler(new THREE.Euler(...DOC_ROT)),
};
/** 유리 한 벌 — 공홈 히어로(Vinc-homepage `site/assets/hero-orbit.js` 의 MAT·ENV_I)와 같은
 *  값이다. 광택(거칠기 .1~.14), 클리어코트 가득, 박막 무지갯빛, 환경 반사가 조형을 만든다.
 *  **색은 없다**: 몸통은 잉크, 초점만 포인트(흑백 + 포인트 하나). 히어로의 보석색은 사이트
 *  조형 안만의 예외라 가져오지 않는다. 라이트는 반사를 줄인다 — 밝은 바탕에서 반사가 세면
 *  조형이 배경에 녹는다(히어로 2026-09-13 실측).
 *  근거: [[decision:app-nodes-wear-the-hero-glass-2026-09-26]] */
const GLASS: Record<ThemeMode, { roughness: number; metalness: number; clearcoat: number;
  clearcoatRoughness: number; iridescence: number; sheen: number; ior: number;
  env: number; exposure: number }> = {
  dark: { roughness: .14, metalness: .08, clearcoat: 1, clearcoatRoughness: .05,
          iridescence: .75, sheen: .25, ior: 1.6, env: 1.35, exposure: 1.06 },
  light: { roughness: .1, metalness: .04, clearcoat: 1, clearcoatRoughness: .04,
           iridescence: .55, sheen: .08, ior: 1.55, env: .8, exposure: 1.02 },
};
function glassParams(pal: Palette, mode: ThemeMode): THREE.MeshPhysicalMaterialParameters {
  const g = GLASS[mode];
  return {
    roughness: g.roughness, metalness: g.metalness,
    clearcoat: g.clearcoat, clearcoatRoughness: g.clearcoatRoughness, ior: g.ior,
    iridescence: g.iridescence, iridescenceIOR: 1.45, iridescenceThicknessRange: [180, 820],
    sheen: g.sheen, sheenColor: new THREE.Color(pal.soft), sheenRoughness: .4,
    specularIntensity: 1, envMapIntensity: g.env,
  };
}
/** 테마 전환 — 지은 재질에 그 모드의 유리 값을 다시 쓴다(오브젝트는 그대로). */
function reglaze(m: THREE.MeshPhysicalMaterial, pal: Palette, mode: ThemeMode): void {
  m.setValues(glassParams(pal, mode));
  m.needsUpdate = true;
}
/** 인스턴스 재질. 색은 흰색 기준(인스턴스 색이 곱해진다) — 나머지는 skinFor("body")와
 *  같은 유리다. 단 **불투명**으로 둔다: 투명으로 바꾸면 수백 인스턴스가 투명 패스로 가서
 *  조기 깊이 기각을 잃고, 한 InstancedMesh 안은 깊이 정렬도 안 된다.
 *
 *  노드 몸통은 **제 색 그대로다** — hover·점등에 배경색으로 섞이지 않고(`applyFade`
 *  는 라벨만 물린다) 거리 안개도 안 받는다(`fog: false`). 깊이 단서는 엣지의 안개가 진다.
 *  근거: [[decision:a-node-keeps-its-own-colour-in-every-state]] */
function instMaterial(pal: Palette): THREE.MeshPhysicalMaterial {
  return new THREE.MeshPhysicalMaterial({ color: WHITE, fog: false, ...glassParams(pal, themeMode()) });
}
/** 레이캐스트 전용 유령 메시 — 실제로 그리지 않는다(`visible=false`, 렌더러가
 *  순회에서 건너뛴다). three-forcegraph 가 노드마다 요구하는 오브젝트 계약(위치
 *  추적·hover·클릭)만 채운다. 지오메트리는 노드 형태와 무관한 저폴리 구 하나로
 *  충분하다 — 안 보이는 히트 영역이라 실루엣이 몸통과 정확히 같을 필요가 없다. */
function ghostNode(r: number): THREE.Mesh {
  const m = new THREE.Mesh(new THREE.SphereGeometry(r, 8, 6), new THREE.MeshBasicMaterial());
  m.visible = false;
  return m;
}
/** 인스턴스 하나의 장부 — nodeScale 슬라이더(아래 __update)가 이 레퍼런스로
 *  인스턴스 행렬을 다시 쓴다. */
type InstRef = {
  kind: InstKind; idx: number; mesh: THREE.InstancedMesh; baseR: number;
};

/** 발광 패널 + PMREM — 이미지 없이 굽는 스튜디오 환경(외부 요청 0). 배치와 세기는 히어로의
 *  `bakeEnv` 와 같고, 색은 전부 무채색 토큰이다(히어로의 장미창 보석색은 뺐다). 유리의
 *  광택 면이 비출 것이 있어야 조형이 선다 — 환경이 없으면 유리는 검게 죽는다.
 *
 *  ⚠ 캐시는 **렌더러마다, 모드마다** 따로다. 패널 색이 팔레트라 모드가 바뀌면 다른
 *  그림이고, PMREM 결과는 렌더 타깃 텍스처라 그것을 구운 WebGL 컨텍스트 안에서만 산다.
 *  근거: [[decision:a-theme-switch-repaints-the-open-view-in-place]] */
const envCache = new WeakMap<THREE.WebGLRenderer, Map<ThemeMode, THREE.Texture>>();
function studioEnv(renderer: THREE.WebGLRenderer, pal: Palette, mode: ThemeMode): THREE.Texture {
  let byMode = envCache.get(renderer);
  if (!byMode) envCache.set(renderer, byMode = new Map());
  const hit = byMode.get(mode);
  if (hit) return hit;
  const light = mode === "light";
  const sc = new THREE.Scene();
  //: 스튜디오 벽. 라이트를 어둡게 켜는 이유: 재질이 PBR 이라 환경이 색을 밀어 올리는데,
  //: 밝은 바탕에서 그러면 조형이 배경에 녹는다.
  sc.background = new THREE.Color(light ? pal.inkMute : pal.fog);
  const panel = (w: number, h: number, d: number, color: number, k: number,
                 pos: [number, number, number], rot?: [number, number, number]) => {
    const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d),
      new THREE.MeshBasicMaterial({ color: new THREE.Color(color).multiplyScalar(k) }));
    m.position.set(...pos); if (rot) m.rotation.set(...rot); sc.add(m);
  };
  //: 키 — 라이트는 작고 세게(넓게 밝히면 뿌옇고, 좁고 센 빛이 날카로운 하이라이트가 된다).
  panel(light ? 7 : 12, light ? 7 : 12, .1, light ? WHITE : pal.soft, light ? 2.6 : 3.2,
        [0, 6.5, -3], [Math.PI / 2.6, 0, 0]);
  panel(9, 9, .1, WHITE, light ? .72 : 1.5, [-6, 2, 4], [0, Math.PI / 2.4, 0]);        // 보조
  panel(9, 9, .1, pal.inkMute, 1.1, [6.5, 1, 3], [0, -Math.PI / 2.4, 0]);              // 가장자리
  panel(14, .1, 14, light ? pal.line : pal.card, light ? .55 : 1, [0, -4, 0]);         // 바닥
  const pm = new THREE.PMREMGenerator(renderer);
  pm.compileEquirectangularShader();
  const tex = pm.fromScene(sc, .04).texture;
  pm.dispose();
  byMode.set(mode, tex);
  return tex;
}

/** 앱 재질 = **히어로와 같은 유리 한 벌**(위 GLASS). 2026-08-15 에 돌아온 MeshPhysical 을
 *  반무광에서 유광으로 올렸다(2026-09-26 발주자 지시: 공홈 노드와 같은 품질).
 *  몸통 색은 **주제의 보석색**이고(`tint`, view-tint.ts), 주제가 없으면 잉크다. 초점만
 *  포인트다 — 초점색은 "지금 초점" 하나만 뜻하므로 주제가 덮으면 안 된다(정본 §2.5).
 *  ⚠ 잉크가 `primary`(인디고)이던 동안 다크의 알베도 대비가 APCA Lc 9.6 으로 불가시 하한
 *  (Lc 16) 아래였다 — 주제 없는 몸통은 계속 `ink` 다. */
function skinFor(pal: Palette, kind: "body" | "core" | "shell", focus: boolean,
                 tint?: string | null): THREE.Material {
  const mode = themeMode();
  const base = focus ? pal.point : tint ? parseInt(tint.slice(1), 16) : pal.ink;
  const m = new THREE.MeshPhysicalMaterial({
    color: base, ...glassParams(pal, mode),
    transparent: true, opacity: 1,
    fog: false,          // 몸통은 거리로 옅어지지 않는다(instMaterial 주석)
  });
  if (kind === "core") { m.emissive = new THREE.Color(base); m.emissiveIntensity = .3; }
  //: 겉껍질은 밝은 바탕에서 조금 더 진해야 읽힌다(히어로와 같은 .28 / .26).
  if (kind === "shell") { m.opacity = mode === "light" ? .28 : .26; m.depthWrite = false; }
  m.userData.base = m.opacity;
  return m;
}

/** 테마 전환 때 개별 몸통(Gateway·Domain·초점)의 재질을 갈아 끼운다(`__retheme`).
 *  `fresh` 는 같은 공장(nodeObject)이 같은 노드로 방금 지은 것이라 순회 순서가 `obj` 와
 *  같다. 색 규칙을 여기서 다시 쓰지 않는 이유가 그것이다: 규칙이 두 벌이면 새로 연
 *  화면과 전환한 화면이 조용히 갈린다. 라벨 스프라이트는 공장 밖에서 붙으므로 셈에서
 *  빠진다(그쪽은 repaintLabel). 오브젝트는 그대로라 자리·크기가 안 움직인다. */
function takeSkins(obj: THREE.Object3D, fresh: THREE.Object3D): void {
  const skins = (o: THREE.Object3D) => {
    const out: (THREE.Mesh | THREE.LineSegments)[] = [];
    o.traverse((c) => { if (c instanceof THREE.Mesh || c instanceof THREE.LineSegments) out.push(c); });
    return out;
  };
  const from = skins(fresh), to = skins(obj);
  const fits = from.length === to.length;
  from.forEach((c, i) => {
    c.geometry.dispose();
    if (!fits) { (c.material as THREE.Material).dispose(); return; }
    (to[i].material as THREE.Material).dispose();
    to[i].material = c.material;
  });
}

//: 초점으로부터의 홉 거리 계층. 조형에는 더 이상 안 쓰이지만 계산은 남는다(아래).
type Tier = "focus" | "near" | "mid" | "far";

// 성좌 글로우와 가시성 링은 **폐기됐다**(발주자 지시 2026-08-15). 강조는 빛이 아니라
// 색 하나로 한다 — 그래서 글로우 텍스처·링 스프라이트·최소 두께 보정이 함께 사라졌다.
// (그 색은 한때 초점색의 **보색**이었다. 같은 날 뒤에 초점색을 전 뷰 한 벌로 모으기로
// 하면서 보색 유도는 폐기됐다 — 초점이 뷰마다 다른 색이던 것이 고치려던 문제였다.)
// 계층(assignTiers 의 __tier)은 남는다: 조형에는 안 쓰지만 초점으로부터의 홉 거리
// 자체는 다른 곳이 읽는다.
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
    // 절대 문턱(구 `deg >= 8`)은 그래프마다 안 맞는다. 실측(개념 162 그래프): 허브라
    // 부를 만한 `concept:view` 조차 degree 4 라 near 에 못 들었고, DEC·에피소드·
    // 레코드 대다수가 degree 1 이라 far 로 몰렸다. 분위수로 잡으면 그래프 규모와
    // 무관하게 "상위가 near" 가 된다 — 정렬만 하므로 결정론(D17)은 그대로다.
    const degs = nodes.map((n) => n.degree || 0).sort((a, b) => a - b);
    const q = (p: number) => (degs.length ? degs[Math.min(degs.length - 1, Math.floor(degs.length * p))] : 0);
    const nearAt = Math.max(2, q(0.85));            // 상위 15% 목표(최소 2 — 잎은 near 가 아니다)
    const midAt = Math.max(1, Math.min(nearAt - 1, q(0.5)));   // 그 아래 · 고아(0)만 far
    for (const n of nodes) {
      const deg = n.degree || 0;
      (n as any).__tier = (n.pinned || deg >= nearAt ? "near" : deg >= midAt ? "mid" : "far") as Tier;
    }
  }
}

// D77 — `applyEnvironment`(PMREM + RoomEnvironment 로 만든 유리 반사원)는 유리와
// 함께 은퇴했다. scene.environment 는 PBR 재질만 읽으므로 Lambert 전환 후에는
// 매 마운트 환경맵을 굽는 비용만 남고 화면에 나타나지 않는 순수 사문이었다.

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
  sp.userData.label = text;                       // repaintLabel 이 다시 구울 원문
  return sp;
}

/** 라벨의 글자를 새 잉크로 다시 굽는다. 스프라이트(자리·크기·물러남 불투명도)는
 *  그대로 두고 텍스처만 바꾼다 — 글자 폭은 잉크와 무관해 크기가 안 변한다. */
function repaintLabel(sp: THREE.Sprite, pal: Palette): void {
  const text = sp.userData.label as string | undefined;
  if (text === undefined) return;
  const fresh = labelSprite(text, pal);
  sp.material.map?.dispose();
  sp.material.map = fresh.material.map;
  sp.material.needsUpdate = true;
  fresh.material.dispose();                       // 텍스처는 옮겼으니 재질만 버린다
}

/** 노드 렌더 반경 — nodeObject(기하)와 화살촉 오프셋(D33)이 공유하는 단일 정의. */
function nodeRadius(n: N3, prefs: ViewPrefs): number {
  // D31 열린 층 — 크기 스케일만 사용자 선택(√연결수 ↔ 균일); 형태=타입은 닫힌 층.
  let r = n.kind === "domain"
    ? 6 + 2.2 * Math.sqrt(n.records || 1)
    : prefs.size === "uniform" ? 5
    : Math.min(3 + 1.4 * Math.sqrt(n.degree || 1), 12);
  if (n.pinned) r *= 1.25;   // pin = 허브 승격(D32) — 확정된 노드는 한 급 크게
  // 2D 는 깊이 겹침 여지가 없어 조밀 — 반경을 줄여 겹침 비율 자체를 낮춘다
  if (prefs.view === "2d" && n.kind !== "domain") r *= 0.7;
  // 사용자 배율(D31 열린 층) — **맨 마지막에** 곱한다. 위의 인코딩(무엇이 크고
  // 작은가)을 건드리지 않고 전체 크기만 옮긴다.
  return r * (prefs.nodeScale ?? 1);
}

/** 조형 = 노드종. 형태가 뜻을 나른다 — 문서류는 정사면체(구보다 작게 읽히고 Bridge
 *  의 정팔면체와 실루엣이 삼각:마름모로 갈린다), Domain 은 겹껍질(담는 그릇), Gateway
 *  는 윤곽선(면이 없다 = 실체가 없다. 배후를 안 가리는 것이 이 형태를 고른 이유다).
 *  as-built: [[vinc.reference.view-controls]].
 *
 *  ⚠ 시안의 재질표(반무광 + 발광패널 환경)는 **적용하지 않았다** — 실측으로 은퇴시킨
 *  것을 되살리는 결정이라 조형 변경과 같은 무게가 아니다. */
function kindGeometry(kind: string | undefined, r: number): THREE.BufferGeometry {
  switch (kind) {
    case "concept": case "decision": case "domain":
      return new THREE.SphereGeometry(r, 32, 22);
    case "bridge":
      return new THREE.OctahedronGeometry(r * 1.06);
    default:
      return new THREE.TetrahedronGeometry(r * 1.22);
  }
}

/** Gateway = 자기 종의 **윤곽선**. 구는 에지가 격자로 뭉개지므로 이코사 detail 1 로
 *  대체한다(시안 §2). 색은 팔레트의 mute — 하드코딩 hex 금지(게이트가 잡는다). */
function gatewayWire(kind: string | undefined, r: number, pal: Palette): THREE.LineSegments {
  const src = (kind === "concept" || kind === "decision" || kind === "domain")
    ? new THREE.IcosahedronGeometry(r, 1) : kindGeometry(kind, r);
  const mat = new THREE.LineBasicMaterial({ color: pal.inkMute, transparent: true, opacity: 0.5,
                                            fog: false });
  mat.userData.base = 0.5;
  const seg = new THREE.LineSegments(new THREE.EdgesGeometry(src), mat);
  if (kind !== "concept" && kind !== "decision" && kind !== "domain" && kind !== "bridge")
    seg.rotation.set(...DOC_ROT);
  if (kind === "bridge") seg.rotation.y = 0.5;
  return seg;
}

/** Domain 겹껍질 — 외피(반투명, depthWrite 끔)와 코어. `skin` 이 두 재질을 만든다.
 *  외피가 depthWrite 를 쓰면 그 안의 코어와 뒤 노드가 함께 지워진다. */
function domainShell(r: number, skin: (o: number) => THREE.Material): THREE.Group {
  const g = new THREE.Group();
  const shellMat = skin(0.28);
  (shellMat as any).depthWrite = false;
  g.add(new THREE.Mesh(new THREE.IcosahedronGeometry(r * 1.18, 3), shellMat));
  g.add(new THREE.Mesh(new THREE.SphereGeometry(r * 0.52, 32, 22), skin(1)));
  return g;
}

function nodeObject(n: N3, prefs: ViewPrefs, pal: Palette = themePalette(),
                    _glow = false): THREE.Object3D {
  const r = nodeRadius(n, prefs);
  //: 주제 틴트 — 꺼져 있으면(기본) undefined 라 잉크 그대로다. 표는 마운트가 만들어
  //: 노드에 실어 준다(`__tint`): 노드마다 전체 주제 목록을 다시 훑을 수 없기 때문이다.
  const tint = (n as { __tint?: string }).__tint;
  //: 강조는 **보색 하나**로 한다(발주자 지시 2026-08-15). 빛나는 형태 — 글로우
  //: 스프라이트와 가시성 링 — 은 전부 폐기됐다. D75·D76 이 링에 지웠던 짐(먼
  //: 거리에서의 가시성)은 이제 재질과 환경맵이 진다. 계층(__tier)은 조형에
  //: 관여하지 않는다. 갈래가 하나로 줄어든 것이 이 변경의 부수 이득이다.
  if (n.gateway) return gatewayWire(n.kind, r, pal);
  if (n.kind === "domain")
    return domainShell(r, (o) => skinFor(pal, o < 1 ? "shell" : "core", !!n.focus, tint));
  const mesh = new THREE.Mesh(kindGeometry(n.kind, r), skinFor(pal, "body", !!n.focus, tint));
  if (n.kind === "bridge") mesh.rotation.y = 0.5;
  else if (n.kind !== "concept" && n.kind !== "decision") mesh.rotation.set(...DOC_ROT);
  return mesh;
}

/** 카메라 **회전만** — 노드를 화면 중심에 놓되 거리는 건드리지 않는다.
 *
 *  옛 `flyTo3d` 가 한 손짓에 **두 가지**를 했다: 시선 대상을 노드로 옮기고(중심),
 *  거리를 반경의 9배로 만든다(줌). 고르는 것과 파고드는 것은 다른 일이라 뒤엣것을
 *  카드의 줌 버튼으로 내렸고, 그러자 그 함수는 부르는 곳이 없어져 사라졌다.
 *
 *  수식은 `camera-aim.ts` 에 있다 — **import 0 이라 손 없이 돌려 볼 수 있다**
 *  (`node --experimental-strip-types app/dev/aim-check.ts`). 여기 두면 이 파일이
 *  렌더러를 통째로 끌고 오므로 검사할 방법이 없어진다. 이 화면은 픽셀을 못 본 채로
 *  나간 전례가 있어서(v0.8.5 계획 §7) 그 정도는 갖춰 둔다.
 *
 *  `atRest` 는 지금 안 쓴다 — 줌아웃이 `zoomBy3d` 로 넘어갔다. 인자를 남긴 이유는
 *  "기본 거리로 물러난다"가 여전히 이 수식의 한 갈래이기 때문이고, aim-check 가 그
 *  갈래를 덮고 있다. */
export function centerOn3d(g: any, n: any, prefs: ViewPrefs, ms = 700, atRest = false) {
  if (!n) return;
  const node = { x: n.fx ?? n.x ?? 0, y: n.fy ?? n.y ?? 0, z: n.fz ?? n.z ?? 0 };
  const rest = atRest ? restRadius(SCALE * prefs.spread) : undefined;
  const aim = aimAt(node, g.cameraPosition?.(), prefs.view === "2d", rest);
  if (!aim) return;                              // 뿌리 — 이미 중심이라 옮길 것이 없다
  g.__wake?.(ms + 200);                          // 트윈은 렌더 루프 안 — 깨워야 움직인다
  g.cameraPosition(aim.pos, aim.tgt, ms);
}

/** 줌 — **회전 없이** 거리만. 방향과 시선 대상은 손대지 않는다.
 *
 *  울타리: 가까이는 씬 크기의 1/8, 멀리는 기본 거리의 4배. 기본 거리(restRadius)가
 *  첫 마운트의 자리이므로 그 네 배면 "한참 물러난" 자리이고, 그보다 멀어지면 노드가
 *  점이 되어 볼 것이 없다. */
export function zoomBy3d(g: any, prefs: ViewPrefs, factor: number, ms = 400): void {
  const cam = g.cameraPosition?.();
  const tgt = g.controls?.()?.target ?? { x: 0, y: 0, z: 0 };
  if (!cam) return;
  const S = SCALE * prefs.spread;
  const pos = zoomStep(cam, tgt, factor, S / 8, restRadius(S) * 4);
  if (!pos) return;
  g.__wake?.(ms + 200);
  g.cameraPosition(pos, { x: tgt.x, y: tgt.y, z: tgt.z }, ms);
}

/** 마운트된 그래프에서 id 로 노드를 찾는다(없으면 undefined — 호출측이 강등 판단). */
export function findNode3d(g: any, id: string): any {
  return (g?.graphData?.().nodes || []).find((n: any) => n.id === id);
}

/** 재마운트 사이에 넘겨 주는 카메라 자리. 옵션 하나를 고칠 때마다 씬을 새로 만드는데,
 *  그때마다 전체 맞춤으로 되감기면 보던 곳을 잃는다(실측 2026-08-17). 좌표가 그대로인
 *  재마운트(layout·cone 이 아닌 옵션)만 이 값을 실어 보낸다 — 좌표가 바뀌면 옛 카메라는
 *  아무것도 안 보는 자리일 수 있다. */
export type Cam3 = { pos: { x: number; y: number; z: number }; tgt: { x: number; y: number; z: number } };
export function camera3d(g: ForceGraph3DInstance): Cam3 | null {
  const c: any = g.controls();
  const p: any = (g as any).cameraPosition?.();
  if (!c?.target || !p) return null;
  return { pos: { x: p.x, y: p.y, z: p.z }, tgt: { x: c.target.x, y: c.target.y, z: c.target.z } };
}

export function mount3d(el: HTMLElement, nodes: N3[], links: L3[], handlers: {
  onClick?: (n: N3) => void; onRightClick?: (n: N3, ev: MouseEvent) => void;
  onLinkClick?: (l: any) => void;
  /** Alt 끌기로 두 노드를 골랐다(끌어서 잇기). 쓰기는 호출측의 관계 고르기가 한다. */
  onConnect?: (a: DragEnd, b: DragEnd) => void;
  canConnect?: (a: string, b: string) => boolean;
}, prefs: ViewPrefs = loadPrefs(), keepCam: Cam3 | null = null,
   tintDomains: (string | undefined)[] | null = null): ForceGraph3DInstance {
  const mountedAt = performance.now();
  const flat = prefs.view === "2d";        // D31 — 동일 렌더러의 z 평면화
  let S = SCALE * prefs.spread;
  /** 사이드카 원좌표(단위원)를 노드에 남겨 둔다 — 아래 `place` 가 배율만 바꿔 다시
   *  놓을 수 있게(간격 슬라이더를 제자리에서 반영, 2026-08-17). n.x 는 렌더 좌표로
   *  덮어쓰므로 원본은 여기 말고는 없다. */
  type Raw = { __x0?: number; __y0?: number; __z0?: number };
  const place = () => {
    for (const n of nodes) {
      const r = n as N3 & Raw;
      if (r.__x0 === undefined) {
        if (n.x === undefined) continue;
        r.__x0 = n.x; r.__y0 = n.y || 0; r.__z0 = n.z || 0;
      }
      const px = r.__x0 * S;
      const py = (flat ? r.__y0! + r.__z0! * 0.55 : r.__y0!) * S;
      const pz = flat ? 0 : r.__z0! * S;
      n.x = px; n.y = py; n.z = pz;
      n.fx = px; n.fy = py; n.fz = pz;
    }
  };
  // pin deterministic sidecar coords (D17).
  // 2D 는 직교 투영이 아닌 경사 사영으로 접는다: z 분리를 y 에 보존해
  // z 로만 갈라져 있던(같은 클러스터) 노드들이 평면에서도 겹치지 않는다.
  // 좌표는 동일 사이드카 정본 — 사영 계수만 상수(결정론 유지).
  // 렌더 좌표(x/y/z)에 직접 기록한다 — fx 핀만 두고 엔진 틱의 핀→좌표 복사에
  // 의존하면, digest(비동기)가 메시를 원점에 재생성한 뒤 cooldown 0 으로 틱 없이
  // 멈추는 타이밍에 전 노드가 (0,0,0)에 뭉친다(세부 뷰 2D 미적용의 실측 원인).
  // fx/fy/fz 는 "엔진이 돌아도 못 움직이게" 하는 핀으로 병기한다.
  place();
  el.dataset.viewLayout = "elk-temporal-v1";
  // 캔버스 투명(alpha) — 환경 대기(body 그라데이션)가 그래프 뒤로 비친다(D37 보강).
  // 다크는 반전 팔레트(D39): 어두운 배경 + 밝은 잉크 마크 — 양 테마 모두 투명.
  // `let` 인 이유: 테마가 뒤집히면 `__retheme` 이 갈아 끼운다. 아래 접근자들(선색·라벨·
  // 입자)은 이 변수를 **부를 때** 읽으므로 새 팔레트를 저절로 따른다.
  let pal = themePalette();
  // 입자는 **상시 애니메이션**이다. 모션 감도를 밝힌 사용자에게는 켜지 않고 선으로
  // 되돌린다 — 이 모드의 이득(덜 붐빔)이 그 비용을 정당화하지 못하는 유일한 경우다.
  const reduceMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
  const particlesOn = prefs.edges === "particles" && !reduceMotion;
  //: 노드만 — 선을 그리지 않는다(기본). 관계는 점등과 카드가 말한다.
  //: 근거: [[decision:bring-and-go-is-retired-and-nodes-only-is-the-default]]
  const nodesOnly = prefs.edges === "none";
  // 상시 라벨 대상 — 2D 와 같은 규칙(연결수 상위 8, 고아 제외).
  const hubIds = new Set(nodes.slice()
    .sort((a, b) => (b.degree || 0) - (a.degree || 0) || (a.id < b.id ? -1 : 1))
    .slice(0, 8).filter((n) => (n.degree || 0) > 0).map((n) => n.id));
  const hubLabels = new Map<string, THREE.Sprite>();
  const hubLabel = (n: N3) => {
    const sprite = labelSprite(n.label, pal);
    hubLabels.set(n.id, sprite);
    return sprite;
  };
  // 주제 틴트(열린 층) — 마운트당 한 번 표를 만들어 노드에 실어 둔다. 각도가
  // 화면에 있는 주제들 위에서 배정되므로(view-tint.ts) 노드 하나만 보고는
  // 계산할 수 없다.
  // 순위를 매길 주제 목록은 호출부가 주면 그것을 쓴다(`tintDomains`). 받은 `nodes` 는
  // 고아 숨김으로 걸러진 것이라, 거기서 지으면 주제 하나가 빠질 때 나머지의 색이
  // 밀려 노드 카드·다른 관점과 같은 주제가 다른 색이 된다.
  const applyTints = () => {
    const tints = tintMap(tintDomains ?? nodes.map((n) => n.domain), themeMode());
    for (const n of nodes) (n as { __tint?: string }).__tint = tints?.get((n.domain || "").trim());
  };
  applyTints();
  /** 인스턴스 몸통의 색 — 주제 틴트가 있으면 그것, 없으면 잉크. 빌드와 테마 전환이
   *  같은 규칙을 읽는다. */
  const instColor = (n: N3) => {
    const tint = (n as { __tint?: string }).__tint;
    return new THREE.Color(tint ? parseInt(tint.slice(1), 16) : pal.ink);
  };
  // 인스턴싱 상태 — scene 은 ForceGraph3D 생성 뒤에야 얻는다(아래), 그래서 최초
  // 빌드에서는 scene?.add 가 아무 일도 안 하고, 생성 직후 명시적으로 한 번 더 붙인다.
  let scene: THREE.Scene | undefined;
  let instMeshes: THREE.InstancedMesh[] = [];
  const instRefs = new Map<string, InstRef>();
  const instState = new Map<string, string>();
  // 지오메트리는 빌드마다 복제본이고 재질도 빌드마다 새로 만든다 — 둘 다 여기서 함께
  // 버린다(InstancedMesh.dispose 는 인스턴스 버퍼만 놓는다).
  const disposeInstMeshes = () => {
    for (const im of instMeshes) {
      scene?.remove(im);
      im.geometry.dispose();
      (im.material as THREE.Material).dispose();
      im.dispose();
    }
    instMeshes = [];
    instRefs.clear();
  };
  const buildInstancedNodes = () => {
    instState.clear();
    disposeInstMeshes();
    const counts: Record<InstKind, number> = { sphere: 0, bridge: 0, doc: 0 };
    for (const n of nodes) if (canInstance(n)) counts[instKindOf(n)]++;
    const built: Partial<Record<InstKind, THREE.InstancedMesh>> = {};
    (Object.keys(counts) as InstKind[]).forEach((k) => {
      if (!counts[k]) return;
      const geom = UNIT_GEOM[k].clone();
      const mesh = new THREE.InstancedMesh(geom, instMaterial(pal), counts[k]);
      // 인스턴스가 씬 전체에 흩어져 있어 컬링용 바운딩구는 전체를 감싸야 정확한데,
      // nodeScale 슬라이더로 개별 행렬을 나중에 또 고친 뒤에도 그
      // 값이 조용히 갱신 안 될 수 있다(재계산은 null 일 때만 지연 실행). 드로우콜이
      // 이제 종류당 하나뿐이라 컬링 이득보다 "각도에 따라 조용히 사라짐" 위험이 크다.
      mesh.frustumCulled = false;
      built[k] = mesh; instMeshes.push(mesh);
    });
    const cursor: Record<InstKind, number> = { sphere: 0, bridge: 0, doc: 0 };
    const m4 = new THREE.Matrix4();
    for (const n of nodes) {
      if (!canInstance(n)) continue;
      const kind = instKindOf(n);
      const mesh = built[kind]!;
      const idx = cursor[kind]++;
      const r = nodeRadius(n, prefs);
      m4.compose(new THREE.Vector3(n.x || 0, n.y || 0, n.z || 0), INST_ROT[kind],
                 new THREE.Vector3(r, r, r));
      mesh.setMatrixAt(idx, m4);
      mesh.setColorAt(idx, instColor(n));
      instRefs.set(n.id, { kind, idx, mesh, baseR: r / (prefs.nodeScale ?? 1) });
    }
    scene?.add(...instMeshes);
  };
  buildInstancedNodes();
  let builtScale = prefs.nodeScale ?? 1;         // 마지막으로 메시를 구웠을 때의 배율
  /** 노드 메시 공장. **새 함수 객체**를 넘기면 3d-force-graph 가 노드 오브젝트를 전부
   *  다시 만든다 — 크기 인코딩·틴트·라벨을 제자리에서 갈아 끼우는 값싼 길이다
   *  (씬·렌더러·컨텍스트는 그대로). 배율(nodeScale)은 여기 안 넣고 scale 로 곱한다.
   *
   *  인스턴싱 대상(canInstance)은 진짜 몸통 대신 안 보이는 레이캐스트용 유령을
   *  돌려준다 — 실제 몸통은 위 InstancedMesh 가 그린다(그려지는 것과 hover/클릭이
   *  물리는 오브젝트가 분리된다). 허브 라벨은 유령의 자식이 아니라 **보이는 빈
   *  그룹의 형제**로 단다(유령이 invisible 이라 자식째 렌더 순회에서 빠지므로) —
   *  위치 추적과 hover 페이드(아래 applyFade 의 traverse)는 그대로 얻는다. */
  const nodeFactory = () => {
    hubLabels.clear();
    return (n: any) => {
      if (canInstance(n)) {
        const ghost = ghostNode(nodeRadius(n, prefs));
        // 유령은 `visible=false` 라 렌더러가 순회에서 자식째 건너뛴다 — 허브 라벨을
        // 유령의 자식으로 달면 라벨도 함께 안 그려진다. 라벨이 있는 노드만 보이는
        // 빈 그룹으로 감싸 유령과 라벨을 형제로 둔다(위치 추적은 그룹이 받는다).
        if (prefs.labels !== "none" && hubIds.has(n.id)) {
          const group = new THREE.Group();
          group.add(ghost, hubLabel(n));
          return group;
        }
        return ghost;
      }
      const mesh = nodeObject(n, prefs, pal, true);
      // V7 — 허브(연결수 상위)는 **상시 라벨**을 단다. 2D 가 이미 쓰는 문법인데
      // 3D 만 hover 를 기다려, 42노드 렌즈가 "이름 없는 별자리"였다(실측: 한 번에
      // 하나씩 짚어야만 이름을 알 수 있었다). 나머지는 그대로 hover 로 뜬다.
      if (prefs.labels !== "none" && hubIds.has(n.id)) mesh.add(hubLabel(n));
      return mesh;
    };
  };
  // D33 — 엣지 = 헤어라인이 **기본**이다: linkWidth 0 = 튜브 메시가 아닌 THREE.Line.
  // 가중치는 여전히 굵기가 아니라 잉크 농도가 말한다(그 구분이 D33 의 요지다).
  // 사용자 배율(2026-08-02)은 그 위에 얹는 전역 손잡이일 뿐이다: 1 이면 헤어라인
  // 그대로(0)이고, 올릴 때만 튜브로 굵어진다 — 기본 화면은 D33 이 정한 모습 그대로.
  const linkWidthFn = () => () => {
    const s = prefs.edgeScale ?? 1;
    return s <= 1 ? 0 : (s - 1) * 0.8;
  };
  // 방향 화살촉 — prerequisite/derives/inherits 만 (spec §1.2); 표시 여부는 D31 열린 층
  const arrowLenFn = () => (l: any) =>
    prefs.arrows && !particlesOn && !nodesOnly && isDirectedLink(l) ? 2.6 : 0;
  assignTiers(nodes, links);   // 성좌 글로우 깊이 계층(92:211) — nodeObject 가 __tier 를 읽는다
  // 세터 체인은 설정 → graphData 마지막: digest 1회에 메시 생성과 카운트다운 리셋이
  // 같은 update 에서 처리되게 해 재생성 경합을 없앤다. 크기는 컨테이너 실측으로 명시
  // (기본값은 마운트 순간 스냅샷이라 스크롤바 등장 뒤 컨테이너보다 커져 넘친다 — 실측).
  const g = new ForceGraph3D(el, { rendererConfig: { alpha: true, antialias: true } } as any)
    .backgroundColor("rgba(0,0,0,0)")
    .showNavInfo(false)
    .cooldownTicks(0)                       // 좌표는 사이드카 정본 — 시뮬레이션 없음
    .nodeThreeObject(nodeFactory())
    .nodeLabel((n: any) => prefs.labels === "none" ? "" :
      `<span style="${pal.label};border-radius:6px;padding:2px 8px;font-family:system-ui">${n.gateway ? (n.domain + " › ") : ""}${n.label}</span>`)
    // D33 — 엣지 = 헤어라인이 **기본**이다: linkWidth 0 = 튜브 메시가 아닌 THREE.Line.
    // 가중치는 여전히 굵기가 아니라 잉크 농도가 말한다(그 구분이 D33 의 요지다).
    // 사용자 배율(2026-08-02)은 그 위에 얹는 전역 손잡이일 뿐이다: 1 이면 헤어라인
    // 그대로(0)이고, 올릴 때만 튜브로 굵어진다 — 기본 화면은 D33 이 정한 모습 그대로.
    .linkWidth(linkWidthFn())
    .linkOpacity(0.85)
    .linkCurvature((l: any) => l.route ? 0 : .18)
    .linkThreeObject((l: any) => l.route ? new RoutedLinkObject(l.route) : undefined as any)
    .linkPositionUpdate((_object: THREE.Object3D, _ends: unknown, l: any) => !!l.route)
    // 방향 화살촉 — prerequisite/derives/inherits 만 (spec §1.2); 표시 여부는 D31 열린 층
    .linkDirectionalArrowLength((l: any) => l.route ? 0 : arrowLenFn()(l))
    // 입자 모드 — 방향 3종에만(위 상수 주석 ②). 흐름이 방향을 말하므로 화살촉은 끈다.
    .linkDirectionalParticles((l: any) => !l.route && particlesOn && isDirectedLink(l)
      ? PARTICLES_PER_LINK : 0)
    .linkDirectionalParticleWidth(PARTICLE_WIDTH)
    .linkDirectionalParticleSpeed(PARTICLE_SPEED)
    .linkDirectionalParticleColor(() => pal.link.heavy)
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
  // ⚠ 크기를 바꾼 뒤엔 컨트롤에도 알려야 한다. TrackballControls 는 생성 시점의 요소
  // 사각형을 `screen` 에 캐시하고 `handleResize()` 를 불러야만 갱신한다. 안 부르면
  // 레이아웃 전 크기(실측 240×120)로 포인터를 환산해 캔버스(1568×1073)와의 비율만큼
  // 회전·이동이 과민해진다 — 180px 끌어 92~98°, 갱신 후 15~20°(2026-08-17).
  // `g.width()` 는 다음 digest 에서야 캔버스에 닿으므로 여기서 바로 handleResize 를
  // 불러도 옛 크기를 읽는다(실측: 창 크기 1800×1125 가 그대로 남았다). 그래서 **캔버스
  // 자체**를 관찰해, 실제로 크기가 바뀐 뒤에 컨트롤을 맞춘다(아래 ResizeObserver).
  const sizeTo = () => {
    if (el.clientWidth > 1 && el.clientHeight > 1) g.width(el.clientWidth).height(el.clientHeight);
  };
  sizeTo();

  // 대기(fog = paper) + 무채 조명 보정 — fog 는 3D 깊이 단서라 2D 에선 끈다
  scene = g.scene();
  // Cull billboards before the renderer builds its visible-object list. This
  // follows camera movement, resize and dragging without a second animation loop.
  const labelWorld = new THREE.Vector3(), labelScale = new THREE.Vector3();
  const labelCamera = new THREE.Vector3(), labelNdc = new THREE.Vector3();
  const instMatrix = new THREE.Matrix4();
  scene.onBeforeRender = (_renderer, _scene, camera) => {
    const nodeVisibility: any = g.nodeVisibility(), linkVisibility: any = g.linkVisibility();
    const isVisible = (rule: any, value: any) => typeof rule === "function" ? !!rule(value)
      : typeof rule === "string" ? !!value[rule] : rule !== false;
    for (const n of nodes) {
      const ref = instRefs.get(n.id);
      if (!ref) continue;
      const r = isVisible(nodeVisibility, n) ? ref.baseR * (prefs.nodeScale ?? 1) : 0;
      const key = `${n.x},${n.y},${n.z},${r},${ref.idx}`;
      if (instState.get(n.id) === key) continue;
      instState.set(n.id, key);
      instMatrix.compose(new THREE.Vector3(n.x || 0, n.y || 0, n.z || 0), INST_ROT[ref.kind], new THREE.Vector3(r, r, r));
      ref.mesh.setMatrixAt(ref.idx, instMatrix); ref.mesh.instanceMatrix.needsUpdate = true;
    }
    for (const link of links as any[]) {
      const object = link.__lineObj;
      if (!(object instanceof RoutedLinkObject)) continue;
      const source = typeof link.source === "object" ? link.source : nodes.find(n => n.id === link.source);
      const target = typeof link.target === "object" ? link.target : nodes.find(n => n.id === link.target);
      if (!source || !target) continue;
      object.update(source, target, S, { color: linkColorFn(link), width: linkWidthFn()(),
        arrow: !!prefs.arrows && !particlesOn && !nodesOnly && isDirectedLink(link),
        particles: particlesOn && isDirectedLink(link), particleColor: pal.link.heavy,
        visible: isVisible(linkVisibility, link) && isVisible(nodeVisibility, source) && isVisible(nodeVisibility, target),
        targetRadius: nodeRadius(target, prefs) }, performance.now());
    }
    const width = el.clientWidth, height = el.clientHeight;
    const boxes: LabelBox[] = [];
    for (const id of hubIds) {
      const sprite = hubLabels.get(id);
      if (!sprite?.parent?.visible) continue;
      sprite.getWorldPosition(labelWorld);
      labelCamera.copy(labelWorld).applyMatrix4(camera.matrixWorldInverse);
      if (labelCamera.z >= 0) continue;
      labelNdc.copy(labelWorld).project(camera);
      if (labelNdc.z < -1 || labelNdc.z > 1) continue;
      sprite.getWorldScale(labelScale);
      const halfW = Math.abs(labelScale.x * camera.projectionMatrix.elements[0] / labelCamera.z) * width / 4;
      const halfH = Math.abs(labelScale.y * camera.projectionMatrix.elements[5] / labelCamera.z) * height / 4;
      const x = (labelNdc.x + 1) * width / 2, y = (1 - labelNdc.y) * height / 2;
      const box = { id, left: x - halfW, right: x + halfW, top: y - halfH, bottom: y + halfH };
      if (box.right >= 0 && box.left <= width && box.bottom >= 0 && box.top <= height) boxes.push(box);
    }
    const visible = nonOverlappingLabels(boxes);
    hubLabels.forEach((sprite, id) => { sprite.visible = visible.has(id); });
  };
  scene.add(...instMeshes);   // buildInstancedNodes() 최초 호출분 — scene 이 이제 있다
  // 환경맵이 돌아왔다(발주자 결정 2026-08-15). D77 이 유리와 함께 은퇴시켰던 것인데,
  // 반무광 재질의 clearcoat·sheen 이 반사할 것이 없으면 조형이 평면으로 죽는다.
  scene.environment = studioEnv(g.renderer(), pal, themeMode());
  g.renderer().toneMapping = THREE.ACESFilmicToneMapping;
  g.renderer().toneMappingExposure = GLASS[themeMode()].exposure;
  // 원경이 배경으로 "서서히" 잦아듦. near/far 는 아래에서 **카메라 거리에 연동**한다
  // (고정값이면 축소가 곧 소멸이다 — syncCamera 주석 참조).
  const fog = flat ? null : new THREE.Fog(pal.fog, S * 2.6, S * 9);
  scene.fog = fog;
  /** 조명 한 벌 — 히어로 무대(three-d-stage.js)와 같은 짜임: 반구광 + 키 + 보조, 앰비언트
   *  없음. 앰비언트가 크면 모든 면이 같은 밝기로 떠서 유리가 평판으로 죽는다(예전 .9~1.1).
   *  세기만 모드를 따른다 — 테마 전환이 같은 함수로 다시 건다. */
  const lightsFor = (dark: boolean) => [
    new THREE.HemisphereLight(SKY, GROUND, dark ? 0.8 : 1.0),
    (() => { const d = new THREE.DirectionalLight(WHITE, dark ? 1.8 : 2.2); d.position.set(60, 120, 80); return d; })(),
    (() => { const d = new THREE.DirectionalLight(SKY, 0.5); d.position.set(-80, 30, 60); return d; })(),
  ];
  g.lights(lightsFor(themeMode() === "dark"));
  const controls: any = g.controls();
  // 컨트롤은 3d-force-graph 기본 **TrackballControls** 다 — enableDamping·autoRotate·
  // enableRotate 는 OrbitControls 의 이름이라 여기선 아무것도 안 했다(2026-08-17 실측:
  // 세 줄 다 무효, 평면 잠금도 안 걸렸다). 트랙볼의 이름은 `noRotate`.
  if (controls) controls.noRotate = flat;   // 2D: 팬/줌만 — 회전 잠금
  // ── fog 카메라 적응 (가시성 연구 C1·R1) ──────────────────────────────────
  // 여태 near/far 가 씬 크기(S)에만 묶인 상수라, 기본 프레이밍의 카메라 거리(≈S·2.53)가
  // 이미 near(S·2.6) 코앞이었다. 휠로 축소하면 전 노드가 near~far 구간에 들어가 배경색으로
  // 소멸했다 — 발주자가 본 "축소하면 완전히 어두워짐"의 주범이다.
  // 거리에 비례시키면 **화면에서 선명한 구간의 비율이 줌에 불변**이 된다. 계수는 심화
  // 연구 권장 구간(k1 0.6~0.8 · k2 2.0~2.5)의 중앙값 — 타깃 앞쪽까지 선명하고(0.7<1)
  // 씬 외곽에서만 잦아든다(2.2>1).
  const FOG_NEAR_K = 0.7, FOG_FAR_K = 2.2;
  // 링 최소 두께 보정은 **함께 폐기됐다**(2026-08-15). 링이 없으니 지킬 두께가 없다.
  // 남은 것은 fog 뿐이고, 그건 깊이 단서라 카메라를 계속 따라간다.
  const syncCamera = () => {
    const cam: any = g.camera();
    const tgt = controls?.target as THREE.Vector3 | undefined;
    const dist = tgt ? cam.position.distanceTo(tgt) : cam.position.length();
    if (!(dist > 0)) return;
    // ELK maps can exceed the renderer's fixed sky/camera range. Fit and zoom
    // must not clip the actual marks while near routed sections remain visible.
    const extent = Math.max(S, ...nodes.map(n => Math.hypot((n.x || 0) - (tgt?.x || 0),
      (n.y || 0) - (tgt?.y || 0), (n.z || 0) - (tgt?.z || 0)) + nodeRadius(n, prefs)));
    cam.far = Math.max(2000, dist + extent * 2);
    cam.updateProjectionMatrix();
    if (controls && "maxDistance" in controls) controls.maxDistance = Math.max(5000, extent * 8);
    if (fog) { fog.near = dist * FOG_NEAR_K; fog.far = dist * FOG_FAR_K; }
  };
  syncCamera();
  // 매 프레임이 아니라 **카메라가 실제로 움직일 때만** 갱신한다(비용 무시 가능).
  controls?.addEventListener?.("change", syncCamera);
  if (keepCam) {
    // 옵션 재마운트 — 보던 자리 그대로. 전체 맞춤은 첫 마운트만 한다(아래 settle).
    g.cameraPosition(keepCam.pos, keepCam.tgt, 0);
  } else if (flat) {
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
    requestAnimationFrame(() => {
      if (dead) return;
      wake(700);
      console.info(`View scene ready ${JSON.stringify({ renderer: "3d", nodes: nodes.length, edges: links.length,
        elapsedMs: Math.round(performance.now() - mountedAt) })}`);
      if (!keepCam) (g as any).zoomToFit?.(400, 60);
      // zoomToFit 은 카메라를 코드로 옮기므로 controls 의 change 가 안 나올 수 있다.
      // 애니메이션(400ms)이 끝난 뒤 fog 를 새 거리에 맞춘다.
      setTimeout(() => { if (!dead) syncCamera(); }, 460);
    });
  };
  requestAnimationFrame(() => settle(0));
  // 컨테이너 추적 리사이즈 — 창 크기 변경·스크롤바 등장에도 캔버스가 컨테이너와 일치.
  const ro = new ResizeObserver(() => {
    if (dead) return;
    sizeTo();                                    // 컨테이너가 변했으면 캔버스를 맞추고
    (g.controls() as any)?.handleResize?.();     // 캔버스가 변했으면 컨트롤을 맞춘다
  });
  ro.observe(el);
  ro.observe(g.renderer().domElement);           // 캔버스 변화는 digest 뒤에 온다(위 주석)

  // ── 필요할 때만 그린다(F6, 2026-08-17) ───────────────────────────────────
  // 좌표는 핀 고정이라 아무 일도 없을 때 화면은 한 픽셀도 안 바뀌는데, three-render-
  // objects 의 틱은 매 프레임 render() 를 부른다(실측: 유휴 3D 가 59fps 로 GPU 를 태운다).
  // "움직일 이유"가 있는 동안만 깨어 있는다: 포인터·휠·카메라 변화·hover 페이드·비행·
  // 옵션 반영은 각자 wake(ms) 를 부르고, 마감이 지나면 pauseAnimation(). 입자 모드는
  // 상시 애니메이션이라 재우지 않는다. resumeAnimation 은 첫 프레임을 곧장 그린다.
  let awakeUntil = performance.now() + 2500;      // 마운트·프레이밍이 끝날 때까지
  let napping = false;
  const wake = (ms = 300) => {
    awakeUntil = Math.max(awakeUntil, performance.now() + ms);
    if (napping) { napping = false; (g as any).resumeAnimation?.(); }
  };
  const napTimer = window.setInterval(() => {
    if (dead || particlesOn || napping) return;
    if (performance.now() > awakeUntil) { napping = true; (g as any).pauseAnimation?.(); }
  }, 200);
  const onWakeEvent = () => wake(300);
  for (const ev of ["pointermove", "pointerdown", "wheel", "keydown"] as const)
    el.addEventListener(ev, onWakeEvent, { passive: true });
  controls?.addEventListener?.("change", onWakeEvent);
  (g as any).__wake = wake;                       // 바깥(f 키·flyToMounted)에서도 깨운다
  // ── 끌어서 잇기(Alt 끌기) ─────────────────────────────────────────────────
  // 찾기는 영역 선택과 같은 화면 투영이다 — 노드가 인스턴스 메시라 레이캐스트로는 못 잡는다.
  // 반지름은 노드 반경을 화면 오른쪽 방향으로 한 번 더 투영해 잰다(멀면 작게 잡힌다).
  // 카메라 뒤의 노드와 시간 커서가 숨긴 노드는 잡지 않는다.
  const unwireConnect = handlers.onConnect ? wireDragConnect(el, {
    hitAt: (x, y) => {
      const cam: THREE.Camera = g.camera();
      const right = new THREE.Vector3().setFromMatrixColumn(cam.matrixWorld, 0);
      const fwd = cam.getWorldDirection(new THREE.Vector3());
      const at = new THREE.Vector3(), rel = new THREE.Vector3();
      const vis: any = g.nodeVisibility();
      let best: DragHit | null = null, bestD = Infinity;
      for (const n of nodes) {
        if (typeof vis === "function" ? !vis(n) : vis === false) continue;
        at.set(n.x || 0, n.y || 0, n.z || 0);
        if (rel.copy(at).sub(cam.position).dot(fwd) <= 0) continue;
        const s = g.graph2ScreenCoords(at.x, at.y, at.z);
        const d = Math.hypot(s.x - x, s.y - y);
        if (d >= bestD) continue;
        const r = nodeRadius(n, prefs);
        const e = g.graph2ScreenCoords(at.x + right.x * r, at.y + right.y * r, at.z + right.z * r);
        const rp = Math.max(HIT_MIN_PX, Math.hypot(e.x - s.x, e.y - s.y));
        if (d <= rp) { best = { id: n.id, title: n.label, x: s.x, y: s.y, r: rp }; bestD = d; }
      }
      return best;
    },
    // 끄는 동안 회전·이동·줌이 멎는다. 노드 끌기는 pointerdown 이 닿지 않아 애초에 안 선다.
    lock: (on) => { if (controls) controls.enabled = !on; wake(300); },
    onConnect: handlers.onConnect,
    allowed: handlers.canConnect,
  }) : null;
  const baseDestructor = (g as any)._destructor?.bind(g);
  (g as any)._destructor = () => {
    dead = true; ro.disconnect();
    unwireConnect?.();
    controls?.removeEventListener?.("change", syncCamera);
    window.clearInterval(napTimer);
    controls?.removeEventListener?.("change", onWakeEvent);
    for (const ev of ["pointermove", "pointerdown", "wheel", "keydown"] as const)
      el.removeEventListener(ev, onWakeEvent);
    disposeInstMeshes();
    if (raf) cancelAnimationFrame(raf);
    baseDestructor?.();
    // dispose() 만으로는 WebGL 컨텍스트가 GC 때까지 살아 있다 — 옵션 클릭 16번에
    // "Too many active WebGL contexts" 가 났다(2026-08-17). 명시적으로 잃게 한다.
    try { g.renderer()?.forceContextLoss?.(); } catch { /* 이미 잃었으면 무시 */ }
  };
  // dev 전용 진단 핸들(프로덕션 번들에서는 상수 폴딩으로 제거됨) — 메시 위치·카메라
  // 상태를 수치로 검증하는 데 쓴다(2026-07 세부 뷰 원점 뭉침 회귀 감시).
  if (import.meta.env.DEV) (window as any).__g3 = g;

  /** 옵션을 **제자리에서** 반영한다(2026-08-17). 노드·엣지 집합이 그대로인 변경만
   *  받고, 그 밖(선 모드·연결 없음·범례 필터·layout·cone)은 false 로 돌려 호출측이
   *  다시 마운트하게 한다. 배율은 메시 scale 로, 굵기·화살촉은 세터 재호출로, 라벨·
   *  틴트·크기 인코딩은 노드 오브젝트 재생성으로 — 씬·렌더러·컨텍스트·카메라는 그대로다. */
  (g as any).__update = (np: ViewPrefs): boolean => {
    const prev = prefs;
    const changed = (Object.keys(np) as (keyof ViewPrefs)[])
      .filter((k) => JSON.stringify(prev[k]) !== JSON.stringify(np[k]));
    const ok = new Set<keyof ViewPrefs>(
      ["nodeScale", "edgeScale", "arrows", "labels", "size", "spread", "railW"]);
    if (!changed.every((k) => ok.has(k))) return false;
    prefs = np;
    if (changed.includes("spread")) {
      S = SCALE * np.spread;
      place();                                   // 핀 좌표를 새 배율로 다시 놓고
      (g as any).d3ReheatSimulation?.();        // 위치 동기화 프레임을 1회 강제(settle 과 같은 길)
    }
    if (changed.some((k) => k === "labels" || k === "size")) {
      applyTints();
      builtScale = np.nodeScale ?? 1;
      buildInstancedNodes();                     // 인스턴스 배치도 새 인코딩으로 다시 굽는다
      g.nodeThreeObject(nodeFactory());          // 새 함수 = 노드 오브젝트 전부 다시 굽기
    } else if (changed.includes("nodeScale")) {
      instState.clear();
      // 반지름을 다시 굽지 않고 메시를 곱한다 — 라벨·글로우도 같이 커지지만 슬라이더
      // 한 프레임에 175개 지오메트리를 새로 만드는 것보다 훨씬 싸다.
      const k = (np.nodeScale ?? 1) / builtScale;
      for (const n of g.graphData().nodes as any[]) n.__threeObj?.scale.setScalar(k);
      // 인스턴스 노드는 유령의 scale 이 아니라 행렬 자체를 다시 쓴다(안 그리므로
      // 무의미) — baseR(빌드 시점 nodeScale=1 기준 반지름)에 새 배율만 곱해 절대값을
      // 다시 세운다(반복 호출에도 누적 오차가 안 생기게).
      const m4 = new THREE.Matrix4();
      for (const n of nodes) {
        const ref = instRefs.get(n.id);
        if (!ref) continue;
        const r = ref.baseR * (np.nodeScale ?? 1);
        m4.compose(new THREE.Vector3(n.x || 0, n.y || 0, n.z || 0), INST_ROT[ref.kind],
                   new THREE.Vector3(r, r, r));
        ref.mesh.setMatrixAt(ref.idx, m4);
        ref.mesh.instanceMatrix.needsUpdate = true;
      }
    }
    if (changed.includes("edgeScale") && links.some(l => !l.route)) g.linkWidth(linkWidthFn());
    if (changed.includes("arrows")) g.linkDirectionalArrowLength((l: any) => l.route ? 0 : arrowLenFn()(l));
    wake(600);                                     // 바뀐 것을 그릴 프레임
    return true;
  };

  /** 점등을 밖에서 건다(`__update` 와 같은 관례 — mount3d 는 라이브러리 타입을
   *  돌려주므로 확장은 전부 `__` 접두 패치다).
   *
   *  ⚠ **`wake()` 가 이 함수의 존재 이유의 절반이다.** 이 렌더러는 할 일이 없으면
   *  ~300ms 뒤 스스로 틱을 멈춘다(F6). 점등을 거는 손은 렌즈 패널 안에 있어 캔버스에
   *  포인터 이벤트가 하나도 안 가므로, 깨우지 않으면 **계산은 맞는데 화면이 그대로**
   *  다. 이 기능이 조용히 안 되는 가장 그럴듯한 경로가 여기다. */
  (g as any).__spotlight = (s: Spot | null): void => {
    spot = s;
    if (!hoverId) applyFade(s ? s.nodes : null);
    g.linkColor(linkColorFn);
    wake(600);
  };

  g.onNodeClick((n: any) => {
    // 클릭은 **중심에 놓기만** 한다(줌 없음). 파고드는 것은 카드의 줌 버튼이다 —
    // 고르는 손짓 하나가 카메라까지 끌고 들어가면 되돌릴 방법이 손에 없었다.
    centerOn3d(g, n, prefs);
    handlers.onClick?.(n);
  });
  g.onNodeDrag(() => wake(600));
  g.onNodeDragEnd((n: any) => {
    n.fx = n.x; n.fy = n.y; n.fz = n.z;
    n.__x0 = n.x / S; n.__y0 = n.y / S; n.__z0 = n.z / S;
    wake(600);
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
  /** 밖에서 건 점등 집합(범례 갈래 hover · 인과 원뿔). **hover 가 이긴다** — 손이
   *  가리키는 것이 언제나 지금 관심사라, 2D 와 같은 규칙이고 우선순위 코드가 없다. */
  let spot: Spot | null = null;
  const linkColorFn = (l: any) => {
    const s = typeof l.source === "object" ? l.source.id : l.source;
    const t = typeof l.target === "object" ? l.target.id : l.target;
    // 노드만 — 선이 없다. hover 로도 돌아오지 않는다(그것이 이 모드의 뜻이다).
    if (nodesOnly) return pal.link.base + ALPHA0;
    const near = spot && !hoverId
      ? spot.edges.has(edgeKey({ source: s, target: t, type: l.type || "" }))
      : !hoverId || s === hoverId || t === hoverId;
    // 입자 모드 — 선은 알파 0 으로 지운다(상수 주석 ①). 방향 3종은 입자가 대신
    // 말하고, 무방향은 **짚었을 때만** 선으로 잠깐 돌아온다. 그래서 지워도 잃지 않는다:
    // 관계는 그대로 있고, 늘 그려져 있지 않을 뿐이다.
    //
    // 점등은 이 모드를 **건드리지 않는다**(위 `near` 계산은 지나가되 여기서 갈린다):
    // 선이 이미 알파 0 이라 물러날 자리가 없고, 물러남을 그리려면 없던 선을 새로
    // 그려야 한다 — 지우려던 잉크를 점등이 도로 늘리는 셈이 된다. 이 모드에서
    // 점등이 말하는 것은 노드 쪽이다(아래 applyFade).
    if (particlesOn) {
      if (isDirectedLink(l)) return pal.link.heavy + ALPHA0;
      return hoverId && near ? pal.link.fade : pal.link.base + ALPHA0;
    }
    if (spot && !hoverId && !near) return pal.link.fade;
    if (hoverId && !near) return pal.link.fade;   // 배경으로 후퇴
    // 짚은 노드에 닿은 선은 포인트색 — 2D 와 같은 규칙(view2d 도 짚은 노드의 선을 point() 로 칠한다).
    if (hoverId && near) return hexStr(pal.point);
    // D33 — 농도 = 관계의 세기(굵기 개정). 다크는 반전(D39).
    // B1(2026-08-03): 그 "세기"를 **기록된 증거의 신뢰도**(E-G6 confidence)가 말한다.
    // 여태 이 램프는 `weight` 로 갈렸는데 그 값을 싣는 곳이 없어 heavy/mid 가 죽은
    // 갈래였다 — 데이터 모델의 우위(관계마다 evidence·confidence 보유)를 화면이
    // 쓰지 않던 낭비의 정확한 지점이다. 임계는 3단 램프에 맞춘 균등 분할이다.
    // 미기입(0.0)은 램프를 타지 않고 기존 크로스/기본 잉크로 떨어진다(하위호환 —
    // 증거를 저작하지 않은 그래프의 화면은 예전과 똑같다).
    const cf = l.confidence || 0;
    if (cf > 0) return cf >= 0.67 ? pal.link.heavy : cf >= 0.34 ? pal.link.mid : pal.link.base;
    return l.cross ? pal.link.cross : pal.link.base;
  };
  g.linkColor(linkColorFn);
  const fadeTargets = new Map<THREE.Material, number>();
  let raf = 0;
  /** 모션을 줄여 달라는 설정에서는 **이징만** 버리고 효과는 남긴다(카피가 그렇게
   *  약속한다). 여태 이 트윈에는 가드가 없어 그 약속이 3D 에서만 지켜지지 않았다 —
   *  평면(app.css 미디어 쿼리)은 이미 지키고 있었다. */
  const jumpFade = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
  const tween = () => {
    let live = false;
    fadeTargets.forEach((target, mat) => {
      const d = target - mat.opacity;
      if (jumpFade) { mat.opacity = target; return; }
      if (Math.abs(d) > 0.01) { mat.opacity += d * 0.25; live = true; }
      else mat.opacity = target;
    });
    wake(200);                                     // 페이드가 도는 동안은 그려야 보인다
    raf = live ? requestAnimationFrame(tween) : 0;
  };
  /** 남길 집합 밖을 물러나게 한다. hover 와 점등이 **같은 함수**를 쓴다 — 두 벌이면
   *  하나가 다른 하나를 덮어쓴 채 아무도 눈치채지 못한다. `null` 이면 전부 복귀.
   *
   *  물러나는 것은 **라벨뿐**이다. 노드 몸통(메시·Domain 껍질·Gateway 윤곽)은 제 색
   *  그대로 두고, 무엇이 남았는지는 엣지(linkColorFn)와 라벨이 말한다. 몸통을 배경색
   *  으로 섞던 동안 같은 노드가 상태에 따라 진한 색과 옅은 틴트를 오갔다.
   *  근거: [[decision:a-node-keeps-its-own-colour-in-every-state]] */
  const applyFade = (keep: Set<string> | null) => {
    // D32-a 정본값. 루프 밖에서 한 번 읽는다(getComputedStyle 은 레이아웃 읽기라
    // 마크마다 부르면 안 된다).
    const recedeText = recede("text");   // 물러나는 것이 라벨(글자)뿐이다
    g.graphData().nodes.forEach((m: any) => {
      const fade = keep ? !keep.has(m.id) : false;
      const obj = m.__threeObj as THREE.Mesh | undefined;
      if (!obj) return;
      obj.traverse((c: THREE.Object3D) => {
        if (!(c instanceof THREE.Sprite)) return;
        const mat = c.material;
        const b = (mat.userData?.base as number | undefined) ?? 1;
        mat.transparent = true; fadeTargets.set(mat, fade ? b * recedeText : b);
      });
    });
    if (!raf) raf = requestAnimationFrame(tween);
  };
  g.onNodeHover((n: any) => {
    // hover 는 **어둡게만** 한다 — 지나가기만 해도 씬이 재배열되면 눈이 쉴 곳이 없다.
    hoverId = n ? n.id : null;
    // 손을 떼면 점등이 있던 자리로 **돌아간다** — 점등 중에 지도 위를 지나갔다고
    // 걸어 둔 것이 사라지면, 밖에서 건 상태를 hover 가 조용히 지우는 셈이 된다.
    const keep = n ? new Set([n.id, ...(neighbors.get(n.id) || [])])
      : spot ? spot.nodes : null;
    applyFade(keep);
    g.linkColor(linkColorFn);   // 접근자 재적용 → 링크색 재평가
    el.style.cursor = n ? "pointer" : "";
  });

  /** 테마가 뒤집혔을 때 **제자리에서** 다시 칠한다(`__update` 와 같은 관례).
   *
   *  팔레트에서 온 값은 전부 마운트 때 굳는다: 잉크·초점·안개·조명 세기·환경맵, 그리고
   *  주제 틴트(명도가 모드별 토큰이다). 다시 마운트하면 풀리지만 그 길(`show`)은 읽던
   *  문서 열과 노드 카드를 닫는다. 그래서 씬·카메라·오브젝트는 그대로 두고 **색만**
   *  갈아 끼운다. 오브젝트를 다시 짓지 않으므로(`nodeThreeObject` 를 안 부른다) 라벨의
   *  물러남도 그대로 남는다.
   *  근거: [[decision:a-theme-switch-repaints-the-open-view-in-place]] */
  (g as any).__retheme = (): void => {
    const mode = themeMode();
    pal = themePalette();
    applyTints();
    for (const n of nodes) {
      const ref = instRefs.get(n.id);
      if (ref) ref.mesh.setColorAt(ref.idx, instColor(n));
    }
    for (const im of instMeshes) {
      if (im.instanceColor) im.instanceColor.needsUpdate = true;
      reglaze(im.material as THREE.MeshPhysicalMaterial, pal, mode);
    }
    // 라벨은 몸통의 자식으로만 달린다.
    const labels = new Set<THREE.Sprite>();
    for (const m of g.graphData().nodes as any[]) {
      const obj = m.__threeObj as THREE.Object3D | undefined;
      if (!obj) continue;
      obj.traverse((c) => { if (c instanceof THREE.Sprite) labels.add(c); });
      if (!canInstance(m)) takeSkins(obj, nodeObject(m, prefs, pal, true));
    }
    for (const sp of labels) repaintLabel(sp, pal);
    fog?.color.setHex(pal.fog);
    if (scene) scene.environment = studioEnv(g.renderer(), pal, mode);
    g.renderer().toneMappingExposure = GLASS[mode].exposure;
    g.lights(lightsFor(mode === "dark"));
    // 선·화살촉·입자는 링크 digest 가 접근자를 다시 읽을 때 새 팔레트로 칠해진다.
    g.linkColor(linkColorFn);
    wake(600);
  };
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
  prefs: ViewPrefs = loadPrefs(),
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
  // 미니 그리드도 지도와 같은 유리다 — 환경맵·톤매핑·조명이 같아야 같은 물건으로 읽힌다.
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = GLASS[themeMode()].exposure;
  const env = studioEnv(renderer, pal, themeMode());

  type Cell = { scene: THREE.Scene; camera: THREE.PerspectiveCamera;
                el: HTMLElement; meshes: Map<string, THREE.Mesh>;
                rad: number; pad: number; dir: THREE.Vector3 };
  const built: Cell[] = cells.map((c) => {
    const scene = new THREE.Scene();
    scene.environment = env;
    scene.add(new THREE.HemisphereLight(SKY, GROUND, themeMode() === "dark" ? 0.8 : 1.0));
    const dl = new THREE.DirectionalLight(WHITE, themeMode() === "dark" ? 1.8 : 2.2);
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
    //: 미니 그리드도 같은 표를 쓴다 — 셀마다 만들되 셀 안에서는 한 번이다.
    {
      const t = tintMap(c.nodes.map((n) => n.domain), themeMode());
      for (const n of c.nodes) (n as { __tint?: string }).__tint = t.get((n.domain || "").trim());
    }
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
      // 미니 경로도 같은 잉크 규율을 탄다 — 여기만 다른 색이면 Compare 와 지도가
      // 어긋난다(그것이 색을 한 벌로 모으는 이번 작업의 출발점이었다).
      const miniTint = (n as { __tint?: string }).__tint;
      (m as any).userData.base = n.focus ? pal.point
        : n.gateway ? pal.inkMute
        : miniTint ? parseInt(miniTint.slice(1), 16) : pal.ink;
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
    // 선택 상자는 포커스 링과 같은 채널이다 — 하드코딩하면 테마를 안 따라간다.
    box.style.cssText = "position:absolute;border:1px dashed hsl(var(--ring));"
      + "background:hsl(var(--ring) / .08);z-index:5;pointer-events:none";
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

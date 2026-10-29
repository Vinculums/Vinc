import { changedRendererPrefs, canUpdateViewStyle } from "../view-controls";
/** View 3D renderer (DEC D21 revised — full 3D, owner decision 2026-07-10).
 *
 *  Monochrome 3D discipline (as-built: [[vinc.reference.view-controls]]):
 *  depth comes from shading and atmosphere, never hue — matte ink Lambert
 *  materials (ink/ink_soft/ink_mute), paper background + fog, one neutral
 *  directional light. The single point color marks "the current focus" only.
 *
 *  Node bodies = the Vinc orb kit: one glass and inner light for every kind,
 *  the shell's shape says the kind — sphere (Concept, Decision with a crystal inside) ·
 *  tetrahedron (Record) · octahedron (Episode) · shard (Bridge) · hollow dotted shell
 *  (Topic). Drawn by one instanced ray-marched layer (`view-orbs3d.ts`); areas are glowing
 *  glass volumes. Gateways stay outlines. Sidecar supplies deterministic x/y/z (D17) —
 *  positions are pinned (fx/fy/fz), no client force simulation. */
import ForceGraph3D, { type ForceGraph3DInstance } from "3d-force-graph";
import { ViewIdleFrames } from "./view-idle-frames";
import * as THREE from "three";
import { loadPrefs, DIRECTED_RELS, edgeKindOf, type ViewPrefs } from "../view-prefs";
import { palette as signal, hexStr, type ThemeMode } from "../theme";
import { tintPlan } from "../view-tint";
import { recede } from "../recede";
import { edgeKey, type Spot } from "../spotlight";
import { aimAt, restRadius, zoomStep } from "../camera-aim";
import { HIT_MIN_PX, wireDragConnect, type DragEnd, type DragHit } from "./drag-connect";
import { nonOverlappingLabels, type LabelBox } from "../view-labels";
import type { ViewRoute } from "../view-route";
import { RoutedLinkObject } from "./view-routes3d";
import { CurvedLinkObject } from "./view-curves3d";
import { topicOf } from "../node-topic";
import { OrbLayer, ORB_STATE, orbKindOf, orbSeed, areaBox, type AreaBox, areaVolume, retintVolume } from "./view-orbs3d";
import { areasApart } from "../area-key";

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
const FAINT_ALPHA = "47";                  // `L3.faint` 선의 평소 농도(약 28%)
const PARTICLES_PER_LINK = 2;
const PARTICLE_SPEED = 0.006;             // 링크 길이 대비 프레임당 이동분
const PARTICLE_WIDTH = 1.1;
const isDirectedLink = (l: { type?: string }) => DIRECTED_RELS.includes(l.type || "");

/** Signal 팔레트 — 토큰과 같은 원천(Vinc-design 허브 gen_theme_ts 생성물).
 *  다크(기본) = 무채색 검정 위 흰 포커스. 라이트 = 그레인+잉크(2026-09-24 흑백).
 *  themePalette()가 유일한 런타임 seam — 하드코딩 hex 금지. */
type Palette = {
  ink: number; inkMute: number; point: number; fog: number; primary: number;
  /** 무채색 단 — `soft` 는 영역 부피의 색이다(영역 색은 아직 정하지 않았다, 몸통이 주제 보석을 입는다). */
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
  ts?: string;                // 기록 시각 — 몸통의 active 상태(최근 7일)가 읽는다
  historical?: boolean;       // 구간이 닫혔거나 후속이 있다 — 몸통의 historical 상태
  conflict?: boolean;         // 같은 열쇠의 다른 값과 구간이 겹친다 — 몸통의 conflict 상태
  x?: number; y?: number; z?: number;
  fx?: number; fy?: number; fz?: number;
};
// D77/M4 — `weight` 는 사문이었다: 렌더러가 농도 램프를 그 값으로 갈랐는데
// `toL3` 가 한 번도 싣지 않아(사이드카 `shape_graph` 도 보내지 않는다) heavy/mid
// 갈래가 **도달 불가능**했다. 삭제하는 대신 실제로 흐르는 신호로 갈아 끼운다 —
// `confidence`(E-G6, 0~1)는 백엔드가 이미 엣지에 싣고 GraphEdge 로 앱까지 온다.
export type L3 = { source: string; target: string; type?: string; cross?: boolean;
  id?: string; confidence?: number; evidence?: string; role?: string;
  route?: ViewRoute; presentation?: boolean;
  /** 평소에 옅게 그린다(짚은 노드의 선이 되면 다른 선과 같이 밝아진다). 데스크톱은 싣지 않는다. */
  faint?: boolean };

//: 정사면체의 고정 자세 — 관문 윤곽선(gatewayWire)만 쓴다. 몸통은 view-orbs3d 의 다면체가
//: 카메라에 고정된 자세로 그린다.
const DOC_ROT: [number, number, number] = [-0.5, 0.6, 0.2];

// ── 노드 몸통 = Vinc orb 한 층 ──────────────────────────────────────────────
// 히어로 유리(MeshPhysical + 스튜디오 환경맵)를 발주자가 확정한 키트로 갈았다. 몸통은 셰이더가 직접 칠하므로 PBR 재질·환경맵·조명이 몸통에 닿지 않는다. 인스턴싱의
// 이유(드로우콜이 노드 수에 비례하지 않게)는 그대로라 한 층에 전부 담는다.
// 근거: [[vinc.research.graph-vinc-orb-2026-10-28]]
// 이전 인스턴싱의 근거·실측: [[decision:view-3d-nodes-are-instanced-and-recede-by-blending-to-the-background]]
//: 관문만 개별 오브젝트로 남긴다(윤곽선, 실체가 없다는 뜻이라 몸통 유리를 입히지 않는다).
//: 초점은 이제 상태(selected)이고 주제는 점 껍질 종이라 둘 다 한 층에 든다.
const canInstance = (n: N3): boolean => !n.gateway;
/** 톤 매핑 노출 — 선·라벨 스프라이트가 받는다(몸통 셰이더는 톤 매핑을 안 탄다). 값은
 *  히어로 유리 시절과 같아 선과 라벨의 밝기가 그대로다. */
const EXPOSURE: Record<ThemeMode, number> = { dark: 1.06, light: 1.02 };
/** 레이캐스트 전용 유령 메시 — 실제로 그리지 않는다(`visible=false`, 렌더러가
 *  순회에서 건너뛴다). three-forcegraph 가 노드마다 요구하는 오브젝트 계약(위치
 *  추적·hover·클릭)만 채운다. 지오메트리는 노드 형태와 무관한 저폴리 구 하나로
 *  충분하다 — 안 보이는 히트 영역이라 실루엣이 몸통과 정확히 같을 필요가 없다. */
function ghostNode(r: number): THREE.Mesh {
  const m = new THREE.Mesh(new THREE.SphereGeometry(r, 8, 6), new THREE.MeshBasicMaterial());
  m.visible = false;
  return m;
}

/** 테마 전환 때 개별 몸통(관문 윤곽선)의 재질을 갈아 끼운다(`__retheme`).
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

/** 영역 이름표 — 영역 부피마다 하나, 그 영역의 보석색으로 크게 굽는다. 크기는 부피에 비례해서
 *  (`height` 월드 단위) 확대 · 축소해도 영역과 같은 비율로 보인다. 노드 라벨과 달리 겹침 골라내기에
 *  들지 않는다: 영역마다 하나뿐이고, 영역이 갈라져 있을 때만 그린다(`areasApart`).
 *  근거: [[decision:each-area-volume-carries-its-name-at-its-area-topic-2026-10-29]] */
function areaLabelSprite(text: string, color: THREE.Color, height: number, pal: Palette): THREE.Sprite {
  const pad = 18, fs = 96;
  const cv = document.createElement("canvas");
  const font = `700 ${fs}px system-ui, sans-serif`;
  const probe = cv.getContext("2d")!;
  probe.font = font;
  cv.width = Math.ceil(probe.measureText(text).width) + pad * 2;
  cv.height = fs + pad * 2;
  const c2 = cv.getContext("2d")!;
  c2.font = font;
  c2.textBaseline = "middle";
  // 배경색 테두리를 둘러 부피 · 선 위에서도 글자가 끊기지 않게 한다.
  c2.lineJoin = "round"; c2.lineWidth = 14;
  c2.strokeStyle = `#${pal.fog.toString(16).padStart(6, "0")}`;
  c2.strokeText(text, pad, cv.height / 2);
  c2.fillStyle = `#${color.getHexString()}`;
  c2.fillText(text, pad, cv.height / 2);
  const tex = new THREE.CanvasTexture(cv);
  tex.minFilter = THREE.LinearFilter;
  tex.colorSpace = THREE.SRGBColorSpace;           // 구운 글자는 sRGB 다. 빼면 한 번 더 밝혀져 보석색이 바랜다
  // 안개 · 톤 보정 · 깊이에서 뺀다: 멀리 있는 영역의 이름도 같은 세기로 읽혀야 한다.
  const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false,
    depthTest: false, fog: false, toneMapped: false }));
  sp.scale.set(height * cv.width / cv.height, height, 1);
  sp.renderOrder = 2;                              // 부피(-1)와 노드 위에 선다
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
  // 사용자 배율(D31 열린 층) — **맨 마지막에** 곱한다. 위의 인코딩(무엇이 크고
  // 작은가)을 건드리지 않고 전체 크기만 옮긴다.
  return r * (prefs.nodeScale ?? 1);
}

/** 관문 윤곽선의 원형 — 관문은 종의 몸통 대신 그 종의 **모서리만** 그린다(면이 없다 =
 *  실체가 없다. 배후를 안 가리는 것이 이 형태를 고른 이유다). 몸통 키트(view-orbs3d)가
 *  바뀐 뒤에도 윤곽은 예전 기하를 쓴다: 윤곽선에서 다면체 셋은 이미 갈리고, 셰이더 조각을
 *  선으로 다시 짓는 비용에 비해 얻는 것이 없다. */
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

/** 한 층(OrbLayer)에 들지 않는 노드의 오브젝트 — 지금은 관문 하나뿐이다(`canInstance`). */
function nodeObject(n: N3, prefs: ViewPrefs, pal: Palette = themePalette()): THREE.Object3D {
  return gatewayWire(n.kind, nodeRadius(n, prefs), pal);
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
  const aim = aimAt(node, g.cameraPosition?.(), false, rest);
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
      const py = r.__y0! * S;
      const pz = r.__z0! * S;
      n.x = px; n.y = py; n.z = pz;
      n.fx = px; n.fy = py; n.fz = pz;
    }
  };
  // pin deterministic sidecar coords (D17).
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
  let particlesOn = prefs.edges === "particles" && !reduceMotion;
  //: 노드만 — 선을 그리지 않는다(기본). 관계는 점등과 카드가 말한다.
  //: 근거: [[decision:bring-and-go-is-retired-and-nodes-only-is-the-default]]
  let nodesOnly = prefs.edges === "none";
  // 상시 라벨 대상 — 연결수 상위 8, 고아 제외.
  const hubIds = new Set(nodes.slice()
    .sort((a, b) => (b.degree || 0) - (a.degree || 0) || (a.id < b.id ? -1 : 1))
    .slice(0, 8).filter((n) => (n.degree || 0) > 0).map((n) => n.id));
  const hubLabels = new Map<string, THREE.Sprite>();
  /** 상시 라벨 밖에서 잠깐 단 라벨(주제를 고르면 그 주제의 결정) — `__labelIds` 가 관리한다. */
  const pickLabels = new Set<string>();
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
  // 영역도 같은 표에서 읽는다(`tintPlan`) — 부피·영역 사이 선·색이 한 판정을 쓴다.
  let areaTints = new Map<string, string>();
  const areaById = new Map<string, string>();
  const applyTints = () => {
    const plan = tintPlan(tintDomains ?? nodes.map((n) => n.domain), themeMode());
    areaTints = plan.areas;
    areaById.clear();
    for (const n of nodes) {
      // 주제 마크는 자기 경로로 칠한다(`topicOf`) — `domain` 열은 부모 경로라 그대로 읽으면 한 단계 위
      // 주제의 색을 입고, 영역 단계 주제(`domain:vinc/design`)는 영역을 잃어 부피 밖에 선다.
      const d = topicOf(n).trim();
      (n as { __tint?: string }).__tint = plan.topics.get(d);
      const area = plan.areaOf.get(d);
      if (area) areaById.set(n.id, area);
    }
  };
  applyTints();
  /** 영역 사이 선 — 두 끝이 다른 영역이다. 전체 보기에서는 숨기고, 짚거나(hover) 점등하거나
   *  초점인 노드에 닿을 때만 보인다. 영역을 잇는다는 사실은 다리 노드가 늘 말하고, 영역 사이
   *  선이 겹치는 것이 털뭉치의 대부분이었다. 영역을 모르는 끝(다리 등)은 가르지 않는다.
   *  근거: [[decision:the-graph-whole-view-is-the-infra-layout-in-vinc-orbs-2026-10-28]] */
  const focusIds = new Set(nodes.filter((n) => n.focus).map((n) => n.id));
  const crossArea = (s: string, t: string) => {
    const a = areaById.get(s), b = areaById.get(t);
    return !!a && !!b && a !== b;
  };
  /** 문서가 개념·결정에 매달리는 끈(About·Attached) — 관계의 67% 라 전체 보기에서는 영역 사이
   *  선과 같은 규칙으로 숨긴다. 짚은 노드의 것은 보인다. */
  const TETHERS = new Set(["ABOUT", "ATTACHES_TO"]);
  const quietAtWhole = (s: string, t: string, type: string | undefined) =>
    (crossArea(s, t) || TETHERS.has(type || "")) && !focusIds.has(s) && !focusIds.has(t);
  /** 선 모양은 범례의 갈래 어휘 하나를 따른다(D74): 방향은 실선과 화살촉, 방향 없는 관계와
   *  다리는 파선. 스키마 타입마다 따로 그리면 범례와 선이 두 어휘가 된다. */
  const dashedLink = (type: string | undefined, cross?: boolean) => {
    const kind = edgeKindOf({ type: type || "", cross_domain: cross });
    return kind === "related" || kind === "bridge";
  };
  /** 몸통의 색 — 초점은 초점색, 주제 틴트가 있으면 그것, 없으면 잉크. 빌드와 테마 전환이
   *  같은 규칙을 읽는다. 초점색은 "지금 초점" 하나만 뜻하므로 주제가 덮지 않는다(정본 §2.5). */
  const instColor = (n: N3) => {
    const tint = (n as { __tint?: string }).__tint;
    return new THREE.Color(n.focus ? pal.point : tint ? parseInt(tint.slice(1), 16) : pal.ink);
  };
  /** 상태 — 초점은 selected, 가장 새 노드로부터 7일 안에 생긴 것은 active, 나머지는 idle.
   *  "오늘"이 아니라 그래프의 가장 새 날을 기준으로 잡는다: 한동안 안 쓴 그래프를 열면
   *  전부 idle 이 되어 무엇이 최근이었는지가 사라진다. historical 은 닫힌 유효구간이나 후속
   *  (`toN3` 가 node-validity 로 판정). conflict 는 같은 열쇠의 다른 값과 구간이 겹치는 주장이다
   *  (같은 곳, 서버의 주장 비교와 같은 규칙). */
  const newest = nodes.reduce((m, n) => n.ts && n.ts > m ? n.ts : m, "");
  const newestAt = Date.parse(newest);          // 형식이 깨진 ts 면 NaN — 그때는 active 를 안 단다
  const activeFrom = Number.isFinite(newestAt) ? new Date(newestAt - 7 * 86400000).toISOString() : "";
  const stateOf = (n: N3): number => n.focus ? ORB_STATE.selected
    : n.conflict ? ORB_STATE.conflict
    : n.historical ? ORB_STATE.historical
    : activeFrom && n.ts && n.ts >= activeFrom ? ORB_STATE.active : ORB_STATE.idle;
  // 몸통 층 — scene 은 ForceGraph3D 생성 뒤에야 얻는다(아래), 그래서 최초 빌드에서는
  // scene?.add 가 아무 일도 안 하고, 생성 직후 명시적으로 한 번 더 붙인다.
  let scene: THREE.Scene | undefined;
  let orbs: OrbLayer | null = null;
  const orbIndex = new Map<string, number>();
  const instState = new Map<string, string>();
  const disposeOrbs = () => {
    if (orbs) { scene?.remove(orbs.body, orbs.halo); orbs.dispose(); }
    orbs = null;
    orbIndex.clear();
  };
  const buildInstancedNodes = () => {
    instState.clear();
    disposeOrbs();
    const members = nodes.filter(canInstance);
    if (!members.length) return;
    const layer = new OrbLayer(members.length, themeMode() === "light");
    members.forEach((n, i) => {
      layer.set(i, n.x || 0, n.y || 0, n.z || 0, nodeRadius(n, prefs), orbKindOf(n.kind),
                stateOf(n), orbSeed(n.id), instColor(n));
      orbIndex.set(n.id, i);
    });
    layer.body.userData.orbLayer = layer;          // QA 가 씬에서 층을 찾는 손잡이
    orbs = layer;
    scene?.add(layer.body, layer.halo);
  };
  buildInstancedNodes();
  // 영역 부피 — 주제 칸이 영역별로 모이므로(view-layout-spatial) 영역마다 x 로 늘인 타원체
  // 하나. 간격 슬라이더가 자리를 옮기면
  // 다시 짓는다(`__update`).
  // 부피는 그 영역의 보석을 입는다(영역 색이 없으면 무채색 단).
  let volumes: { mesh: THREE.Mesh; area: string; label?: THREE.Sprite; box: AreaBox; text: string }[] = [];
  const volumeColor = (area: string) => {
    const hex = areaTints.get(area);
    return new THREE.Color(hex ? parseInt(hex.slice(1), 16) : pal.soft);
  };
  const disposeVolumes = () => {
    for (const v of volumes) {
      scene?.remove(v.mesh); (v.mesh.material as THREE.Material).dispose();
      if (v.label) { scene?.remove(v.label); v.label.material.map?.dispose(); v.label.material.dispose(); }
    }
    volumes = [];
  };
  const buildVolumes = () => {
    disposeVolumes();
    const placed = nodes.filter((n) => n.kind !== "bridge" && !n.gateway && n.domain
      && Number.isFinite(n.x));
    const groups = new Map<string, { x: number; y: number; z: number; r: number }[]>();
    for (const n of placed) {
      const area = areaById.get(n.id);
      if (!area) continue;
      let g = groups.get(area);
      if (!g) groups.set(area, g = []);
      g.push({ x: n.x || 0, y: n.y || 0, z: n.z || 0, r: nodeRadius(n, prefs) });
    }
    const light = themeMode() === "light";
    const boxes: [string, AreaBox][] = [];
    for (const [area, pts] of groups) {
      const box = areaBox(pts);
      if (box) boxes.push([area, box]);
    }
    // 영역이 실제로 갈라져 있을 때만 그린다(areasApart) — 갈라지지 않은 배치에서는 부피마다
    // 지도 전체를 감싸 한가운데서 겹친다.
    if (!areasApart(boxes.map(([, box]) => box))) return;
    // 이름표는 그 영역의 대표 주제 노드(경로가 가장 짧은 주제 마크, 예: `domain:vinc/design`) 바로 위에 선다.
    // 대표 주제가 화면에 없으면 부피의 윗부분에 선다.
    const anchors = new Map<string, N3>();
    for (const n of nodes) {
      if (n.kind !== "domain" || !Number.isFinite(n.x)) continue;
      const area = areaById.get(n.id);
      const best = area ? anchors.get(area) : undefined;
      if (area && (!best || topicOf(n).split("/").length < topicOf(best).split("/").length)) anchors.set(area, n);
    }
    for (const [area, box] of boxes) {
      const at = anchors.get(area);
      const text = at?.label || area.split("/").at(-1) || area;
      const v: (typeof volumes)[number] = { mesh: areaVolume(box, volumeColor(area), light), area, box, text };
      if (prefs.labels !== "none") {
        v.label = areaLabelSprite(text, volumeColor(area), box.rc * .2, pal);
        if (at) v.label.position.set(at.x || 0, (at.y || 0) + box.rc * .2, at.z || 0);
        else v.label.position.set(box.cx, box.cy + box.rc * .7, box.cz);
      }
      volumes.push(v);
    }
    if (volumes.length) scene?.add(...volumes.flatMap((v) => v.label ? [v.mesh, v.label] : [v.mesh]));
  };
  const renderedRadii = new Map<string, number>();
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
    pickLabels.clear();                          // 오브젝트를 다시 지으면 붙여 둔 라벨도 함께 사라진다
    return (n: any) => {
      if (canInstance(n)) {
        const ghost = ghostNode(nodeRadius(n, prefs));
        ghost.userData.baseRadius = nodeRadius(n, prefs);
        // 유령은 `visible=false` 라 렌더러가 순회에서 자식째 건너뛴다 — 허브 라벨을
        // 유령의 자식으로 달면 라벨도 함께 안 그려진다. 라벨이 있는 노드만 보이는
        // 빈 그룹으로 감싸 유령과 라벨을 형제로 둔다(위치 추적은 그룹이 받는다).
        if (prefs.labels !== "none" && hubIds.has(n.id)) {
          const group = new THREE.Group();
          group.add(ghost, hubLabel(n));
          group.userData.mark = ghost;
          return group;
        }
        const group = new THREE.Group();
        group.add(ghost); group.userData.mark = ghost;
        return group;
      }
      const mesh = nodeObject(n, prefs, pal);
      mesh.userData.baseRadius = nodeRadius(n, prefs);
      const group = new THREE.Group();
      group.add(mesh); group.userData.mark = mesh;
      // V7 — 허브(연결수 상위)는 **상시 라벨**을 단다. hover 만 기다리면 렌즈가 "이름
      // 없는 별자리"가 된다. 나머지는 그대로 hover 로 뜬다.
      if (prefs.labels !== "none" && hubIds.has(n.id)) group.add(hubLabel(n));
      return group;
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
  const arrowLenFn = () => (l: any) => {
    const s = typeof l.source === "object" ? l.source.id : l.source;
    const t = typeof l.target === "object" ? l.target.id : l.target;
    //: 영역 사이 선·끈은 평소 알파 0 이라 화살촉만 떠 보인다 — 화살촉은 초점에 닿을 때만 단다.
    if (quietAtWhole(s, t, l.type)) return 0;
    return prefs.arrows && !particlesOn && !nodesOnly && isDirectedLink(l) ? 2.6 : 0;
  };
  assignTiers(nodes, links);   // 성좌 글로우 깊이 계층(92:211) — nodeObject 가 __tier 를 읽는다
  // 세터 체인은 설정 → graphData 마지막: digest 1회에 메시 생성과 카운트다운 리셋이
  // 같은 update 에서 처리되게 해 재생성 경합을 없앤다. 크기는 컨테이너 실측으로 명시
  // (기본값은 마운트 순간 스냅샷이라 스크롤바 등장 뒤 컨테이너보다 커져 넘친다 — 실측).
  const g = new ForceGraph3D(el, { rendererConfig: { alpha: true, antialias: true } } as any)
    .backgroundColor("rgba(0,0,0,0)")
    .showNavInfo(false)
    .linkVisibility(!nodesOnly)             // Skip hidden line objects while retaining every relationship.
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
    .linkThreeObject((l: any) => l.route ? new RoutedLinkObject(l.route) : new CurvedLinkObject())
    .linkPositionUpdate((object: THREE.Object3D, ends: any, l: any) => {
      if (object instanceof CurvedLinkObject)
        object.update(ends.start, ends.end, l.__curve, linkWidthFn()(), linkColorFn(l));
      return true;
    })
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

  // 대기(fog = paper) + 무채 조명 보정 — fog 는 깊이 단서다
  scene = g.scene();
  // Cull billboards before the renderer builds its visible-object list. This
  // follows camera movement, resize and dragging without a second animation loop.
  const labelWorld = new THREE.Vector3(), labelScale = new THREE.Vector3();
  const labelCamera = new THREE.Vector3(), labelNdc = new THREE.Vector3();
  // 몸통 안의 빛이 떠도는 시계. 유휴 정지(F6) 뒤 깨어날 때 한 번에 몇 초를 건너뛰지 않게
  // 프레임 간격을 자른다. 모션을 줄여 달라는 설정에서는 멈춘다.
  let orbTime = 0, orbLast = performance.now();
  scene.onBeforeRender = (_renderer, _scene, camera) => {
    const nodeVisibility: any = g.nodeVisibility(), linkVisibility: any = g.linkVisibility();
    const isVisible = (rule: any, value: any) => typeof rule === "function" ? !!rule(value)
      : typeof rule === "string" ? !!value[rule] : rule !== false;
    const matrix = camera.matrixWorldInverse.elements;
    const pixelsAtUnitDepth = camera.projectionMatrix.elements[5] * el.clientHeight / 2;
    const originalPixels = new Map<string, number>();
    for (const n of nodes) {
      const base = nodeRadius(n, prefs);
      const depth = -(matrix[2] * (n.x || 0) + matrix[6] * (n.y || 0) + matrix[10] * (n.z || 0) + matrix[14]);
      const pixelsPerUnit = depth > 0 ? pixelsAtUnitDepth / depth : 0;
      // Enlarge the existing body, never a halo or replacement glyph. A shared
      // reference radius preserves degree/pin/type size ratios at the same depth.
      // The user's size slider still scales both the close and distant marks.
      const boost = pixelsPerUnit > 0
        ? Math.max(1, 2.5 / (4.4 * pixelsPerUnit)) : 1;
      const radius = base * boost;
      renderedRadii.set(n.id, radius);
      originalPixels.set(n.id, base * pixelsPerUnit);
      const mark = (n as any).__threeObj?.userData.mark as THREE.Object3D | undefined;
      if (mark) mark.scale.setScalar(radius / mark.userData.baseRadius);
      const idx = orbIndex.get(n.id);
      if (idx === undefined || !orbs) continue;
      const r = isVisible(nodeVisibility, n) ? radius : 0;
      const key = `${n.x},${n.y},${n.z},${r}`;
      if (instState.get(n.id) === key) continue;
      instState.set(n.id, key);
      orbs.move(idx, n.x || 0, n.y || 0, n.z || 0, r);
    }
    const now = performance.now();
    if (!reduceMotion) orbTime += Math.min(now - orbLast, 50) / 1000;
    orbLast = now;
    orbs?.flush(orbTime, pixelsAtUnitDepth);
    for (const link of links as any[]) {
      const object = link.__lineObj;
      const source = typeof link.source === "object" ? link.source : nodes.find(n => n.id === link.source);
      const target = typeof link.target === "object" ? link.target : nodes.find(n => n.id === link.target);
      if (!source || !target) continue;
      if (object instanceof CurvedLinkObject) {
        // 고정 좌표의 simulation이 멈춘 뒤에도 색·폭을 갱신한다.
        // renderer를 깨우는 것만으로는 linkPositionUpdate가 실행되지 않는다.
        object.update(source, target, link.__curve, linkWidthFn()(), linkColorFn(link));
        continue;
      }
      if (!(object instanceof RoutedLinkObject)) continue;
      const emphasized = hoverId ? source.id === hoverId || target.id === hoverId
        : !!spot?.edges.has(edgeKey({ source: source.id, target: target.id, type: link.type || "" }));
      // Far overview lines must not form an opaque fence over subpixel bodies.
      // Restore the original ink on approach and for explicitly traced relations.
      const pixels = Math.min(originalPixels.get(source.id) || 0, originalPixels.get(target.id) || 0);
      const detail = emphasized ? 1 : .08 + .92 * Math.min(1, pixels / 2.5) ** 2;
      object.update(source, target, S, { color: linkColorFn(link), width: linkWidthFn()(),
        arrow: !!prefs.arrows && !particlesOn && !nodesOnly && isDirectedLink(link),
        particles: particlesOn && isDirectedLink(link), particleColor: pal.link.heavy,
        visible: isVisible(linkVisibility, link) && isVisible(nodeVisibility, source) && isVisible(nodeVisibility, target)
          && (!quietAtWhole(source.id, target.id, link.type) || emphasized),
        dashed: dashedLink(link.type, link.cross),
        targetRadius: renderedRadii.get(target.id) ?? nodeRadius(target, prefs), detail }, performance.now());
    }
    const width = el.clientWidth, height = el.clientHeight;
    const boxes: LabelBox[] = [];
    for (const id of hubLabels.keys()) {
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
  // buildInstancedNodes() 최초 호출분 — scene 이 이제 있다(TS 는 클로저의 대입을 못 보고 null 로 좁힌다)
  const firstOrbs = orbs as OrbLayer | null;
  if (firstOrbs) scene.add(firstOrbs.body, firstOrbs.halo);
  buildVolumes();
  // 환경맵은 몸통이 PBR 유리이던 동안의 것이라 몸통 셰이더와 함께 뺐다.
  // 톤 매핑은 남는다: 선과 라벨이 그 밝기로 맞춰져 있다.
  g.renderer().toneMapping = THREE.ACESFilmicToneMapping;
  g.renderer().toneMappingExposure = EXPOSURE[themeMode()];
  // 원경이 배경으로 "서서히" 잦아듦. near/far 는 아래에서 **카메라 거리에 연동**한다
  // (고정값이면 축소가 곧 소멸이다 — syncCamera 주석 참조).
  const fog = new THREE.Fog(pal.fog, S * 2.6, S * 9);
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
  // 세 줄 다 무효였다). 트랙볼의 이름은 `noRotate`.
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
    fog.near = dist * FOG_NEAR_K; fog.far = dist * FOG_FAR_K;
  };
  syncCamera();
  // 매 프레임이 아니라 **카메라가 실제로 움직일 때만** 갱신한다(비용 무시 가능).
  controls?.addEventListener?.("change", syncCamera);
  if (keepCam) {
    // 옵션 재마운트 — 보던 자리 그대로. 전체 맞춤은 첫 마운트만 한다(아래 settle).
    g.cameraPosition(keepCam.pos, keepCam.tgt, 0);
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
      setTimeout(() => {
        if (dead) return;
        syncCamera();
        console.info(`View scene complete ${JSON.stringify({ renderer: "3d", nodes: nodes.length, edges: links.length,
          elapsedMs: Math.round(performance.now() - mountedAt) })}`);
      }, 460);
    });
  };
  requestAnimationFrame(() => settle(0));
  // 컨테이너 추적 리사이즈 — 창 크기 변경·스크롤바 등장에도 캔버스가 컨테이너와 일치.
  const ro = new ResizeObserver(() => {
    if (dead) return;
    sizeTo();                                    // 컨테이너가 변했으면 캔버스를 맞추고
    (g.controls() as any)?.handleResize?.();     // 캔버스가 변했으면 컨트롤을 맞춘다
    wake(300);
  });
  ro.observe(el);
  ro.observe(g.renderer().domElement);           // 캔버스 변화는 digest 뒤에 온다(위 주석)

  // ── 필요할 때만 그린다(F6, 2026-08-17) ───────────────────────────────────
  // 좌표는 핀 고정이라 아무 일도 없을 때 화면은 한 픽셀도 안 바뀌는데, three-render-
  // objects 의 틱은 매 프레임 render() 를 부른다(실측: 유휴 3D 가 59fps 로 GPU 를 태운다).
  // "움직일 이유"가 있는 동안만 깨어 있는다: 포인터·휠·카메라 변화·hover 페이드·비행·
  // 옵션 반영은 각자 wake(ms) 를 부르고, 마감이 지나면 pauseAnimation(). 입자 모드는
  // 상시 애니메이션이라 재우지 않는다. resumeAnimation 은 첫 프레임을 곧장 그린다.
  const idleCamera = g.camera() as THREE.PerspectiveCamera;
  const paintedPosition = new THREE.Vector3(), paintedRotation = new THREE.Quaternion();
  const paintedTarget = new THREE.Vector3(), paintedProjection = new THREE.Matrix4();
  let painted = false;
  const idle = new ViewIdleFrames({
    now: () => performance.now(), defer: (fn) => queueMicrotask(fn),
    // Kapsule digest는 1ms debounce다. 그 task가 끝난 다음 rAF에서 재개한다.
    deferSettle: (fn) => window.setTimeout(fn, 2),
    deferResume: (fn) => window.setTimeout(() => requestAnimationFrame(fn), 2),
    pause: () => (g as any).pauseAnimation?.(), resume: () => (g as any).resumeAnimation?.(),
    continuous: () => particlesOn,
    hasUnpaintedChanges: () => !painted || !paintedPosition.equals(idleCamera.position)
      || !paintedRotation.equals(idleCamera.quaternion)
      || !paintedProjection.equals(idleCamera.projectionMatrix)
      || !!controls?.target && !paintedTarget.equals(controls.target),
  });
  const wake = (ms = 300) => idle.wake(ms);
  const napTimer = window.setInterval(() => idle.check(), 200);
  // 카메라 tween은 render 뒤에 진행된다. 실제 그린 상태와 달라졌으면 다음 프레임을 남긴다.
  scene.onAfterRender = () => {
    paintedPosition.copy(idleCamera.position); paintedRotation.copy(idleCamera.quaternion);
    paintedProjection.copy(idleCamera.projectionMatrix);
    if (controls?.target) paintedTarget.copy(controls.target);
    painted = true;
    idle.afterRender();
  };
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
        const r = renderedRadii.get(n.id) ?? nodeRadius(n, prefs);
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
    idle.dispose();
    unwireConnect?.();
    controls?.removeEventListener?.("change", syncCamera);
    window.clearInterval(napTimer);
    controls?.removeEventListener?.("change", onWakeEvent);
    for (const ev of ["pointermove", "pointerdown", "wheel", "keydown"] as const)
      el.removeEventListener(ev, onWakeEvent);
    disposeOrbs();
    disposeVolumes();
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
    const changed = changedRendererPrefs(prev, np);
    if (!canUpdateViewStyle(changed, true)) return false;
    prefs = np;
    if (!changed.length) return true;
    if (changed.includes("spread")) {
      S = SCALE * np.spread;
      place();                                   // 핀 좌표를 새 배율로 다시 놓고
      buildVolumes();                            // 영역 부피도 새 자리를 감싸게
      (g as any).d3ReheatSimulation?.();        // 위치 동기화 프레임을 1회 강제(settle 과 같은 길)
    }
    if (changed.some((k) => k === "labels" || k === "size")) {
      applyTints();
      buildInstancedNodes();                     // 인스턴스 배치도 새 인코딩으로 다시 굽는다
      buildVolumes();
      g.nodeThreeObject(nodeFactory());          // 새 함수 = 노드 오브젝트 전부 다시 굽기
    } else if (changed.includes("nodeScale")) {
      instState.clear();
      // The next render updates bodies and hit ghosts together; labels keep
      // their own size and the camera-dependent correction never accumulates.
    }
    // 폭은 custom 버퍼의 위치·paint 갱신에서 반영한다.
    // linkWidth setter는 경로 링크까지 모든 객체를 재생성하므로 호출하지 않는다.
    if (changed.includes("edges")) {
      particlesOn = prefs.edges === "particles" && !reduceMotion;
      nodesOnly = prefs.edges === "none";
      g.linkDirectionalParticles((l: any) => !l.route && particlesOn && isDirectedLink(l) ? PARTICLES_PER_LINK : 0);
      // 처음 숨겨졌던 링크의 곡선을 계산하도록 고정 좌표 tick을 한 번 실행한다.
      (g as any).d3ReheatSimulation?.();
    }
    if (changed.includes("arrows") || changed.includes("edges"))
      g.linkDirectionalArrowLength((l: any) => l.route ? 0 : arrowLenFn()(l));
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
  /** 이 노드들이 화면에 들도록 카메라를 맞춘다(주제 고르기). 집합은 그대로고 카메라만 움직인다 —
   *  파고들기가 아니다: [[decision:the-graph-whole-view-is-the-infra-layout-in-vinc-orbs-2026-10-28]] */
  (g as any).__frame = (ids: string[] | null, ms = 700): void => {
    // `zoomToFit` 은 거리만 줄이고 시선 대상을 옮기지 않아(실측) 직접 잰다: 고른 노드들의 중심과
    // 반경, 지금 보는 방향 그대로 그 구가 화면에 들어오는 거리.
    const want = ids ? new Set(ids) : null;
    const picked = nodes.filter((n) => (!want || want.has(n.id)) && Number.isFinite(n.x));
    if (!picked.length) return;
    const c = new THREE.Vector3();
    for (const n of picked) c.add(new THREE.Vector3(n.x || 0, n.y || 0, n.z || 0));
    c.divideScalar(picked.length);
    let r = 0;
    for (const n of picked) r = Math.max(r, c.distanceTo(new THREE.Vector3(n.x || 0, n.y || 0, n.z || 0))
      + nodeRadius(n, prefs));
    const cam = g.camera() as THREE.PerspectiveCamera;
    const tgt = (controls?.target as THREE.Vector3 | undefined) ?? new THREE.Vector3();
    const dir = cam.position.clone().sub(tgt);
    if (dir.lengthSq() < 1e-9) dir.set(0, 0, 1);
    const half = THREE.MathUtils.degToRad(cam.fov / 2) * Math.min(1, cam.aspect);
    const dist = Math.max(r, 40) / Math.sin(half) * 1.15;
    const pos = c.clone().add(dir.normalize().multiplyScalar(dist));
    wake(ms + 500);
    g.cameraPosition({ x: pos.x, y: pos.y, z: pos.z }, { x: c.x, y: c.y, z: c.z }, ms);
    setTimeout(() => { if (!dead) syncCamera(); }, ms + 60);
  };
  /** 상시 라벨(허브) 밖의 노드에 라벨을 잠깐 단다. 새 목록이 오면 앞의 것은 거둔다. 겹침
   *  솎기(nonOverlappingLabels)는 상시 라벨과 같이 받는다. */
  (g as any).__labelIds = (ids: string[]): void => {
    for (const id of pickLabels) {
      const sp = hubLabels.get(id);
      sp?.parent?.remove(sp);
      sp?.material.map?.dispose(); sp?.material.dispose();
      hubLabels.delete(id);
    }
    pickLabels.clear();
    if (prefs.labels === "none") return;
    for (const id of ids) {
      if (hubLabels.has(id)) continue;
      const n = nodes.find((m) => m.id === id) as (N3 & { __threeObj?: THREE.Object3D }) | undefined;
      const group = n?.__threeObj;
      if (!n || !group) continue;
      group.add(hubLabel(n));
      pickLabels.add(id);
    }
    wake(400);
  };
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
   *  가리키는 것이 언제나 지금 관심사라 우선순위 코드가 없다. */
  let spot: Spot | null = null;
  const linkColorFn = (l: any) => {
    const s = typeof l.source === "object" ? l.source.id : l.source;
    const t = typeof l.target === "object" ? l.target.id : l.target;
    // 노드만 — 선이 없다. hover 로도 돌아오지 않는다(그것이 이 모드의 뜻이다).
    if (nodesOnly) return pal.link.base + ALPHA0;
    // 영역 사이 선과 끈은 짚었을 때만(위 `quietAtWhole` 주석). 점등이 걸려 있으면 그 집합을 따른다.
    if (quietAtWhole(s, t, l.type)
        && !(hoverId ? s === hoverId || t === hoverId
             : spot?.edges.has(edgeKey({ source: s, target: t, type: l.type || "" }))))
      return pal.link.base + ALPHA0;
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
    // 짚은 노드에 닿은 선은 포인트색.
    if (hoverId && near) return hexStr(pal.point);
    // D33 — 농도 = 관계의 세기(굵기 개정). 다크는 반전(D39).
    // B1(2026-08-03): 그 "세기"를 **기록된 증거의 신뢰도**(E-G6 confidence)가 말한다.
    // 여태 이 램프는 `weight` 로 갈렸는데 그 값을 싣는 곳이 없어 heavy/mid 가 죽은
    // 갈래였다 — 데이터 모델의 우위(관계마다 evidence·confidence 보유)를 화면이
    // 쓰지 않던 낭비의 정확한 지점이다. 임계는 3단 램프에 맞춘 균등 분할이다.
    // 미기입(0.0)은 램프를 타지 않고 기존 크로스/기본 잉크로 떨어진다(하위호환 —
    // 증거를 저작하지 않은 그래프의 화면은 예전과 똑같다).
    if (l.faint) return (l.cross ? pal.link.cross : pal.link.base) + FAINT_ALPHA;
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
      const idx = orbIndex.get(n.id);
      if (idx !== undefined) orbs?.tint(idx, instColor(n));
    }
    orbs?.setLight(mode === "light");
    for (const v of volumes) {
      retintVolume(v.mesh, volumeColor(v.area), mode === "light");
      if (v.label) {                               // 이름표도 새 보석으로 다시 굽는다(자리 · 크기는 그대로)
        const fresh = areaLabelSprite(v.text, volumeColor(v.area), v.box.rc * .2, pal);
        v.label.material.map?.dispose();
        v.label.material.map = fresh.material.map;
        v.label.material.needsUpdate = true;
        fresh.material.dispose();
      }
    }
    // 라벨은 몸통의 자식으로만 달린다.
    const labels = new Set<THREE.Sprite>();
    for (const m of g.graphData().nodes as any[]) {
      const obj = m.__threeObj as THREE.Object3D | undefined;
      if (!obj) continue;
      obj.traverse((c) => { if (c instanceof THREE.Sprite) labels.add(c); });
      if (!canInstance(m)) takeSkins(obj, nodeObject(m, prefs, pal));
    }
    for (const sp of labels) repaintLabel(sp, pal);
    fog.color.setHex(pal.fog);
    g.renderer().toneMappingExposure = EXPOSURE[mode];
    g.lights(lightsFor(mode === "dark"));
    // 선·화살촉·입자는 링크 digest 가 접근자를 다시 읽을 때 새 팔레트로 칠해진다.
    g.linkColor(linkColorFn);
    wake(600);
  };
  return g;
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

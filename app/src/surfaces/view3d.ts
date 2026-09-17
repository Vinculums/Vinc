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
import { palette as signal, viewScene, type ThemeMode } from "../theme";
import { tintMap } from "../view-tint";
import { recede } from "../recede";
import { edgeKey, type Spot } from "../spotlight";
import { aimAt, restRadius, zoomStep } from "../camera-aim";

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
 *  다크(기본) = Deep Signal: 딥 차콜 위 페리윙클 포커스. 라이트 = 그레인+잉크.
 *  themePalette()가 유일한 런타임 seam — 하드코딩 hex 금지. */
type Palette = {
  ink: number; inkMute: number; point: number; fog: number; primary: number;
  link: { heavy: string; mid: string; base: string; cross: string; fade: string };
  label: string;
};
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
                   confidence?: number };

// D77(2026-08-03) — **그래프 노드에서 유리 은퇴.** D40 이 노드에 입힌 프로스티드
// 유리(MeshPhysicalMaterial: clearcoat + 환경맵 반사)를 이 파일 머리말이 원래
// 규정한 matte ink Lambert 로 되돌린다. 근거는 실측이다: 기본 테마인 다크에서
// `nodeObject` 의 성좌 글로우 갈래가 MeshBasicMaterial 로 **조기 반환**하므로
// 유리는 애초에 렌더되지 않는다(도메인 노드 제외). 즉 재질 체계를 둘 유지하는
// 비용(PBR 셰이더 + PMREM 환경맵 생성)을 내면서 기본 화면에서 얻는 이득이 0이었다.
// 물성이 판독 정확도를 높인다는 근거도 문헌에 없다(chartjunk 후속 실험).
// 불투명도·색·형태 채널은 그대로다 — 바뀐 것은 반사와 클리어코트뿐이다.
// ⚠ D37 Liquid Glass 는 **크롬 층에 그대로 남는다**. 거기서 물성은 데이터 채널이
//   아니라 UI 어포던스이므로 이 기회비용 논리가 적용되지 않는다.
// Lambert 를 고른 이유(평면 MeshBasic 이 아니라): D33 이 조명으로 만드는 "판재"
// 명암 그라데이션이 라이트 테마의 깊이 단서다. 유리를 걷어내되 그 단서는 지킨다.
const NODE_OP = 0.72;   // 일반 노드 프로스트
const GATE_OP = 0.35;   // 게이트웨이 — 더 투명("여기 소속 아님")
//: 정사면체의 고정 자세. 앱 노드는 자전하지 않으므로 이 각도가 곧 실루엣이다 —
//: face·view dot 0.76 / 0.21 로 두 면의 음영이 갈려 입체로 읽힌다(시안 §2).
const DOC_ROT: [number, number, number] = [-0.5, 0.6, 0.2];

// ── 대량 노드 인스턴싱 ──────────────────────────────────────────────────────
// 노드마다 개별 지오메트리 + MeshPhysicalMaterial 로 그리면 드로우콜과 셰이딩 비용이
// 노드 수에 비례한다. 3D 의 기본 선 모드는 입자라(edgeShowFor) F6 유휴 정지를 못 받고
// 매 프레임 이 비용을 낸다. 그래서 몸통이 종류(구·팔면체·사면체)만 다른 노드는 종류당
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
  sphere: new THREE.SphereGeometry(1, 20, 14),
  bridge: new THREE.OctahedronGeometry(1.06),
  doc: new THREE.TetrahedronGeometry(1.22),
};
const INST_ROT: Record<InstKind, THREE.Quaternion> = {
  sphere: new THREE.Quaternion(),
  bridge: new THREE.Quaternion().setFromEuler(new THREE.Euler(0, 0.5, 0)),
  doc: new THREE.Quaternion().setFromEuler(new THREE.Euler(...DOC_ROT)),
};
/** 인스턴스 재질. 색은 흰색 기준(인스턴스 색이 곱해진다) — 나머지는 skinFor("body")와
 *  같은 값이다. 단 **불투명**으로 둔다: 투명으로 바꾸면 수백 인스턴스가 투명 패스로 가서
 *  조기 깊이 기각을 잃고, 한 InstancedMesh 안은 깊이 정렬도 안 된다.
 *
 *  hover 페이드(D32-a)는 알파 대신 **최종 색을 배경색으로 섞는다**(`instanceFade`).
 *  fog 가 하는 연산과 같고, 뒤가 배경뿐인 자리에서는 알파 합성과 수학적으로 같다.
 *  인스턴스 색(알베도)만 섞으면 안 된다 — clearcoat·sheen·환경 반사가 그대로 남는다. */
function instMaterial(pal: Palette): THREE.MeshPhysicalMaterial {
  const m = new THREE.MeshPhysicalMaterial({
    color: 0xffffff, roughness: .52, metalness: .08,
    clearcoat: .25, clearcoatRoughness: .3,
    sheen: .2, sheenColor: new THREE.Color(pal.point), sheenRoughness: .6,
    envMapIntensity: .85,
  });
  // fog 유니폼과 같은 색공간(renderer.outputColorSpace — 이 앱은 기본 sRGB 그대로)으로
  // 넣는다. 섞는 자리가 colorspace_fragment 뒤라 선형값을 넣으면 색이 어긋난다.
  const fadeColor = new THREE.Color(pal.fog);
  fadeColor.getRGB(fadeColor, THREE.SRGBColorSpace);
  m.onBeforeCompile = (shader) => {
    shader.uniforms.uFadeColor = { value: fadeColor };
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", "#include <common>\nattribute float instanceFade;\nvarying float vInstanceFade;")
      .replace("#include <begin_vertex>", "#include <begin_vertex>\n\tvInstanceFade = instanceFade;");
    shader.fragmentShader = shader.fragmentShader
      .replace("#include <common>", "#include <common>\nuniform vec3 uFadeColor;\nvarying float vInstanceFade;")
      .replace("#include <premultiplied_alpha_fragment>",
        "gl_FragColor.rgb = mix( gl_FragColor.rgb, uFadeColor, vInstanceFade );\n\t#include <premultiplied_alpha_fragment>");
  };
  m.customProgramCacheKey = () => "vinc-inst-fade";
  return m;
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
/** 인스턴스 하나의 장부 — hover 페이드(아래 applyFade)와 nodeScale 슬라이더
 *  (아래 __update)·Bring&Go 비행이 이 레퍼런스로 인스턴스 행렬·페이드를 다시 쓴다. */
type InstRef = {
  kind: InstKind; idx: number; mesh: THREE.InstancedMesh; baseR: number;
  /** `applyFade`의 tween 이 그대로 다루는 대상. `.opacity` 자리에 **배경 혼합비**
   *  (0 = 그대로, 1 = 배경색)를 담는다 — 쓰면 `instanceFade` 속성에 바로 반영된다. */
  proxy: { opacity: number };
};
function fadeProxy(attr: THREE.InstancedBufferAttribute, idx: number): { opacity: number } {
  return {
    get opacity() { return attr.getX(idx); },
    set opacity(v: number) { attr.setX(idx, v); attr.needsUpdate = true; },
  };
}

/** 발광 패널 4장 + PMREM — 이미지 없이 굽는 스튜디오 환경(시안 §3, 외부 요청 0).
 *  반무광 재질의 clearcoat·sheen 이 반사할 것이 있어야 조형이 판재로 읽힌다. */
let _envTex: THREE.Texture | null = null;
function studioEnv(renderer: THREE.WebGLRenderer, pal: Palette): THREE.Texture {
  if (_envTex) return _envTex;
  const sc = new THREE.Scene();
  const panel = (w: number, h: number, d: number, color: number, k: number,
                 pos: [number, number, number], rot?: [number, number, number]) => {
    const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d),
      new THREE.MeshBasicMaterial({ color: new THREE.Color(color).multiplyScalar(k) }));
    m.position.set(...pos); if (rot) m.rotation.set(...rot); sc.add(m);
  };
  panel(12, 12, .1, pal.point, 3.2, [0, 6.5, -3], [Math.PI / 2.6, 0, 0]);
  panel(9, 9, .1, 0xFFFFFF, 1.5, [-6, 2, 4], [0, Math.PI / 2.4, 0]);
  panel(9, 9, .1, pal.ink, 1.1, [6.5, 1, 3], [0, -Math.PI / 2.4, 0]);
  panel(16, .1, 16, pal.fog, 2.6, [0, -2.6, 0]);
  panel(10, 10, .1, 0xFFFFFF, .55, [0, -1.2, 5], [Math.PI / 2.6, 0, 0]);
  const pm = new THREE.PMREMGenerator(renderer);
  pm.compileEquirectangularShader();
  _envTex = pm.fromScene(sc, .04).texture;
  pm.dispose();
  return _envTex;
}

/** 앱 재질 = 시안 §3 의 **반무광 한 벌**(유광은 히어로 전용).
 *
 *  D77 이 은퇴시킨 MeshPhysical 을 되살린다 — 발주자 결정 2026-08-15. D77 의 근거는
 *  "다크에서 글로우 분기가 MeshBasic 으로 조기 반환해 물성이 애초에 렌더되지
 *  않는다"였는데, 같은 결정으로 **그 글로우 분기 자체가 사라졌다.** 근거가 없어졌으니
 *  판정도 뒤집힌다. 환경맵도 같은 이유로 함께 돌아온다. */
function skinFor(pal: Palette, kind: "body" | "core" | "shell", focus: boolean,
                 tint?: string | null): THREE.Material {
  // 기본 잉크는 **`ink`** 다. 여기가 `primary`(인디고) 이던 동안 3D 본체만 다른
  // 잉크를 썼고, 다크에서는 알베도 대비가 APCA Lc 9.6 이었다 — 이 리포가 스스로
  // 적어 둔 불가시 하한(Lc 16)보다 낮다. 조명이 얹혀 겨우 보이던 것이지 색이
  // 보이던 것이 아니다. 2D · 타임라인 · 번들 셋이 이미 쓰던 값으로 맞춘다.
  //
  // 코어(Domain 속)도 잉크다. 초점색이던 자리인데, 초점색은 "지금 초점" 하나만
  // 뜻하고 타입에는 안 쓴다(정본 §2.5). 초점은 보색이 아니라 초점 토큰이다.
  // 주제 틴트(열린 층)는 **초점이 아닐 때만** 얹는다 — 초점색은 "지금 초점" 하나만
  // 뜻하므로 주제가 덮으면 안 된다.
  const base = focus ? pal.point : tint ? parseInt(tint.slice(1), 16) : pal.ink;
  const m = new THREE.MeshPhysicalMaterial({
    color: base, roughness: kind === "shell" ? .20 : .52, metalness: .08,
    clearcoat: .25, clearcoatRoughness: .3,
    sheen: .2, sheenColor: new THREE.Color(pal.point), sheenRoughness: .6,
    envMapIntensity: .85, transparent: true, opacity: 1,
  });
  if (kind === "core") { m.emissive = new THREE.Color(base); m.emissiveIntensity = .3; }
  if (kind === "shell") { m.opacity = .28; m.depthWrite = false; }
  m.userData.base = m.opacity;
  return m;
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
  return sp;
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
      return new THREE.SphereGeometry(r, 20, 14);
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
  const mat = new THREE.LineBasicMaterial({ color: pal.inkMute, transparent: true, opacity: 0.5 });
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
  g.add(new THREE.Mesh(new THREE.SphereGeometry(r * 0.52, 24, 16), skin(1)));
  return g;
}

function nodeObject(n: N3, prefs: ViewPrefs, pal: Palette = themePalette(),
                    _glow = false): THREE.Object3D {
  const r = nodeRadius(n, prefs);
  //: 주제 틴트 — 꺼져 있으면(기본) undefined 라 잉크 그대로다. 표는 마운트가 만들어
  //: 노드에 실어 준다(`__tint`): 노드마다 전체 주제 목록을 다시 훑을 수 없기 때문이다.
  const tint = prefs.domainTint ? (n as { __tint?: string }).__tint : undefined;
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
}, prefs: ViewPrefs = loadPrefs(), keepCam: Cam3 | null = null): ForceGraph3DInstance {
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
  // 캔버스 투명(alpha) — 환경 대기(body 그라데이션)가 그래프 뒤로 비친다(D37 보강).
  // 다크는 반전 팔레트(D39): 어두운 배경 + 밝은 잉크 마크 — 양 테마 모두 투명.
  const darkTheme = document.documentElement.dataset.theme === "dark";
  const pal = themePalette();
  // 입자는 **상시 애니메이션**이다. 모션 감도를 밝힌 사용자에게는 켜지 않고 선으로
  // 되돌린다 — 이 모드의 이득(덜 붐빔)이 그 비용을 정당화하지 못하는 유일한 경우다.
  const reduceMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
  const particlesOn = prefs.edges === "particles" && !reduceMotion;
  //: Bring & Go — 선을 안 그리고 짚으면 이웃을 데려온다(아래 planFly3d).
  const bringOn = prefs.edges === "bring";
  // 상시 라벨 대상 — 2D 와 같은 규칙(연결수 상위 8, 고아 제외).
  const hubIds = new Set(nodes.slice()
    .sort((a, b) => (b.degree || 0) - (a.degree || 0) || (a.id < b.id ? -1 : 1))
    .slice(0, 8).filter((n) => (n.degree || 0) > 0).map((n) => n.id));
  // 주제 틴트(열린 층) — 마운트당 한 번 표를 만들어 노드에 실어 둔다. 각도가
  // 화면에 있는 주제들 위에서 배정되므로(view-tint.ts) 노드 하나만 보고는
  // 계산할 수 없다.
  const applyTints = () => {
    const tints = prefs.domainTint ? tintMap(nodes.map((n) => n.domain), themeMode()) : null;
    for (const n of nodes) (n as { __tint?: string }).__tint = tints?.get((n.domain || "").trim());
  };
  applyTints();
  // 인스턴싱 상태 — scene 은 ForceGraph3D 생성 뒤에야 얻는다(아래), 그래서 최초
  // 빌드에서는 scene?.add 가 아무 일도 안 하고, 생성 직후 명시적으로 한 번 더 붙인다.
  let scene: THREE.Scene | undefined;
  let instMeshes: THREE.InstancedMesh[] = [];
  const instRefs = new Map<string, InstRef>();
  // 지오메트리는 인스턴스 수만큼의 `instanceFade` 속성을 지니므로 빌드마다 복제본이고,
  // 재질도 빌드마다 새로 만든다 — 둘 다 여기서 함께 버린다(InstancedMesh.dispose 는
  // 인스턴스 버퍼만 놓는다).
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
    disposeInstMeshes();
    const counts: Record<InstKind, number> = { sphere: 0, bridge: 0, doc: 0 };
    for (const n of nodes) if (canInstance(n)) counts[instKindOf(n)]++;
    const built: Partial<Record<InstKind, THREE.InstancedMesh>> = {};
    (Object.keys(counts) as InstKind[]).forEach((k) => {
      if (!counts[k]) return;
      const geom = UNIT_GEOM[k].clone();
      geom.setAttribute("instanceFade",
        new THREE.InstancedBufferAttribute(new Float32Array(counts[k]), 1));
      const mesh = new THREE.InstancedMesh(geom, instMaterial(pal), counts[k]);
      // 인스턴스가 씬 전체에 흩어져 있어 컬링용 바운딩구는 전체를 감싸야 정확한데,
      // Bring&Go 비행·nodeScale 슬라이더로 개별 행렬을 나중에 또 고친 뒤에도 그
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
      const tint = (n as { __tint?: string }).__tint;
      mesh.setColorAt(idx, new THREE.Color(tint ? parseInt(tint.slice(1), 16) : pal.ink));
      instRefs.set(n.id, { kind, idx, mesh, baseR: r / (prefs.nodeScale ?? 1),
        proxy: fadeProxy(mesh.geometry.getAttribute("instanceFade") as THREE.InstancedBufferAttribute, idx) });
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
  const nodeFactory = () => (n: any) => {
    if (canInstance(n)) {
      const ghost = ghostNode(nodeRadius(n, prefs));
      // 유령은 `visible=false` 라 렌더러가 순회에서 자식째 건너뛴다 — 허브 라벨을
      // 유령의 자식으로 달면 라벨도 함께 안 그려진다. 라벨이 있는 노드만 보이는
      // 빈 그룹으로 감싸 유령과 라벨을 형제로 둔다(위치 추적은 그룹이 받는다).
      if (prefs.labels !== "none" && hubIds.has(n.id)) {
        const group = new THREE.Group();
        group.add(ghost, labelSprite(n.label, pal));
        return group;
      }
      return ghost;
    }
    const mesh = nodeObject(n, prefs, pal, true);
    // V7 — 허브(연결수 상위)는 **상시 라벨**을 단다. 2D 가 이미 쓰는 문법인데
    // 3D 만 hover 를 기다려, 42노드 렌즈가 "이름 없는 별자리"였다(실측: 한 번에
    // 하나씩 짚어야만 이름을 알 수 있었다). 나머지는 그대로 hover 로 뜬다.
    if (prefs.labels !== "none" && hubIds.has(n.id)) mesh.add(labelSprite(n.label, pal));
    return mesh;
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
    prefs.arrows && !particlesOn && !bringOn && isDirectedLink(l) ? 2.6 : 0;
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
    .linkCurvature(0.18)
    // 방향 화살촉 — prerequisite/derives/inherits 만 (spec §1.2); 표시 여부는 D31 열린 층
    .linkDirectionalArrowLength(arrowLenFn())
    // 입자 모드 — 방향 3종에만(위 상수 주석 ②). 흐름이 방향을 말하므로 화살촉은 끈다.
    .linkDirectionalParticles((l: any) => particlesOn && isDirectedLink(l)
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
  scene.add(...instMeshes);   // buildInstancedNodes() 최초 호출분 — scene 이 이제 있다
  // 환경맵이 돌아왔다(발주자 결정 2026-08-15). D77 이 유리와 함께 은퇴시켰던 것인데,
  // 반무광 재질의 clearcoat·sheen 이 반사할 것이 없으면 조형이 평면으로 죽는다.
  scene.environment = studioEnv(g.renderer(), pal);
  g.renderer().toneMapping = THREE.ACESFilmicToneMapping;
  g.renderer().toneMappingExposure = 1.06;
  // 원경이 배경으로 "서서히" 잦아듦. near/far 는 아래에서 **카메라 거리에 연동**한다
  // (고정값이면 축소가 곧 소멸이다 — syncCamera 주석 참조).
  const fog = flat ? null : new THREE.Fog(pal.fog, S * 2.6, S * 9);
  scene.fog = fog;
  g.lights([
    new THREE.AmbientLight(0xffffff, darkTheme ? 0.9 : 1.1),
    // 반구광(하늘=따뜻한 종이빛 / 지면=중간 잉크) — 플랫 셰이딩 면들에 부드러운
    // 명암 그라데이션을 깔아 "점토" 대신 "판재" 인상을 만든다(D33). 다크는 감광.
    new THREE.HemisphereLight(0xf0f1fa, 0x565b74, darkTheme ? 0.7 : 0.9),
    (() => { const d = new THREE.DirectionalLight(0xf0f1fa, darkTheme ? 1.1 : 1.5); d.position.set(60, 120, 80); return d; })(),
  ]);
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
  const baseDestructor = (g as any)._destructor?.bind(g);
  (g as any)._destructor = () => {
    dead = true; ro.disconnect();
    controls?.removeEventListener?.("change", syncCamera);
    window.clearInterval(napTimer);
    controls?.removeEventListener?.("change", onWakeEvent);
    for (const ev of ["pointermove", "pointerdown", "wheel", "keydown"] as const)
      el.removeEventListener(ev, onWakeEvent);
    disposeInstMeshes();
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
      ["nodeScale", "edgeScale", "arrows", "labels", "domainTint", "size", "spread", "railW"]);
    if (!changed.every((k) => ok.has(k))) return false;
    prefs = np;
    if (changed.includes("spread")) {
      S = SCALE * np.spread;
      place();                                   // 핀 좌표를 새 배율로 다시 놓고
      (g as any).d3ReheatSimulation?.();        // 위치 동기화 프레임을 1회 강제(settle 과 같은 길)
    }
    if (changed.some((k) => k === "labels" || k === "domainTint" || k === "size")) {
      applyTints();
      builtScale = np.nodeScale ?? 1;
      buildInstancedNodes();                     // 인스턴스 배치도 새 인코딩으로 다시 굽는다
      g.nodeThreeObject(nodeFactory());          // 새 함수 = 노드 오브젝트 전부 다시 굽기
    } else if (changed.includes("nodeScale")) {
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
    if (changed.includes("edgeScale")) g.linkWidth(linkWidthFn());
    if (changed.includes("arrows")) g.linkDirectionalArrowLength(arrowLenFn());
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
    // 고르는 행위가 곧 모으는 행위다. 같은 것을 다시 누르면 풀린다.
    if (bringOn) {
      gathered = gathered === n.id ? null : n.id;
      planFly3d(gathered);
    } else {
      // 클릭은 **중심에 놓기만** 한다(줌 없음). 파고드는 것은 카드의 줌 버튼이다 —
      // 고르는 손짓 하나가 카메라까지 끌고 들어가면 되돌릴 방법이 손에 없었다.
      centerOn3d(g, n, prefs);
    }
    handlers.onClick?.(n);
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
    // 데려오는 방식엔 선이 없다 — 고리 자체가 이웃을 말하므로 hover 복귀도 없다.
    if (bringOn) return pal.link.base + ALPHA0;
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
  const fadeTargets = new Map<THREE.Material | { opacity: number }, number>();
  let raf = 0;
  /** 모션을 줄여 달라는 설정에서는 **이징만** 버리고 효과는 남긴다(카피가 그렇게
   *  약속한다). 여태 이 트윈에는 가드가 없어 그 약속이 3D 에서만 지켜지지 않았다 —
   *  비행(`jumpFly`)과 평면(app.css 미디어 쿼리)은 이미 지키고 있었다. */
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
   *  하나가 다른 하나를 덮어쓴 채 아무도 눈치채지 못한다. `null` 이면 전부 복귀. */
  const applyFade = (keep: Set<string> | null) => {
    // D32-a 정본값 — 여태 여기가 `× 0.15` 리터럴이라 정본(0.40)의 3.75 배를 물러났고,
    // 비이웃이 배경 대비 1.15:1 로 **소멸**했다(가시성 연구 C4). 루프 밖에서 한 번 읽는다
    // (getComputedStyle 은 레이아웃 읽기라 마크마다 부르면 안 된다).
    const recedeNode = recede("node");
    const mixTarget = 1 - recedeNode;   // 인스턴스 노드 — 알파 대신 배경색 혼합비
    g.graphData().nodes.forEach((m: any) => {
      const fade = keep ? !keep.has(m.id) : false;
      const ref = instRefs.get(m.id);
      if (ref) fadeTargets.set(ref.proxy, fade ? mixTarget : 0);
      const obj = m.__threeObj as THREE.Mesh | undefined;
      if (!obj) return;
      obj.traverse((c: THREE.Object3D) => {
        const mat = (c as any).material as THREE.Material | undefined;
        if (mat && "opacity" in mat) {   // mesh fill·링·글로우 스프라이트 공통 — 각자 저장한 base 로 복귀
          const b = (mat.userData?.base as number | undefined) ?? (m.gateway ? GATE_OP : NODE_OP);
          mat.transparent = true; fadeTargets.set(mat, fade ? b * recedeNode : b);
        }
      });
    });
    if (!raf) raf = requestAnimationFrame(tween);
  };
  g.onNodeHover((n: any) => {
    // hover 는 **어둡게만** 한다. 모으는 것은 클릭이다(2D 와 같은 규칙, 발주자 지시
    // 2026-08-15) — 지나가기만 해도 씬이 재배열되면 눈이 쉴 곳이 없다. 고리가 서
    // 있는 동안 그 구성원 위를 지나도 흐림 기준은 흔들리지 않는다.
    if (bringOn && gathered && n && ring.has(n.id)) return;
    hoverId = n ? n.id : null;
    // 손을 떼면 점등이 있던 자리로 **돌아간다** — 점등 중에 지도 위를 지나갔다고
    // 걸어 둔 것이 사라지면, 밖에서 건 상태를 hover 가 조용히 지우는 셈이 된다.
    const keep = n ? new Set([n.id, ...(neighbors.get(n.id) || [])])
      : spot ? spot.nodes : null;
    applyFade(keep);
    g.linkColor(linkColorFn);   // 접근자 재적용 → 링크색 재평가
    el.style.cursor = n ? "pointer" : "";
  });

  // ── Bring & Go — 3D 판 ────────────────────────────────────────────────────
  //
  //  2D 와 같은 문법이되 두 가지가 다르다.
  //   ① 고리를 **카메라를 향한 평면**에 세운다. 월드 축에 세우면 보는 각도에 따라
  //      고리가 납작한 선으로 눌린다 — 2D 에는 없는 3D 고유의 함정이다.
  //   ② 옮기는 것은 노드 좌표가 아니라 **메시**다. 엔진이 `cooldownTicks(0)` 으로
  //      멈춰 있어 x/y/z 를 고쳐도 씬이 안 따라오고, 반대로 좌표를 그대로 두면
  //      집으로 돌아갈 정본(fx/fy/fz — 사이드카 D17)이 흔들리지 않는다.
  //
  //  이름은 hover 때 스프라이트를 달았다 뗀다. 전부 미리 달면 큰 그래프에서 텍스처가
  //  노드 수만큼 살아 있게 된다(허브 8개만 상시로 다는 이유와 같다).
  const flyTarget = new Map<string, THREE.Vector3>();
  const ringSprites = new Map<string, THREE.Sprite>();
  /** 지금 고리에 서 있는 것들(초점 포함) — 위 hover 배선이 이걸로 진동을 막는다. */
  let ring = new Set<string>();
  /** 모아 놓은 초점 — 클릭으로만 선다(위 onNodeHover 주석). */
  let gathered: string | null = null;
  let flyRaf = 0;
  const jumpFly = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
  const homeOf = (m: any) =>
    new THREE.Vector3(m.fx ?? m.x ?? 0, m.fy ?? m.y ?? 0, m.fz ?? m.z ?? 0);
  // 방향 3종만 방향을 갖는다. 고리에서 각도가 그것을 말한다(2D 와 같은 규칙).
  const inbound = new Map<string, Set<string>>();
  const outbound = new Map<string, Set<string>>();
  for (const l of links) {
    if (!isDirectedLink(l as any)) continue;
    const s = typeof l.source === "object" ? (l.source as any).id : l.source;
    const t = typeof l.target === "object" ? (l.target as any).id : l.target;
    (outbound.get(s) || outbound.set(s, new Set()).get(s)!).add(t);
    (inbound.get(t) || inbound.set(t, new Set()).get(t)!).add(s);
  }
  function arcAngles(count: number, center: number, width: number): number[] {
    if (count <= 0) return [];
    if (count === 1) return [center];
    const step = width / count;
    return Array.from({ length: count }, (_, i) => center - width / 2 + step * (i + 0.5));
  }
  /** 고리 자리 = 방향. 위 = 들어옴 · 아래 = 나감 · 좌우 = 무방향, 폭은 갈래 크기에
   *  비례한다. 왜 폭을 고정하지 않는지(그리고 순서만 주면 왜 안 되는지)는
   *  view2d.ts 의 같은 이름 함수에 적혀 있다 — 규칙이 갈라지면 두 관점이 다른
   *  말을 하므로 식은 글자 그대로 같게 둔다. 여기서도 sin 이 양수일 때 위다
   *  (카메라 up 벡터를 쓰므로 정의상 그렇다). */
  function ringSlots(id: string, list: string[]): { id: string; a: number }[] {
    const inn = inbound.get(id), out = outbound.get(id);
    const isIn = (x: string) => !!inn?.has(x);
    const isOut = (x: string) => !inn?.has(x) && !!out?.has(x);
    const up = list.filter(isIn);
    const down = list.filter(isOut);
    const side = list.filter((x) => !isIn(x) && !isOut(x));
    const n = list.length || 1;
    const wUp = (up.length / n) * Math.PI * 2;
    const wDown = (down.length / n) * Math.PI * 2;
    const wSide = Math.PI - (wUp + wDown) / 2;
    const half = Math.ceil(side.length / 2);
    const R = side.slice(0, half), L = side.slice(half);
    return [
      ...R.map((x, i) => ({ id: x, a: arcAngles(R.length, 0, wSide)[i] })),
      ...up.map((x, i) => ({ id: x, a: arcAngles(up.length, Math.PI / 2, wUp)[i] })),
      ...L.map((x, i) => ({ id: x, a: arcAngles(L.length, Math.PI, wSide)[i] })),
      ...down.map((x, i) => ({ id: x, a: arcAngles(down.length, -Math.PI / 2, wDown)[i] })),
    ];
  }

  function dropSprites() {
    ringSprites.forEach((sp) => {
      sp.parent?.remove(sp);
      (sp.material as THREE.SpriteMaterial).map?.dispose();
      sp.material.dispose();
    });
    ringSprites.clear();
  }
  function planFly3d(id: string | null) {
    flyTarget.clear();
    dropSprites();
    ring = new Set(id ? [id] : []);
    const data = g.graphData().nodes as any[];
    const byId = new Map<string, any>(data.map((m) => [m.id, m]));
    const hub = id ? byId.get(id) : null;
    if (hub) {
      const c = homeOf(hub);
      const cam: any = g.camera();
      const right = new THREE.Vector3(), up = new THREE.Vector3(), fwd = new THREE.Vector3();
      cam.matrixWorld.extractBasis(right, up, fwd);
      const slots = ringSlots(id!, [...(neighbors.get(id!) || [])].filter((x) => byId.has(x)));
      // 반지름은 씬 규모(S)에 매다. 화면 픽셀로 환산하는 2D 와 달리 3D 는 노드마다
      // 카메라 거리가 달라 "화면에서 몇 px" 이 한 값으로 정해지지 않는다.
      const r0 = S * (0.3 + Math.min(slots.length, 24) / 60);
      slots.forEach((s, i) => {
        const nid = s.id;
        const r = slots.length > 8 && i % 2 ? r0 * 1.6 : r0;   // 두 겹(2D 와 같은 이유)
        flyTarget.set(nid, c.clone()
          .add(right.clone().multiplyScalar(Math.cos(s.a) * r))
          .add(up.clone().multiplyScalar(Math.sin(s.a) * r)));
        ring.add(nid);
        const m = byId.get(nid);
        const obj = m?.__threeObj as THREE.Object3D | undefined;
        if (obj && !hubIds.has(nid) && prefs.labels !== "none") {
          const sp = labelSprite(m.label, pal);
          if (instRefs.has(nid)) {
            // 인스턴싱된 노드는 몸통(유령)이 `visible=false` 라 자식은 렌더러가 통째로
            // 건너뛴다 — 씬에 직접 달고 위치는 stepFly3d 가 매 프레임 따라간다.
            const offset = sp.position.clone();
            sp.userData.flyOffset = offset;
            sp.position.copy(obj.position).add(offset);
            scene?.add(sp);
          } else {
            obj.add(sp);
          }
          ringSprites.set(nid, sp);
        }
      });
    }
    for (const m of data) if (!flyTarget.has(m.id)) flyTarget.set(m.id, homeOf(m));
    if (jumpFly) { stepFly3d(1); return; }
    if (!flyRaf) flyRaf = requestAnimationFrame(tickFly3d);
  }
  /** 한 프레임 전진. 도착 판정은 씬 단위(S=140)에서 0.1 이면 화면에서 같은 픽셀이다.
   *
   *  인스턴싱된 노드는 몸통이 유령(obj)이 아니라 InstancedMesh 에 있으므로, 유령
   *  위치를 옮기는 것만으로는 화면이 안 따라온다 — 같은 프레임에 그 인스턴스의
   *  행렬도 새 위치로 다시 쓴다(회전·반지름은 그대로, 이동만). 고리 인원은
   *  보통 수십 개 이하라 매 프레임 다시 써도 비용이 무시할 만하다. */
  const flyM4 = new THREE.Matrix4();
  function stepFly3d(t: number): boolean {
    let live = false;
    for (const m of g.graphData().nodes as any[]) {
      const to = flyTarget.get(m.id);
      const obj = m.__threeObj as THREE.Object3D | undefined;
      if (!to || !obj) continue;
      if (obj.position.distanceToSquared(to) < 0.01) obj.position.copy(to);
      else { obj.position.lerp(to, t); live = true; }
      const ref = instRefs.get(m.id);
      if (ref) {
        const r = ref.baseR * (prefs.nodeScale ?? 1);
        flyM4.compose(obj.position, INST_ROT[ref.kind], new THREE.Vector3(r, r, r));
        ref.mesh.setMatrixAt(ref.idx, flyM4);
        ref.mesh.instanceMatrix.needsUpdate = true;
        const rsp = ringSprites.get(m.id);   // 씬에 직접 단 임시 라벨(위) — 따라오게
        if (rsp?.userData.flyOffset) rsp.position.copy(obj.position).add(rsp.userData.flyOffset);
      }
    }
    return live;
  }
  function tickFly3d() { wake(200); flyRaf = stepFly3d(0.22) ? requestAnimationFrame(tickFly3d) : 0; }

  // 해체는 호출부가 `_destructor()` 로 한다(view.ts). 그 자리에 정리를 얹는다 —
  // 안 하면 관점을 바꾼 뒤에도 rAF 가 죽은 씬을 계속 돌린다.
  // 고리는 지도 밖으로 나갈 때 풀린다(노드에서 벗어나는 것만으로는 안 풀린다).
  const leaveMap = () => {
    if (!bringOn || !ring.size) return;
    hoverId = null; gathered = null; planFly3d(null);
  };
  el.addEventListener("mouseleave", leaveMap);

  const dropped = (g as any)._destructor?.bind(g);
  (g as any)._destructor = () => {
    el.removeEventListener("mouseleave", leaveMap);
    if (flyRaf) cancelAnimationFrame(flyRaf);
    flyRaf = 0;
    // ⚠ 여기서 planFly3d(null) 를 부르면 안 된다 — 그 함수는 집으로 돌아가는 rAF 를
    // **다시 띄운다**. 해체 중에 되살아나면 죽은 씬을 계속 돌린다.
    dropSprites();
    flyTarget.clear();
    dropped?.();
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
  // D77 — 미니 그리드도 유리 반사원(PMREM/RoomEnvironment) 없이 그린다.

  type Cell = { scene: THREE.Scene; camera: THREE.PerspectiveCamera;
                el: HTMLElement; meshes: Map<string, THREE.Mesh>;
                rad: number; pad: number; dir: THREE.Vector3 };
  const built: Cell[] = cells.map((c) => {
    const scene = new THREE.Scene();
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
    //: 미니 그리드도 같은 표를 쓴다 — 셀마다 만들되 셀 안에서는 한 번이다.
    if (prefs.domainTint) {
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
      const miniTint = prefs.domainTint ? (n as { __tint?: string }).__tint : undefined;
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

/** 2D 유리 마크 — 3D 의 유리 노드(view3d.ts `GLASS`)를 평면 그림으로 **굽는다**.
 *
 *  2D 는 sigma 의 이미지 프로그램이 그린다. 그 프로그램의 색 모드는 그림의 **알파만** 쓰고
 *  RGB 를 버린다(마스크에 노드 색을 곱해 칠한다) — 그래서 마스크에 음영을 넣어도 보이지
 *  않는다. 유리 노드는 그림 모드로 그리고, 색과 음영을 그림 안에 굽는다. 몸통 색이 곧
 *  주제의 보석색이라 (모양 × 색 × 링 × 모드) 조합마다 한 장이고, 한 번 구운 것은 다시 쓴다.
 *
 *  좌표는 평면 마크(`markTexture`)와 같다: 64 단위 판에 반경 24, 링 반경 24×1.22. 그래서
 *  유리 마크와 마스크 마크(주제 겹껍질·관문)가 한 화면에서 같은 크기로 선다.
 *
 *  음영은 히어로 유리의 읽힘을 평면으로 옮긴 것이다: 왼쪽 위에서 오는 빛(밝은 쪽), 가장자리로
 *  갈수록 짙어지는 두께, 또렷한 반사점 하나, 아래쪽의 옅은 되비침. 각진 종(마름모·삼각형)은
 *  면마다 밝기를 달리해 입체로 읽히게 한다(3D 의 정팔면체·정사면체 고정 자세와 같은 방향).
 *  근거: [[decision:app-nodes-wear-the-hero-glass-2026-09-26]] */
import { MARK_TEX_PX } from "./surfaces/view-planar";

export type GlassShape = "sphere" | "diamond" | "triangle";

/** 유리로 그리는 종. 주제(겹껍질)와 관문(윤곽선)은 면이 없거나 반투명이라 마스크 마크로 남는다. */
export function glassShapeOf(kind: string | undefined, gateway: boolean): GlassShape | null {
  if (gateway || kind === "domain") return null;
  if (!kind || kind === "concept" || kind === "decision") return "sphere";
  if (kind === "bridge") return "diamond";
  return "triangle";
}

const S = MARK_TEX_PX, K = S / 64, C = S / 2, R = 24 * K, RING_R = R * 1.22, RING_W = 2 * K;
const cache = new Map<string, string>();

type RGB = [number, number, number];
const parse = (hex: string): RGB => {
  const n = parseInt(hex.replace("#", "").slice(0, 6), 16);
  return [n >> 16, (n >> 8) & 255, n & 255];
};
/** `t` 만큼 흰색(t>0) 또는 검정(t<0) 쪽으로. */
const shade = ([r, g, b]: RGB, t: number, a = 1): string => {
  const to = t >= 0 ? 255 : 0, k = Math.abs(t);
  const m = (v: number) => Math.round(v + (to - v) * k);
  return `rgba(${m(r)},${m(g)},${m(b)},${a})`;
};

/** 유리 마크 한 장(data URL). `ring` 은 선택으로 남은 노드에 두르는 외곽선이다. */
export function glassSprite(shape: GlassShape, hex: string, ring = false): string {
  const key = `${shape}|${hex}|${ring ? 1 : 0}`;
  const hit = cache.get(key);
  if (hit) return hit;
  const cv = document.createElement("canvas");
  cv.width = cv.height = S;
  const ctx = cv.getContext("2d");
  if (!ctx) return "";
  const col = parse(hex);
  if (shape === "sphere") sphere(ctx, col);
  else facets(ctx, col, shape === "diamond" ? diamondPts() : trianglePts());
  if (ring) {
    ctx.beginPath(); ctx.arc(C, C, RING_R, 0, Math.PI * 2);
    ctx.lineWidth = RING_W; ctx.strokeStyle = shade(col, 0, .55); ctx.stroke();
  }
  const url = cv.toDataURL("image/png");
  cache.set(key, url);
  return url;
}

/** 반사점 — 빛이 오는 왼쪽 위에 또렷한 타원 하나. */
function glint(ctx: CanvasRenderingContext2D, x: number, y: number, rx: number, ry: number, a: number) {
  ctx.save();
  ctx.translate(x, y); ctx.rotate(-0.6); ctx.scale(1, ry / rx);
  const g = ctx.createRadialGradient(0, 0, 0, 0, 0, rx);
  g.addColorStop(0, `rgba(255,255,255,${a})`);
  g.addColorStop(.55, `rgba(255,255,255,${a * .45})`);
  g.addColorStop(1, "rgba(255,255,255,0)");
  ctx.fillStyle = g; ctx.beginPath(); ctx.arc(0, 0, rx, 0, Math.PI * 2); ctx.fill();
  ctx.restore();
}

function sphere(ctx: CanvasRenderingContext2D, col: RGB) {
  // 몸통: 빛 쪽이 밝고 가장자리로 갈수록 짙어진다(유리의 두께).
  const body = ctx.createRadialGradient(C - R * .35, C - R * .4, R * .05, C, C, R * 1.05);
  body.addColorStop(0, shade(col, .42));
  body.addColorStop(.38, shade(col, .1));
  body.addColorStop(.78, shade(col, -.18));
  body.addColorStop(1, shade(col, -.5));
  ctx.fillStyle = body; ctx.beginPath(); ctx.arc(C, C, R, 0, Math.PI * 2); ctx.fill();
  // 아래쪽 되비침 — 바닥의 빛이 유리 안쪽에서 올라온다.
  const back = ctx.createRadialGradient(C + R * .2, C + R * .62, 0, C + R * .2, C + R * .62, R * .55);
  back.addColorStop(0, shade(col, .35, .55));
  back.addColorStop(1, shade(col, .35, 0));
  ctx.save(); ctx.beginPath(); ctx.arc(C, C, R, 0, Math.PI * 2); ctx.clip();
  ctx.fillStyle = back; ctx.fillRect(0, 0, S, S);
  // 가장자리 광택 — 클리어코트가 테두리를 따라 얇게 빛난다.
  ctx.lineWidth = R * .07; ctx.strokeStyle = "rgba(255,255,255,.16)";
  ctx.beginPath(); ctx.arc(C, C, R - R * .035, Math.PI * .9, Math.PI * 1.6); ctx.stroke();
  ctx.restore();
  glint(ctx, C - R * .36, C - R * .42, R * .3, R * .17, .92);
  glint(ctx, C + R * .42, C + R * .5, R * .07, R * .07, .35);
}

type Pt = [number, number];
const diamondPts = (): Pt[] => [[C, C - R], [C + R, C], [C, C + R], [C - R, C]];
/** 정사면체의 정면 — 평면 마크와 같은 꼭짓점(-90° · 30° · 150°, 외접원 1.08r). */
const trianglePts = (): Pt[] => [-90, 30, 150].map((deg) => {
  const t = (deg * Math.PI) / 180;
  return [C + Math.cos(t) * R * 1.08, C + Math.sin(t) * R * 1.08] as Pt;
});

/** 각진 종 — 가운데에서 꼭짓점으로 면을 나누고 면마다 빛을 달리 받는다. */
function facets(ctx: CanvasRenderingContext2D, col: RGB, pts: Pt[]) {
  const cx = pts.reduce((a, p) => a + p[0], 0) / pts.length;
  const cy = pts.reduce((a, p) => a + p[1], 0) / pts.length;
  pts.forEach((p, i) => {
    const q = pts[(i + 1) % pts.length];
    // 면의 방향 — 빛(왼쪽 위)을 향할수록 밝다.
    const mx = (p[0] + q[0]) / 2 - cx, my = (p[1] + q[1]) / 2 - cy;
    const lit = (-mx - my) / (Math.hypot(mx, my) * Math.SQRT2);        // -1 … 1
    const g = ctx.createLinearGradient(cx, cy, (p[0] + q[0]) / 2, (p[1] + q[1]) / 2);
    g.addColorStop(0, shade(col, .12 + lit * .22));
    g.addColorStop(1, shade(col, -.12 + lit * .3));
    ctx.fillStyle = g;
    ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(p[0], p[1]); ctx.lineTo(q[0], q[1]); ctx.closePath();
    ctx.fill();
  });
  // 모서리 광택 — 면이 만나는 선을 얇게 밝힌다.
  ctx.lineWidth = K * .9; ctx.strokeStyle = "rgba(255,255,255,.22)";
  ctx.beginPath();
  for (const p of pts) { ctx.moveTo(cx, cy); ctx.lineTo(p[0], p[1]); }
  ctx.stroke();
  ctx.lineWidth = K * .8; ctx.strokeStyle = shade(col, -.45, .8);
  ctx.beginPath(); pts.forEach((p, i) => (i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])));
  ctx.closePath(); ctx.stroke();
  glint(ctx, cx - R * .3, cy - R * .32, R * .22, R * .12, .8);
}

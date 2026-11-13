/** 나침반 렌즈의 문법을 SVG 문자열로 짓는다: 노드의 모양(종류마다), 빛살(뜻의 관계), 방향 있는 관계의 표.
 *  그리기(view-compass.ts)가 층 · 전체 보기 · 범례 · 요약의 표식에 받아 쓴다. 모양과 표는 받은 값만 읽고,
 *  빛살은 그리는 그 순간의 테마와 섞기를 손잡이(RayLook)로 읽는다. */
import type { Pt } from "./view-compass-gather";

/** 빛살이 부르는 그 순간에 읽는 것: 테마(라이트면 띠를 굵게)와 섞기(주제 색을 카드색 쪽으로 바랜다) */
export interface RayLook { theme: () => 'dark' | 'light'; mix: (c: string, p: number) => string }

// ── 모양·빛살·표(문법) ─────────────────────────────────────────────────────────────────────
export const GAP = 4, P = (pts: Pt[]) => pts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
export const P2 = (pts: Pt[]) => pts.map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`).join(' ');   // 줄기 조각: 0.1 단위로 자르면 확대했을 때 호가 각진다
export const arcPts = (cx0: number, cy0: number, r: number, mid: number, half: number, n = 6) => { const o: Pt[] = []; for (let q = 0; q <= n; q++) { const a = mid - half + (2 * half * q) / n; o.push([cx0 + Math.cos(a) * r, cy0 + Math.sin(a) * r]); } return o; };
export function shape(kind: string, x: number, y: number, r: number, fill: string, hollow?: boolean) {
  const st = hollow ? `fill="none" stroke="${fill}" stroke-width="1.6"` : `fill="${fill}"`;
  if (kind === 'doc') return `<g class="shape"><circle cx="${x}" cy="${y}" r="${r}" ${st}/><rect x="${x - r * .42}" y="${y - r * .12}" width="${r * .84}" height="${r * .24}" rx="${r * .1}" fill="var(--c-card)"/></g>`;
  if (kind === 'decision') { const d = r * 1.25; return `<path class="shape" d="M${x} ${y - d}L${x + d} ${y}L${x} ${y + d}L${x - d} ${y}Z" ${st}/>`; }
  if (kind === 'record') { const s = r * .9; return `<rect class="shape" x="${x - s}" y="${y - s}" width="${2 * s}" height="${2 * s}" rx="${r * .25}" ${st}/>`; }
  if (kind === 'episode') { const h = r * 1.2; return `<path class="shape" d="M${x} ${y - h}L${x + h * 1.05} ${y + h * .75}L${x - h * 1.05} ${y + h * .75}Z" ${st} stroke-linejoin="round"/>`; }
  // 다리는 범례와 같은 이음 표식(위아래 호 둘과 가운데 점)이다. 끝 주제 색을 아는 자리는 `junction` 이 그린다
  if (kind === 'bridge') { const k = r * .7, w = Math.max(1.6, r * .36).toFixed(2);
    return `<g class="shape"><path d="M${x - k} ${y - k}A${r} ${r} 0 0 1 ${x + k} ${y - k}M${x + k} ${y + k}A${r} ${r} 0 0 1 ${x - k} ${y + k}" fill="none" stroke="${fill}" stroke-width="${w}" stroke-linecap="round"/><circle cx="${x}" cy="${y}" r="${(r * .33).toFixed(2)}" fill="${fill}"/></g>`; }
  return `<circle class="shape" cx="${x}" cy="${y}" r="${r}" ${st}/>`;
}
// 빛살: 가운데 쪽이 굵고 바깥으로 가늘다. 양끝은 노드와 같은 중심의 원호로, 틈만큼 떨어져 잘린다
export type End = { id: string; x: number; y: number; r: number; c: string };
/** 빛살 하나를 짓는 함수(ray)를 돌려준다. 테마 · 섞기는 손잡이라 부를 때마다 지금 값을 읽는다 */
export function compassRay(look: RayLook) {
  const { theme, mix } = look;
  function ray(a: End, b: End, i: number, w0 = 3, w1 = .5) {
    if (theme() === 'light') { w0 *= 1.45; w1 = Math.max(w1 * 1.6, 1); }   // 밝은 바탕에서는 가는 띠가 묻힌다: 라이트만 굵게, 바깥 끝도 1 아래로 가늘어지지 않게
    const th = Math.atan2(b.y - a.y, b.x - a.x), ra = a.r + GAP, rb = b.r + GAP;
    const pts = [...arcPts(a.x, a.y, ra, th, Math.asin(Math.min(.95, w0 / ra))), ...arcPts(b.x, b.y, rb, th + Math.PI, Math.asin(Math.min(.95, w1 / rb))).reverse()];
    const x0 = a.x + Math.cos(th) * ra, y0 = a.y + Math.sin(th) * ra, x1 = b.x - Math.cos(th) * rb, y1 = b.y - Math.sin(th) * rb;
    return { def: `<linearGradient id="r${i}" gradientUnits="userSpaceOnUse" x1="${x0}" y1="${y0}" x2="${x1}" y2="${y1}"><stop offset="0" style="stop-color:${mix(a.c, 85)}"/><stop offset="1" style="stop-color:${mix(b.c, 30)}"/></linearGradient>`,
             svg: `<polygon class="ray" data-a="${a.id}" data-b="${b.id}" points="${P(pts)}" style="fill:url(#r${i})"/>`, x0, y0, x1, y1 };
  }
  return { ray };
}
export function bead(x: number, y: number, th: number, c: string, kind: string) {
  const p = (dx: number, dy: number): Pt => [x + Math.cos(th) * dx - Math.sin(th) * dy, y + Math.sin(th) * dx + Math.cos(th) * dy], f = (q: Pt) => `${q[0].toFixed(1)} ${q[1].toFixed(1)}`;
  let d = `M${f(p(-1.5, -4))}L${f(p(2.5, 0))}L${f(p(-1.5, 4))}`, extra = '';
  if (kind === 'INHERITS_FROM') d += `M${f(p(-6, -4))}L${f(p(-6, 4))}`;
  if (kind === 'DERIVES_FROM') { const q = p(-6.5, 0); extra = `<circle cx="${q[0].toFixed(1)}" cy="${q[1].toFixed(1)}" r="2.2" fill="var(--c-card)" stroke="${c}" stroke-width="1.4"/>`; }
  return `<path class="ray" d="${d}" fill="none" stroke="${c}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>${extra}`;
}

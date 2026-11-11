/** 주제 색 바닥 표 생성기 — `src/view-tint-floor.ts` 를 짓는다.
 *
 *     cd app && node dev/topic-tint-floor.mjs           # 지을 표를 재기만 한다
 *     cd app && node dev/topic-tint-floor.mjs --write   # 표를 쓴다(토큰 · 줄 칠 · 바닥이 바뀐 뒤)
 *     ... --rule=all|normal|none --out=<file>             # 쌍 규칙의 범위를 바꿔 다른 곳에 짓는다(비교용)
 *
 *  무엇을 푸는가: 계획(`tintPlan`)의 색 전부(영역 24 개 x 주제 돌림 7 칸, 모드마다)를 마크 바닥(`floorKit.floor`,
 *  비텍스트 바닥) 아래로 내리지 않으면서 계획에서 되도록 덜 옮긴다.
 *    - 이미 바닥을 넘는 색은 **그대로 둔다**(표에 0, 0 이 들어가고 앱도 표를 읽기 전에 그대로 돌려준다).
 *    - 못 넘는 색만 옮긴다. 색상각은 그대로 두고 OKLab 명도와 채도 몫을 고른다. 하나씩 바닥에 붙이면 색상각이
 *      가까운 둘(영역 보석을 20 도씩 돌린 벌이 다른 보석과 만난다)이 같은 명도로 모여 한 색이 될 수 있어서,
 *      옮기는 색은 계획의 색 전부와 함께 보고 고른다. 기준(벌점, CIEDE2000):
 *      정상 시각은 바닥 전 5 이상 떨어진 둘이 바닥 뒤에도 5 이상이고 어떤 둘도 바닥 전 거리의 절반 아래로 내려가지
 *      않는다. 적록 두 색각(Machado 2009 정도 1.0)은 5 이상이던 둘이 3 이상이다. 그다음이 계획 색에서 옮긴 거리
 *      (CIEDE2000)가 작은 것이다. 명도는 검정 · 흰빛으로 읽히지 않는 범위(`L_RANGE`) 안이다.
 *  풀이는 좌표 하강(색 하나씩, 정해진 순서로, 바뀌는 것이 없을 때까지)이라 같은 입력이면 같은 표가 나온다.
 *  표의 열쇠(`FLOOR_KEY`)는 입력 토큰의 지문이라, 토큰이 바뀌면 앱은 표를 버리고 색마다 바닥에 붙이며
 *  `tests/topic-tint-contrast.test.mjs` 가 실패해 이 스크립트를 다시 돌리라고 알린다. */
import { writeFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { rgbOfHex, lab, de2000Lab, simulate, oklch } from "./colour-math.mjs";

const SRC = resolve(dirname(fileURLToPath(import.meta.url)), "../src");
const WRITE = process.argv.includes("--write");
const arg = (k) => { const a = process.argv.find((x) => x.startsWith(`--${k}=`)); return a ? a.slice(k.length + 3) : null; };
/** 어느 쌍 규칙을 지키는가: all = 정상 시각 + 적록 두 색각(기본), normal = 정상 시각만(색각 쪽은 재기만 한다),
 *  none = 규칙 없이 색마다 계획 색에 가장 가까운 자리(하나씩 바닥에 붙인 것과 같다). */
const RULE = arg("rule") || "all";
if (!["all", "normal", "none"].includes(RULE)) throw new Error(`--rule=${RULE}: all, normal, none 중 하나`);
/** 표를 쓸 곳(기본은 src/view-tint-floor.ts). 다른 규칙의 표를 비교용으로 따로 지을 때 쓴다. */
const OUT = arg("out") ? resolve(arg("out")) : resolve(SRC, "view-tint-floor.ts");
const r = await build({ entryPoints: [resolve(SRC, "view-tint.ts")], bundle: true, platform: "node", format: "esm", write: false, logLevel: "silent" });
const { floorKit: K } = await import(`data:text/javascript;base64,${Buffer.from(r.outputFiles[0].text).toString("base64")}`);

const VIS = [(c) => c, (c) => simulate("deutan", c), (c) => simulate("protan", c)];
const CHROMA = [1, 0.85, 0.7, 0.55];
/** 명도의 바깥 끝. 라이트에서 이보다 어두우면 보석이 아니라 검정으로, 다크에서 이보다 밝으면 흰빛으로 읽힌다. */
const L_RANGE = { light: [0.3, 1], dark: [0, 0.9] };
/** OKLCH 채도의 아래 끝. 이보다 묽으면 마크가 보석이 아니라 회색으로 읽힌다(원래 색이 이보다 묽으면 원래 채도까지). */
const C_MIN = 0.05;
/** 규칙의 문턱 위에 두는 여유(거리 단위). 문턱에 딱 붙은 해는 8비트로 내리면서 넘어간다. */
const SLACK = 0.4;
/** 규칙을 어긴 거리 1 의 벌점. 계획 색에서 옮긴 거리 1(CIEDE2000)의 벌점은 1 이라 규칙이 먼저고 덜 옮김이 그다음이다. */
const RULE_WEIGHT = 20;

function solve(mode) {
  const slots = [];
  for (let a = 0; a < K.areas; a++) for (let k = 0; k < K.turns; k++) slots.push(K.rawPlanColour(a, k, mode));
  const raw = [...new Set(slots)], n = raw.length;
  const base = raw.map((h) => VIS.map((f) => lab(f(rgbOfHex(h)))));
  const d0 = VIS.map((_, v) => raw.map((_, i) => raw.map((_, j) => de2000Lab(base[i][v], base[j][v]))));
  // 고를 수 있는 자리: 바닥을 넘는 명도 x 채도 몫. 같은 HEX 로 떨어지는 자리는 하나만 남긴다.
  const cand = raw.map((h, i) => {
    if (K.passes(h, mode)) return [{ L: 0, cs: 0, hex: h, labs: base[i] }];   // 바닥을 넘는 색은 그대로
    const seen = new Set(), out = [], C0 = oklch(rgbOfHex(h))[1];
    for (const cs of CHROMA) for (let L = L_RANGE[mode][0]; L <= L_RANGE[mode][1] + 1e-9; L += 0.004) {
      const hex = K.markAt(h, +L.toFixed(4), cs);
      if (seen.has(hex) || !K.passes(hex, mode) || oklch(rgbOfHex(hex))[1] < Math.min(C_MIN, C0) - 1e-9) continue;
      seen.add(hex); out.push({ L: +L.toFixed(4), cs, hex, labs: VIS.map((f) => lab(f(rgbOfHex(hex)))) });
    }
    return out;
  });
  const pen = (v, i, j, d) => {
    const D = d0[v][i][j];
    if (RULE === "none" || (RULE === "normal" && v > 0)) return 0;
    if (v === 0) return RULE_WEIGHT * (Math.max(0, (D >= 5 ? 5 + SLACK : 0) - d) + Math.max(0, D / 2 + SLACK - d));
    return D >= 5 ? RULE_WEIGHT * Math.max(0, 3 + SLACK - d) : 0;
  };
  const stay = (i, c) => de2000Lab(base[i][0], c.labs[0]);   // 계획 색에서 옮긴 거리
  // 시작: 색마다 계획 색에 가장 가까운 자리 — 하나씩 바닥에 붙인 것과 같다
  const pick = cand.map((cs, i) => cs.reduce((b, c, k) => (stay(i, c) < stay(i, cs[b]) - 1e-12 ? k : b), 0));
  const cost = (i, c) => {
    let s = stay(i, c);
    for (let j = 0; j < n; j++) if (j !== i) for (let v = 0; v < 3; v++) s += pen(v, i, j, de2000Lab(c.labs[v], cand[j][pick[j]].labs[v]));
    return s;
  };
  for (let sweep = 0; sweep < 30; sweep++) {
    let changed = 0;
    for (let x = 0; x < n; x++) {
      const i = sweep % 2 ? n - 1 - x : x;   // 앞뒤로 번갈아 훑는다(한 방향만 훑으면 앞쪽 색이 먼저 자리를 잡아 뒤쪽이 몰린다)
      let best = pick[i], bc = cost(i, cand[i][best]);
      cand[i].forEach((c, k) => { const x = cost(i, c); if (x < bc - 1e-9) { bc = x; best = k; } });
      if (best !== pick[i]) { pick[i] = best; changed++; }
    }
    console.error(`${mode} sweep ${sweep}: ${changed} changed`);
    if (!changed) break;
  }
  // 둘째 단계: 아직 규칙을 어기는 쌍마다 두 색을 **함께** 옮겨 본다(하나씩 옮기면 서로 막혀 못 빠지는 자리).
  const pairPen = (i, ci, j, cj) => { let s = 0; for (let v = 0; v < 3; v++) s += pen(v, i, j, de2000Lab(ci.labs[v], cj.labs[v])); return s; };
  const rest = (i, c, skip) => { let s = stay(i, c); for (let k = 0; k < n; k++) if (k !== i && k !== skip) for (let v = 0; v < 3; v++) s += pen(v, i, k, de2000Lab(c.labs[v], cand[k][pick[k]].labs[v])); return s; };
  for (let round = 0; round < 12; round++) {
    let moved = 0;
    for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) {
      if (pairPen(i, cand[i][pick[i]], j, cand[j][pick[j]]) <= 0) continue;
      const Ai = cand[i].map((c) => rest(i, c, j)), Aj = cand[j].map((c) => rest(j, c, i));
      let best = [pick[i], pick[j]], bc = Ai[pick[i]] + Aj[pick[j]] + pairPen(i, cand[i][pick[i]], j, cand[j][pick[j]]);
      for (let a = 0; a < cand[i].length; a++) for (let b = 0; b < cand[j].length; b++) {
        const t = Ai[a] + Aj[b]; if (t >= bc) continue;
        const x = t + pairPen(i, cand[i][a], j, cand[j][b]); if (x < bc - 1e-9) { bc = x; best = [a, b]; }
      }
      if (best[0] !== pick[i] || best[1] !== pick[j]) { [pick[i], pick[j]] = best; moved++; }
    }
    console.error(`${mode} pair round ${round}: ${moved} moved`);
    if (!moved) break;
  }
  // 셋째 단계: 규칙 벌점을 늘리지 않는 한 계획 색에 더 가까운 자리로 되돌린다(규칙이 먼저, 덜 옮김이 그다음).
  const rulePen = (i, c) => { let s = 0; for (let j = 0; j < n; j++) if (j !== i) for (let v = 0; v < 3; v++) s += pen(v, i, j, de2000Lab(c.labs[v], cand[j][pick[j]].labs[v])); return s; };
  for (let round = 0; round < 20; round++) {
    let back = 0;
    for (let i = 0; i < n; i++) {
      if (cand[i].length === 1) continue;
      const now = rulePen(i, cand[i][pick[i]]), here = stay(i, cand[i][pick[i]]);
      const order = cand[i].map((c, k) => [stay(i, c), k]).filter(([d]) => d < here - 1e-9).sort((a, b) => a[0] - b[0] || a[1] - b[1]);
      for (const [, k] of order) if (rulePen(i, cand[i][k]) <= now + 1e-9) { pick[i] = k; back++; break; }
    }
    console.error(`${mode} pull-back round ${round}: ${back} moved closer`);
    if (!back) break;
  }
  const at = new Map(raw.map((h, i) => [h, cand[i][pick[i]]]));
  return slots.flatMap((h) => [at.get(h).L, at.get(h).cs]);
}

const table = { light: solve("light"), dark: solve("dark") };
const body = `// AUTO-GENERATED by app/dev/topic-tint-floor.mjs --write. Do not hand-edit.
// 계획의 색마다 [OKLab 명도, 채도 몫] 한 쌍(0, 0 은 옮기지 않는 색), 차례는 (영역 x ${K.turns} + 주제 돌림). FLOOR_KEY 는 입력 토큰의 지문이다.
export const FLOOR_KEY = ${K.floorInputKey()};
export const FLOOR_AREAS = ${K.areas};
export const FLOOR_TABLE: Record<"light" | "dark", readonly number[]> = {
  light: [${table.light.join(", ")}],
  dark: [${table.dark.join(", ")}],
};
`;
if (WRITE) { writeFileSync(OUT, body); console.log(`wrote ${OUT} (rule=${RULE})`); }
else console.log(body.slice(0, 400) + "\n... (run with --write to save)");

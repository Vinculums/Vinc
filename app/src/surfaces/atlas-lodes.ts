/** 생키 lode 배치 — 순수 산법 모듈 (D68).
 *
 *  mountTotals(atlas-planar)의 경로 보존 리본(D63)은 **정렬이 곧 레이아웃**이다:
 *  튜플을 정렬한 순서대로 오프셋을 누적한 것이 노드 안 띠(lode)의 세로 자리가
 *  된다. 이 모듈은 그 정렬을 전역 고정 키에서 barycenter 스윕으로 바꾼다.
 *
 *  **교차 불변 정리 — 이 모듈이 하는 것과 하지 않는 것.** stratum(노드) 순서가
 *  고정이고 주제 노드 안 들어오는 순서 == 나가는 순서(띠 정합)인 한, lode
 *  재정렬로는 교차 **총수**가 줄지 않는다: 다른 stratum 간 교차는 stratum 순서가
 *  전부 결정하고, 같은 주제 안 "충돌쌍"(출처 순서와 상태 순서가 반대인 튜플쌍)은
 *  어떤 공통 밴드 순서를 잡아도 왼쪽 아니면 오른쪽 갭에서 정확히 1회 교차한다.
 *  종전 (oi,si) 사전순도 이미 그 최소치였다 — 단 충돌 교차를 전부 오른쪽 갭에
 *  몰아넣어 화면 오른쪽 절반만 엉켜 보였다. 재정렬의 실효는 ① 교차의 양 갭 분산
 *  ② 평균 기울기·방랑 축소(리본이 납작해져 강처럼 읽힌다)다. 그래서 목표
 *  메트릭은 교차 수가 아니라 :func:`wanderCost` 이고, :func:`countCrossings` 는
 *  총수가 안 변했음을 확인하는 계측이다(줄었다면 그게 오히려 결함 신호다).
 *
 *  계약: DOM·COPY·렌더 치수 import 0(순수) · Math.random·시간 금지(결정론) ·
 *  좌표는 픽셀이 아니라 **개념 단위(unit-space)** — 픽셀 기반이면 순서가 컨테이너
 *  높이의 함수가 되어 리사이즈가 밴드를 재셔플한다. mount 에서 1회 계산하고
 *  draw(w,h) 는 순서를 소비만 한다. */

/** 경로 보존 튜플의 배치 시점 표현 — (출처 stratum, 주제 stratum, 상태 stratum)
 *  인덱스와 개념 수. atlas-planar 의 TotalsPath 를 인덱스화한 것과 동형. */
export type Lode = { oi: number; di: number; si: number; n: number };

/** 열별 lode 순서 — 값은 튜플 배열 인덱스, 위→아래. byTopic 이 in·out 양쪽의
 *  유일한 정본이라 띠 정합이 정렬 규칙이 아니라 **자료구조로** 보장된다. */
export type LodeOrders = {
  byOrigin: number[][];
  byTopic: number[][];
  byState: number[][];
};

/** 튜플별 단위 오프셋(자기 stratum 상단에서 자기 띠 상단까지, 개념 단위).
 *  렌더는 `box.y + off * scale` 로 픽셀에 앉힌다. */
export type LodeOffsets = { offL: number[]; offB: number[]; offR: number[] };

/** 열 구성(각 stratum 의 값 합)은 순서에서 파생된다 — 별도 인자를 받지 않아야
 *  순서와 열 구성이 어긋날 길이 없다. */
const stratumTotals = (tuples: Lode[], groups: number[][]) =>
  groups.map((g) => g.reduce((s, i) => s + tuples[i].n, 0));

const prefix = (tot: number[]) => {
  const base: number[] = [];
  let y = 0;
  for (const v of tot) { base.push(y); y += v; }
  return base;
};

export function lodeOffsets(tuples: Lode[], ord: LodeOrders): LodeOffsets {
  const fill = (groups: number[][], out: number[]) => {
    for (const g of groups) {
      let y = 0;
      for (const ti of g) { out[ti] = y; y += tuples[ti].n; }
    }
  };
  const offL = new Array<number>(tuples.length).fill(0);
  const offB = new Array<number>(tuples.length).fill(0);
  const offR = new Array<number>(tuples.length).fill(0);
  fill(ord.byOrigin, offL);
  fill(ord.byTopic, offB);
  fill(ord.byState, offR);
  return { offL, offB, offR };
}

/** 세 열의 절대 단위 y 중심 — stratum 패딩·세로 중앙 정렬은 무시한 근사다.
 *  세 열의 값 합이 같으므로(보존 불변) 좌표계가 서로 통약된다. */
function absCenters(tuples: Lode[], ord: LodeOrders) {
  const oBase = prefix(stratumTotals(tuples, ord.byOrigin));
  const dBase = prefix(stratumTotals(tuples, ord.byTopic));
  const sBase = prefix(stratumTotals(tuples, ord.byState));
  const { offL, offB, offR } = lodeOffsets(tuples, ord);
  const at = (t: Lode, si: number, base: number[], off: number[], i: number) =>
    base[si] + off[i] + t.n / 2;
  const yL = tuples.map((t, i) => at(t, t.oi, oBase, offL, i));
  const yB = tuples.map((t, i) => at(t, t.di, dBase, offB, i));
  const yR = tuples.map((t, i) => at(t, t.si, sBase, offR, i));
  return { yL, yB, yR };
}

/** 방랑 비용 J = Σ n·((yL−yB)² + (yB−yR)²) — 리본 세로 이동의 **제곱** 합.
 *  교차 총수가 불변이므로(모듈 머리 정리) 이것이 재정렬의 목표 메트릭이다.
 *
 *  절댓값(L1)이 아니라 제곱(L2)인 이유(실측): 밴드 중심이 양끝 사이에 있으면
 *  |yL−yB| + |yB−yR| = |yR−yL| 로 **yB 와 무관**해진다 — 전형적 배치가 전부
 *  그 평탄 구간에 들어가 barycenter 가 밴드를 실제로 개선해도 L1 은 보지
 *  못했다. L2 는 중점(= barycenter 자리)에서 유일 최소라 개선을 추적한다.
 *  기울기 분산을 벌하는 성질도 지각과 맞다 — 한쪽 0°·한쪽 급경사보다 양쪽
 *  완만이 낫다(ParSetgnostics 의 Slope 지표와 같은 방향). */
export function wanderCost(tuples: Lode[], ord: LodeOrders): number {
  const c = absCenters(tuples, ord);
  return tuples.reduce((s, t, i) =>
    s + t.n * ((c.yL[i] - c.yB[i]) ** 2 + (c.yB[i] - c.yR[i]) ** 2), 0);
}

/** 갭별 교차 계측 — 쌍의 상대 순서가 갭 양끝에서 뒤집히면 교차 1. O(T²) 쌍
 *  비교(T 는 기껏 수백). weighted 는 굵기 곱(n·n) — 지각 무게의 근사. */
export function countCrossings(tuples: Lode[], ord: LodeOrders):
  { gap0: number; gap1: number; weighted0: number; weighted1: number } {
  const c = absCenters(tuples, ord);
  let gap0 = 0, gap1 = 0, weighted0 = 0, weighted1 = 0;
  for (let a = 0; a < tuples.length; a++) {
    for (let b = a + 1; b < tuples.length; b++) {
      const w = tuples[a].n * tuples[b].n;
      if ((c.yL[a] - c.yL[b]) * (c.yB[a] - c.yB[b]) < 0) { gap0++; weighted0 += w; }
      if ((c.yB[a] - c.yB[b]) * (c.yR[a] - c.yR[b]) < 0) { gap1++; weighted1 += w; }
    }
  }
  return { gap0, gap1, weighted0, weighted1 };
}

/** barycenter 교대 스윕 — 3라운드, 한 라운드 = 가운데 당김 → 바깥 재동기.
 *
 *  (b) 가운데 열은 결합 barycenter: 튜플의 좌·우 가닥 중심의 **중점** 순으로
 *      각 주제 밴드를 정렬한다(L2 목적함수의 점별 최소 자리). 주제 축을 왼쪽
 *      기준·오른쪽 기준으로 따로 쓸면 공유 밴드 순서가 하나뿐이라 서로의 결과를
 *      되감는 핑퐁이 된다 — 그래서 양측을 한 번에 당긴다.
 *  (a) 바깥 열은 닫힌 해다: 각 출처·상태 stratum 의 lode 를 밴드 y 중심 순으로
 *      정렬하면 같은 stratum 끼리의 갭 교차가 0 이 되고(재배열 부등식), 초기
 *      의미 순서는 이 정렬의 고정점이다 — 그래서 라운드가 가운데부터 돈다.
 *
 *  수렴 검사 대신 **keep-best**: 라운드마다(바깥 재동기 **후에만** — 바깥이
 *  어긋난 중간 상태를 후보로 삼지 않는다) J 를 재고 엄격히 나아진 스냅숏만
 *  보관한다. 진동해도 최선 라운드를 돌려주고, 전면 동률이면 초기(종전 의미
 *  순서) 그대로라 그림이 바뀌지 않는다. 동률 비교자는 항상 의미 순서
 *  (di,si)/(oi,si)/(di,oi) 를 뒤에 붙인다 — 정렬 안정성에 기대지 않는다. */
export function orderLodes(tuples: Lode[], nOrigins: number, nTopics: number,
                           nStates: number): LodeOrders {
  const mk = (n: number) => Array.from({ length: n }, () => [] as number[]);
  const byOrigin = mk(nOrigins), byTopic = mk(nTopics), byState = mk(nStates);
  tuples.forEach((t, i) => {
    byOrigin[t.oi].push(i); byTopic[t.di].push(i); byState[t.si].push(i);
  });
  const semL = (a: number, b: number) =>
    tuples[a].di - tuples[b].di || tuples[a].si - tuples[b].si;
  const semB = (a: number, b: number) =>
    tuples[a].oi - tuples[b].oi || tuples[a].si - tuples[b].si;
  const semR = (a: number, b: number) =>
    tuples[a].di - tuples[b].di || tuples[a].oi - tuples[b].oi;
  for (const g of byOrigin) g.sort(semL);
  for (const g of byTopic) g.sort(semB);
  for (const g of byState) g.sort(semR);

  const ord: LodeOrders = { byOrigin, byTopic, byState };
  const snap = (): LodeOrders => ({
    byOrigin: byOrigin.map((g) => [...g]),
    byTopic: byTopic.map((g) => [...g]),
    byState: byState.map((g) => [...g]),
  });
  let best = snap();
  let bestJ = wanderCost(tuples, ord);
  for (let r = 0; r < 3; r++) {
    let c = absCenters(tuples, ord);
    const { yL, yR } = c;
    for (const g of byTopic)
      g.sort((a, b) => (yL[a] + yR[a]) - (yL[b] + yR[b]) || semB(a, b));
    c = absCenters(tuples, ord);
    const yB = c.yB;
    for (const g of byOrigin) g.sort((a, b) => yB[a] - yB[b] || semL(a, b));
    for (const g of byState) g.sort((a, b) => yB[a] - yB[b] || semR(a, b));
    const j = wanderCost(tuples, ord);
    if (j < bestJ - 1e-9) { bestJ = j; best = snap(); }
  }
  return best;
}

// ── Others 버킷 (Stage 1) ───────────────────────────────────────────────────

/** 병합 stratum 의 예약 키 — 실주제와 충돌하면 버킷 전체를 비활성한다(가드는
 *  bucketOthers 안). 화면 라벨은 호출부가 COPY 로 채운다(이 모듈은 카피 무지). */
export const OTHERS_KEY = "__others__";

/** mountTotals 의 2열 행과 동형 — 렌더러의 data 구축 결과를 그대로 받는다. */
export type TotalsDatum = {
  domain: string; label: string; origin: number[]; state: number[]; total: number;
};

/** 작은 주제를 "기타" stratum 하나로 **통병합**한다(Stage 1).
 *
 *  튜플 부분 병합이 아니라 주제 단위다 — 한 주제의 일부 튜플만 걷어내면 남은
 *  주제의 노드 값이 원장 수치와 어긋난다(화면에 남은 주제는 전부 온전한 값).
 *  주제를 통째로 옮기므로 주변합·보존 불변은 자동 유지된다.
 *
 *  전제: data 는 크기순(concepts 내림차순) — 순위 = 배열 인덱스.
 *  병합 대상 M = { 순위 > maxTopics } ∪ { 몫 < minShare 이고 순위 > keepTop }.
 *  keepTop 가드가 "전 주제가 임계 미만" 퇴화(전부 병합 → stratum 1개)를 막고,
 *  |M| ≥ 2 일 때만 발동한다(1개 병합은 개명일 뿐 이득이 없다). */
export function bucketOthers(data: TotalsDatum[], tuples: Lode[], grand: number,
  opts: { maxTopics?: number; minShare?: number; keepTop?: number;
          keep?: Set<string> } = {}):
  { data: TotalsDatum[]; tuples: Lode[]; mergedTopics: number; mergedDocs: number } {
  const maxTopics = opts.maxTopics ?? 12;
  const minShare = opts.minShare ?? 0.02;
  const keepTop = opts.keepTop ?? 3;
  const pinned = opts.keep;
  const none = { data, tuples, mergedTopics: 0, mergedDocs: 0 };
  if (grand <= 0 || data.some((r) => r.domain === OTHERS_KEY)) return none;
  const merged = new Set<number>();
  data.forEach((r, di) => {
    // 핀(D70) — 지금 가리키고 있는 주제는 병합에서 뺀다. 좌측 트리나 검색으로
    // 그 주제를 짚어 왔는데 화면에서는 `기타` 에 삼켜져 있으면, 가리킨 곳이
    // 사라진 셈이다. **주제 통째로** 빼므로 열 합 보존은 그대로다(구조상 불변).
    // 핀 하나 때문에 표시 주제가 maxTopics+1 이 될 수 있다 — 단일 핀이라 수용한다.
    if (pinned?.has(r.domain)) return;
    if (di >= maxTopics || (di >= keepTop && r.total / grand < minShare)) merged.add(di);
  });
  if (merged.size < 2) return none;

  const remap = new Map<number, number>();
  const keep: TotalsDatum[] = [];
  data.forEach((r, di) => {
    if (!merged.has(di)) { remap.set(di, keep.length); keep.push(r); }
  });
  const others: TotalsDatum = {
    domain: OTHERS_KEY, label: "",
    origin: (data[0]?.origin ?? []).map(() => 0),
    state: (data[0]?.state ?? []).map(() => 0),
    total: 0,
  };
  data.forEach((r, di) => {
    if (!merged.has(di)) return;
    r.origin.forEach((v, i) => { others.origin[i] += v; });
    r.state.forEach((v, i) => { others.state[i] += v; });
    others.total += r.total;
  });
  // 기타는 2열 말미 고정 — 크기순 관례 바깥의 "나머지" 자리.
  const othersDi = keep.length;
  const passthru: Lode[] = [];
  const agg = new Map<string, Lode>();
  for (const t of tuples) {
    const nd = remap.get(t.di);
    if (nd !== undefined) { passthru.push({ oi: t.oi, di: nd, si: t.si, n: t.n }); continue; }
    const key = `${t.oi}|${t.si}`;
    const a = agg.get(key) || { oi: t.oi, di: othersDi, si: t.si, n: 0 };
    a.n += t.n;
    agg.set(key, a);
  }
  const bucket = [...agg.values()].sort((a, b) => a.oi - b.oi || a.si - b.si);
  return { data: [...keep, others], tuples: [...passthru, ...bucket],
           mergedTopics: merged.size, mergedDocs: others.total };
}

// ── Stage 2 강등 (per-gap 집계) ─────────────────────────────────────────────

/** 튜플을 갭별 집계 리본으로 접는다 — 왼쪽 (출처×주제), 오른쪽 (주제×상태).
 *  정적 리본 수가 곱이 아니라 합으로 억제되고, 정체성은 hover 조인(튜플 표)이
 *  복원한다. 방출 순서는 모드 B 와 같은 의미 순서 — 그 순서는 갭별로 이미
 *  교차 최적이다(모듈 머리 정리의 따름정리). */
export function aggGaps(tuples: Lode[]): {
  left: { oi: number; di: number; n: number }[];
  right: { di: number; si: number; n: number }[];
} {
  const lm = new Map<string, { oi: number; di: number; n: number }>();
  const rm = new Map<string, { di: number; si: number; n: number }>();
  for (const t of tuples) {
    const lk = `${t.oi}|${t.di}`;
    const l = lm.get(lk) || { oi: t.oi, di: t.di, n: 0 };
    l.n += t.n; lm.set(lk, l);
    const rk = `${t.di}|${t.si}`;
    const r = rm.get(rk) || { di: t.di, si: t.si, n: 0 };
    r.n += t.n; rm.set(rk, r);
  }
  return {
    left: [...lm.values()].sort((a, b) => a.oi - b.oi || a.di - b.di),
    right: [...rm.values()].sort((a, b) => a.di - b.di || a.si - b.si),
  };
}

/** 경로 보존이 감당 안 되는 규모의 판정 — 입력이 전부 데이터의 순수 함수라
 *  리사이즈로 모드가 튀지 않는다. 가닥 수(튜플×2)가 상한을 넘거나, 절대 높이 캡
 *  안에서 전 가닥에 최소 두께를 줄 수 없으면 갭 집계로 강등한다. */
export function renderPlan(tupleCount: number, needRib: number, capH: number,
                           maxBands = 40): { mode: "path" | "agg"; reason: "" | "bands" | "height" } {
  if (tupleCount * 2 > maxBands) return { mode: "agg", reason: "bands" };
  if (needRib > capH) return { mode: "agg", reason: "height" };
  return { mode: "path", reason: "" };
}

/** 점등(spotlight) — "이것만 밝고 나머지는 물러난다"를 만드는 **순수 층**.
 *
 *  두 기능이 같은 것을 필요로 한다: 범례에서 선 갈래를 가리키면 지도가 그 갈래만
 *  남기는 것, 그리고 초점 노드의 **인과 원뿔**(과거·미래 전이폐포)을 밝히는 것.
 *  둘 다 "이름 붙인 부분집합만 남기고 나머지를 물러나게" 하는 한 가지 동작이라
 *  메커니즘도 하나여야 한다 — 두 벌이 되면 둘이 서로를 덮어쓰는 날이 온다.
 *
 *  지금 앱에서 `spotOf`·`causalCone` 을 부르는 곳은 없다(그것을 읽던 2D·3D 렌더러는
 *  걷었다). `edgeKey` 는 레이아웃 선(`view-layout-wire.ts`)이 쓰고, 셋 다
 *  `app/dev/cone-check.ts` 가 지킨다.
 *
 *  ⚠ **import 를 두지 않는다.** 이 파일만 `node --experimental-strip-types` 로 곧장
 *  돌릴 수 있어야 `app/dev/cone-check.ts` 가 브라우저 없이 폐포를 검사한다. 그래서
 *  `DIRECTED_RELS` 를 여기서 읽지 않고 **호출부가 걸러 넘긴다**(view.ts 는 이미 그
 *  상수를 갖고 있다).
 */

/** 지금 밝혀야 하는 것. 노드는 id, 엣지는 :func:`edgeKey`. */
export type Spot = { nodes: Set<string>; edges: Set<string> };

type E = { source: string; target: string; type: string };

/** 관계 하나의 열쇠. 레이아웃 선(`view-layout-wire.ts`)과 허브에 저장된 레이아웃 답이
 *  이 열쇠로 관계를 짝짓는다.
 *
 *  타입까지 넣는 이유: 같은 쌍의 평행 관계가 서로 다른 열쇠를 가져야 한다. 쌍만으로
 *  열쇠를 만들면 여러 선이 같은 열쇠를 갖게 되어 다른 선의 자리를 받는다. */
export const edgeKey = (e: E): string => `${e.source}\u0000${e.target}\u0000${e.type}`;

/** 화살표가 **시간을 거스르는** 관계. `DERIVES_FROM(a→b)` 은 "a 가 b 에서 나왔다"라
 *  b 가 상류다.
 *
 *  ⚠ 이 표는 사이드카의 `api/query.py _REVERSED_RELS` 와 **같아야 한다.** 원뿔이
 *  거기서 어긋나면 `rank` 배치가 왼쪽에 세운 것을 원뿔은 미래라고 부르게 되고,
 *  화면이 자기를 반박한다. 그래서 여기서 "옳은 의미"를 새로 정하지 않고 그리는
 *  쪽을 따라간다.
 *
 *  ⚠ 2026-08-24 에 `INHERITS_FROM` 이 여기와 서버 양쪽에 함께 들어왔다. 그 전까지
 *  서버 표에는 `DERIVES_FROM` 만 있었고(2026-08-02 의 방향 정합 수정이 그것만
 *  다뤘다), 그래서 rank 배치가 상속 사슬만 거꾸로 세우고 있었다. 관례는 둘을 같은
 *  모양으로 규정한다: "a 가 b 를 상속한다" = b 가 위.
 *
 *  ⚠ 두 곳은 **함께** 움직인다 — 한쪽만 고치면 하이라이트가 배치를 반박한다. */
const UPSTREAM_REVERSED: readonly string[] = ["DERIVES_FROM", "INHERITS_FROM"];

/** 고른 선들 → 점등 집합. 노드는 그 선들의 양 끝이고, `seed` 는 있으면 함께 밝힌다
 *  (원뿔의 씨앗은 자기 자신에게 닿는 선이 없어도 밝아야 한다). */
export function spotOf(sel: E[], seed?: string): Spot {
  const nodes = new Set<string>();
  const edges = new Set<string>();
  if (seed) nodes.add(seed);
  for (const e of sel) {
    nodes.add(e.source);
    nodes.add(e.target);
    edges.add(edgeKey(e));
  }
  return { nodes, edges };
}

/** 씨앗의 인과 원뿔에 놓인 선들. `dir="past"` 는 상류 쪽 전이폐포(무엇이 이것을
 *  있게 했나), `"future"` 는 하류 쪽(이것이 무엇을 낳았나).
 *
 *  `es` 에는 **방향 관계만** 넘긴다 — 이 함수는 무엇이 방향 관계인지 모르는 것이
 *  의도다(파일 머리의 import 0 규율). 무방향 관계를 섞어 넣으면 그것도 방향으로
 *  취급되고, 그건 호출부의 잘못이지 여기의 잘못이 아니다.
 *
 *  순환은 그래프에 실재한다("시간을 거스르는 기록") — `seen` 가드가 종료를 보장하고,
 *  각 노드는 한 번만 펼쳐지므로 같은 선이 두 번 실리지 않는다. */
export function causalCone(es: E[], seed: string, dir: "past" | "future"): E[] {
  const step = new Map<string, { e: E; to: string }[]>();
  for (const e of es) {
    const rev = UPSTREAM_REVERSED.includes(e.type);
    const up = rev ? e.target : e.source;
    const down = rev ? e.source : e.target;
    const from = dir === "past" ? down : up;
    const to = dir === "past" ? up : down;
    const bucket = step.get(from);
    if (bucket) bucket.push({ e, to });
    else step.set(from, [{ e, to }]);
  }
  const seen = new Set<string>([seed]);
  const queue = [seed];
  const out: E[] = [];
  for (let i = 0; i < queue.length; i++) {
    for (const hop of step.get(queue[i]) || []) {
      out.push(hop.e);
      if (!seen.has(hop.to)) {
        seen.add(hop.to);
        queue.push(hop.to);
      }
    }
  }
  return out;
}

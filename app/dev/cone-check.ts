/** 인과 폐포 자체 검사 — 브라우저도 프레임워크도 없이 돈다.
 *
 *     node --experimental-strip-types app/dev/cone-check.ts
 *
 *  `app/dev/` 는 tsconfig 의 `include` 밖이라(앱 셸의 일부가 아니다, marks.ts 와 같은
 *  지위) 여기서만 `.ts` 확장자를 붙여 import 한다 — `src/` 안에서 그러면
 *  `npm run typecheck` 가 거부한다(allowImportingTsExtensions 미설정).
 *  `spotlight.ts` 가 import 0 인 것이 이 검사가 성립하는 조건이다. */
import assert from "node:assert/strict";
import { causalCone, edgeKey, spotOf } from "../src/spotlight.ts";

const P = "PREREQUISITE_OF";     // a→b = a 가 먼저(정방향)
const D = "DERIVES_FROM";        // a→b = b 가 먼저(역방향)
const ids = (es: { source: string; target: string }[]) =>
  [...new Set(es.flatMap((e) => [e.source, e.target]))].sort().join(",");

// ── 사슬 a → b → c → d (전부 정방향) ────────────────────────────────────────
const chain = [
  { source: "a", target: "b", type: P },
  { source: "b", target: "c", type: P },
  { source: "c", target: "d", type: P },
];
assert.equal(ids(causalCone(chain, "c", "past")), "a,b,c",
             "과거 원뿔은 씨앗까지 거슬러 전부 담는다(전이적이다)");
assert.equal(ids(causalCone(chain, "c", "future")), "c,d",
             "미래 원뿔은 하류만");
assert.equal(causalCone(chain, "a", "past").length, 0,
             "맨 위의 과거는 비어 있다");

// ── 역방향 관계는 화살표와 반대로 읽는다 ────────────────────────────────────
// x --DERIVES_FROM--> y 는 "x 가 y 에서 나왔다" = y 가 상류.
const rev = [{ source: "x", target: "y", type: D }];
assert.equal(ids(causalCone(rev, "x", "past")), "x,y",
             "파생의 상류는 화살표가 가리키는 쪽이다");
assert.equal(causalCone(rev, "x", "future").length, 0);
assert.equal(ids(causalCone(rev, "y", "future")), "x,y",
             "그 반대편에서 보면 미래다");

// ── 순환은 멈춰야 한다(그래프에 실재한다) ───────────────────────────────────
const cycle = [
  { source: "p", target: "q", type: P },
  { source: "q", target: "r", type: P },
  { source: "r", target: "p", type: P },
];
assert.equal(causalCone(cycle, "p", "future").length, 3,
             "순환에서도 각 선은 한 번만 실린다");
assert.equal(causalCone([{ source: "s", target: "s", type: P }], "s", "future").length, 1,
             "자기연결도 한 번만");

// ── 점등 집합 ───────────────────────────────────────────────────────────────
const spot = spotOf(chain.slice(0, 1), "z");
assert.deepEqual([...spot.nodes].sort(), ["a", "b", "z"], "씨앗은 선이 없어도 밝는다");
assert.deepEqual([...spot.edges], [edgeKey(chain[0])]);
// 열쇠는 타입까지 가른다 — 2D 가 평행 엣지를 접기 때문에 쌍만으로는 부족하다.
assert.notEqual(edgeKey({ source: "a", target: "b", type: P }),
                edgeKey({ source: "a", target: "b", type: D }));

console.log("cone-check: ok");

/** View 주소 문법 자체 검사 — 브라우저도 프레임워크도 없이 돈다.
 *
 *     node --experimental-strip-types app/dev/view-url-check.ts
 *
 *  `app/dev/` 는 tsconfig 의 `include` 밖이라(앱 셸의 일부가 아니다) 여기서만 `.ts`
 *  확장자를 붙여 import 한다 — `src/` 안에서 그러면 `npm run typecheck` 가 거부한다.
 *  `view-url.ts` 가 DOM 을 안 쓰는 것이 이 검사가 성립하는 조건이고, 그래서
 *  `absoluteViewUrl`(location 을 읽는다)만 이 파일에서 부르지 않는다.
 *
 *  ⚠ **왜 이게 있어야 하나**: 이 문법에는 거울이 둘이다 — 여기가 만들고
 *  `vinc_mcp/viewurl.py` 가 읽는다. 파이썬 쪽은 `tests/test_view_url.py` 가 지키는데
 *  앱 쪽에는 JS 테스트 러너가 없어서(`app/package.json` 에 `test` 없음) 지키는 것이
 *  아무것도 없었다. 아래 EXPECT 는 파이썬 `GRAMMAR` 와 **같은 형태**를 적은 것이고,
 *  둘이 갈라지면 여기서 먼저 소리가 난다. */
import assert from "node:assert/strict";
import { formatViewUrl, parseViewUrl, KEYWORDS } from "../src/view-url.ts";
import type { ViewSelection } from "../src/view-url.ts";

let n = 0;
const ok = (label: string) => { n += 1; console.log("  ok", label); };

// ── 1. 포맷 → 파이썬이 읽는 형태와 같은가 ────────────────────────────────────
const CASES: Array<[ViewSelection, { depth?: number; viewpoint?: string }, string]> = [
  [{ kind: "all" }, {}, "#view"],
  [{ kind: "domains", domains: ["vinc/engineering"] }, {}, "#view/vinc%2Fengineering"],
  [{ kind: "domains", domains: ["a", "b"] }, { depth: 3 }, "#view/a,b/d3"],
  [{ kind: "node", id: "concept:x" }, { depth: 2 }, "#view/trace/concept%3Ax/d2"],
  [{ kind: "ids", ids: ["a", "b"] }, {}, "#view/ids/a,b"],
  [{ kind: "doc", id: "d4ab94561078498e3" }, {}, "#view/doc/d4ab94561078498e3"],
  [{ kind: "node", id: "concept:x" }, { depth: 2, viewpoint: "timeline" },
   "#view/trace/concept%3Ax/d2/as/timeline"],
];
for (const [sel, opts, want] of CASES) {
  assert.equal(formatViewUrl(sel, opts), want, `format ${want}`);
}
ok(`포맷 ${CASES.length}종이 기대한 문자열과 같다`);

// ── 2. 왕복 — 만든 것을 다시 읽으면 같은 선택이 나오는가 ──────────────────────
for (const [sel, opts] of CASES) {
  const back = parseViewUrl(formatViewUrl(sel, opts));
  assert.equal(back.sel.kind, sel.kind, `roundtrip kind ${sel.kind}`);
  assert.equal(back.depth, opts.depth ?? 0, `roundtrip depth ${sel.kind}`);
  if (sel.kind === "node") assert.equal((back.sel as any).id, sel.id);
  if (sel.kind === "doc") assert.equal((back.sel as any).id, sel.id);
  if (sel.kind === "ids") assert.deepEqual((back.sel as any).ids, sel.ids);
  if (sel.kind === "domains") assert.deepEqual((back.sel as any).domains, sel.domains);
}
ok("왕복이 선택을 보존한다");

// ── 3. 관점은 늘 맨 뒤이고, 깊이는 그 앞이다 ─────────────────────────────────
// 순서가 뒤집히면 파서가 관점을 떼어 낼 때 깊이까지 함께 잘려 나간다.
{
  const u = formatViewUrl({ kind: "node", id: "n" }, { depth: 4, viewpoint: "2d" });
  assert.ok(u.indexOf("/d4") < u.indexOf("/as/"), "깊이가 관점보다 앞이어야 한다");
  const back = parseViewUrl(u);
  assert.equal(back.depth, 4);
  assert.equal(back.viewpoint, "2d");
  ok("깊이가 관점보다 앞이고 둘 다 살아 돌아온다");
}

// ── 4. 기존 딥링크가 전부 그대로 읽힌다 ──────────────────────────────────────
// 꼬리(`/d<n>`)를 문법에 더한 것이 옛 주소를 깨지 않는다는 것이 이 항목의 요점이다.
for (const [old, kind] of [
  ["#view", "all"], ["#view/vinc/design", "domains"],
  ["#view/trace/concept:a", "node"], ["#view/vinc/design/as/2d", "domains"],
  ["#view/trace/concept:a/as/timeline", "node"],
] as const) {
  assert.equal(parseViewUrl(old).sel.kind, kind, `legacy ${old}`);
}
ok("기존 딥링크 5종이 그대로 읽힌다");

// ── 5. 예약어를 빠뜨리면 주제 이름이 된다 ────────────────────────────────────
// 이것이 이 문법의 조용한 함정이다: 등록 안 된 키워드는 오류가 아니라 **빈 주제**가
// 되어 화면만 비고 아무 신호도 없다.
for (const kw of KEYWORDS) {
  if (kw === "as") continue;
  const got = parseViewUrl(`#view/${kw}/x`).sel.kind;
  assert.notEqual(got, "domains", `${kw} 가 주제로 읽히면 안 된다`);
}
ok(`예약어 ${KEYWORDS.length - 1}종이 주제로 읽히지 않는다`);

// ── 6. 슬래시가 든 주제가 쪼개지지 않는다 ────────────────────────────────────
{
  const back = parseViewUrl("#view/vinc/engineering,vinc/design");
  assert.deepEqual((back.sel as any).domains, ["vinc/engineering", "vinc/design"]);
  ok("슬래시가 든 주제 둘이 온전하다");
}

// ── 7. 읽을 수 없는 것은 전체 지도로 되돌린다(던지지 않는다) ─────────────────
for (const junk of ["", "https://vincs.io/", "#search/x", "쓰레기"]) {
  assert.equal(parseViewUrl(junk).sel.kind, "all", `junk ${junk}`);
}
ok("못 읽는 입력은 조용히 전체 지도다");

console.log(`\nview-url: ${n}개 검사 통과`);

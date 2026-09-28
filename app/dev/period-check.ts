/** 타임라인 기간의 셈 — 브라우저 없이 돈다.
 *
 *     node --experimental-strip-types app/dev/period-check.ts
 *
 *  기간은 노드 시각과 **같은 문자열 키 공간**에서 비교한다(`start <= ts < end`, 사전식).
 *  날짜를 파싱하지 않는 것이 이 셈의 계약이라(시간대·로캘), 경계가 어긋나면 화면은 오류
 *  없이 하루씩 빠지거나 더 들어간다. 그 경계를 여기서 직접 돌려 본다.
 *  근거: [[vinc.plan.view-card-timeline-period-authoring-2026-09-27]] §2.5 */
import assert from "node:assert/strict";
import { inPeriod, nextTimeKey, periodRange, TIME_KEY } from "../src/view-prefs.ts";

let n = 0;
const ok = (label: string) => { n += 1; console.log("  ok", label); };
const custom = (a: string, b: string) => ({ period: "custom" as const, periodFrom: a, periodTo: b });
const now = new Date(Date.UTC(2026, 8, 27, 15, 0, 0));   // 2026-09-27 15:00Z

// ── 1. 다음 칸 — 알갱이를 지킨다 ─────────────────────────────────────────────
assert.equal(nextTimeKey("2026"), "2027");
assert.equal(nextTimeKey("2026-12"), "2027-01");
assert.equal(nextTimeKey("2026-09"), "2026-10");
assert.equal(nextTimeKey("2026-02-28"), "2026-03-01");
assert.equal(nextTimeKey("2024-02-28"), "2024-02-29");
assert.equal(nextTimeKey("2026-12-31"), "2027-01-01");
ok("다음 칸은 같은 알갱이로 넘어간다(해 · 달 · 윤일 · 해넘이)");

// ── 2. 프리셋은 오늘에서 푼다(UTC) ──────────────────────────────────────────
assert.equal(periodRange({ period: "all", periodFrom: "", periodTo: "" }, now), null);
assert.deepEqual(periodRange({ period: "7d", periodFrom: "", periodTo: "" }, now),
  { start: "2026-09-21", end: "2026-09-28" });
assert.deepEqual(periodRange({ period: "30d", periodFrom: "", periodTo: "" }, now),
  { start: "2026-08-29", end: "2026-09-28" });
assert.deepEqual(periodRange({ period: "year", periodFrom: "", periodTo: "" }, now),
  { start: "2026", end: "2027" });
ok("최근 7일은 오늘을 포함한 이레, 올해는 해 한 칸");

// ── 3. 경계 — 기록 시각(ISO, UTC)과 같은 키 공간 ─────────────────────────────
const r7 = periodRange({ period: "7d", periodFrom: "", periodTo: "" }, now)!;
assert.equal(inPeriod("2026-09-21T00:00:00.000Z", r7), true);
assert.equal(inPeriod("2026-09-20T23:59:59.999Z", r7), false);
assert.equal(inPeriod("2026-09-27T23:59:59.999Z", r7), true);
assert.equal(inPeriod("2026-09-28T00:00:00.000Z", r7), false);
assert.equal(inPeriod("", r7), false);
assert.equal(inPeriod("", null), true);
ok("시작은 포함 · 끝은 제외 · 시각 없는 노드는 기간에 안 든다(전체면 거르지 않는다)");

// ── 4. 직접 설정 — 연·월·일 무엇으로 적어도 양 끝을 포함한다 ───────────────────
assert.deepEqual(periodRange(custom("2025-03", "2025-06")), { start: "2025-03", end: "2025-07" });
assert.deepEqual(periodRange(custom("2025", "2025")), { start: "2025", end: "2026" });
assert.deepEqual(periodRange(custom("2025-06", "2025-03")), { start: "2025-03", end: "2025-07" });
const q2 = periodRange(custom("2025-03", "2025-06"))!;
assert.equal(inPeriod("2025-06-30T23:00:00.000Z", q2), true);
assert.equal(inPeriod("2025-07-01T00:00:00.000Z", q2), false);
assert.equal(inPeriod("2025-03", q2), true);          // 내용 시점(달 알갱이)
assert.equal(inPeriod("2025", q2), false);            // 해만 적힌 시점은 달 범위에 못 든다
ok("거꾸로 적어도 뒤집어 받고, 끝 달의 마지막 날까지 든다");

// ── 5. 망가진 값은 거르지 않는다(화면을 조용히 비우지 않는다) ───────────────────
assert.equal(periodRange(custom("2025-13", "2025-06")), null);
assert.equal(periodRange(custom("", "")), null);
for (const k of ["2025", "2025-01", "2025-12-31"]) assert.equal(TIME_KEY.test(k), true, k);
for (const k of ["25", "2025-1", "2025-00", "2025-01-32", "2025/01"]) assert.equal(TIME_KEY.test(k), false, k);
ok("형식이 틀린 직접 설정은 전체로 읽는다");

console.log(`period-check: ${n} passed`);

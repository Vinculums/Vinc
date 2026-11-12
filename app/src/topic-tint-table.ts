import { tintPlan } from "./view-tint";
import type { ThemeMode } from "./theme";

/** 주제 색 표 하나. 앱이 아는 주제 전부(문서의 주제 · 그래프 노드의 주제 · 역할 주제)를 한 집합으로 모아 `tintPlan` 을 한 번 돌린다.
 *  문서 트리 · 홈 목록 · 나침반(층 보기와 전체 보기) · 사이드바 그래프 트리가 모두 이 표를 읽으므로 같은 주제는 어느 표면에서나
 *  같은 색이다. 표를 짓는 곳은 여기 하나다: 표면마다 따로 지으면 넣는 주제 집합이 달라 한 주제가 두 색이 된다.
 *
 *  표는 스페이스마다 하나다. 다른 스페이스 키로 주제가 들어오면 먼저 비운다(한 스페이스의 주제가 다른 스페이스의 순위에 섞이지 않게).
 *  한 스페이스 안에서는 늘기만 한다: 좁힌 그래프 읽기가 표를 줄여 다른 표면의 색을 흔들지 않게 한다. 새 주제가 들어오면
 *  `tintPlan` 이 정렬 순위로 다시 칠하므로 이미 칠한 주제의 색도 바뀔 수 있다. 그래서 표가 바뀔 때마다 `TOPIC_TINTS_EVENT` 를 낸다. */

/** 표가 바뀌었다(주제가 늘었거나 스페이스가 바뀌었다). 홈 목록 · 사이드바 · 나침반이 듣고 다시 칠한다. 이름은 듣는 쪽과 맞춘다. */
export const TOPIC_TINTS_EVENT = "vinc:lnb-tints";

type Plan = ReturnType<typeof tintPlan>;
let space: string | null = null;
const known = new Set<string>();
const plans = new Map<ThemeMode, Plan>();
let root = "";

const segsOf = (d: string | undefined): string[] => String(d || "").trim().split("/").filter(Boolean);

/** 뿌리 규칙: 최상위 주제가 하나뿐이면 그 주제가 뿌리이고, 둘 이상이거나 없으면 이름 없는 가상 뿌리("")다.
 *  ⚠ 최상위 이름을 어디에도 박지 말 것. 나침반의 지도 뿌리와 색 표의 뿌리가 이 함수 하나를 쓴다. */
export function rootTopicOf(domains: Iterable<string | undefined>): string {
  const tops = new Set<string>();
  for (const d of domains) { const s = segsOf(d); if (s.length) tops.add(s[0]); }
  return tops.size === 1 ? [...tops][0] : "";
}

/** 이 스페이스에서 본 주제를 표에 더한다. 조상 경로까지 넣는다(트리와 나침반은 조상 주제도 칠한다). 표가 바뀌면 알리고 true 를 돌려준다. */
export function addTopics(spaceKey: string, domains: Iterable<string | undefined>): boolean {
  let changed = false;
  if (space !== spaceKey) { changed = known.size > 0; space = spaceKey; known.clear(); }
  for (const d of domains) {
    const s = segsOf(d);
    for (let i = 1; i <= s.length; i++) { const p = s.slice(0, i).join("/"); if (!known.has(p)) { known.add(p); changed = true; } }
  }
  if (!changed) return false;
  plans.clear();
  root = rootTopicOf(known);
  globalThis.dispatchEvent?.(new Event(TOPIC_TINTS_EVENT));
  return true;
}

/** 지금 모드의 색 한 벌(주제 색 · 영역 색 · 주제의 영역). 모드마다 한 번 짓고 표가 바뀌면 다시 짓는다. */
export function topicTintPlan(mode: ThemeMode): Plan {
  let p = plans.get(mode);
  if (!p) { p = tintPlan([...known], mode); plans.set(mode, p); }
  return p;
}

/** 주제 하나의 색: 표에서 가장 가까운 조상의 색. 뿌리는 어느 영역도 아니라 색이 없다. 빈 주제 · 뿌리 · 표에 없는 주제는 ""(중립 잉크). */
export function topicTint(domain: string | undefined, mode: ThemeMode): string {
  const s = segsOf(domain);
  if (!s.length || s.join("/") === root) return "";
  const t = topicTintPlan(mode).topics;
  for (let i = s.length; i >= 1; i--) { const c = t.get(s.slice(0, i).join("/")); if (c) return c; }
  return "";
}

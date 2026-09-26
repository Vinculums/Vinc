/** 좌측 문서 트리의 펼침 규칙.
 *
 *  ⚠ 이 파일은 import 0 이어야 한다 — `app/dev/lnb-tree-check.ts` 가 브라우저 없이
 *  plain node 로 이 규칙을 실제로 돌린다.
 *
 *  기본은 **3단계까지 보이게** 편다: 1·2단계 주제 가운데 하위 주제가 있는 것만 연다
 *  (`vinc` ▾ → `design` ▾ → `surfaces` ▸). 앱을 켤 때마다 이 기본에서 시작하고,
 *  세션 안에서 사용자가 접은 것은 다시 펴지 않는다.
 *  근거: [[decision:the-document-tree-opens-three-levels-and-a-row-click-only-folds-2026-09-26]] */

export const DEFAULT_OPEN_DEPTH = 2;

export type PathNode = { path: string; kids: PathNode[] };

/** 기본으로 펼칠 주제 경로. 하위 주제가 없는 줄은 펴면 곧장 문서·노드가 쏟아지므로
 *  기본에서 뺀다(문서·노드는 눌러야 보인다). */
export function defaultOpenPaths(tops: PathNode[], depth = DEFAULT_OPEN_DEPTH): string[] {
  const out: string[] = [];
  const walk = (n: PathNode, level: number) => {
    if (level > depth || n.kids.length === 0) return;
    out.push(n.path);
    for (const k of n.kids) walk(k, level + 1);
  };
  for (const t of tops) walk(t, 1);
  return out;
}

/** 이번에 처음 보는 기본 경로만 펼침에 더한다. 이미 본 경로는 사용자가 접었을 수
 *  있으므로 건드리지 않는다. 두 집합을 그 자리에서 고치고, 더한 수를 돌려준다. */
export function applyDefaults(tops: PathNode[], expanded: Set<string>, seen: Set<string>): number {
  let added = 0;
  for (const p of defaultOpenPaths(tops)) {
    if (seen.has(p)) continue;
    seen.add(p);
    if (!expanded.has(p)) { expanded.add(p); added++; }
  }
  return added;
}

/** 주제 줄의 자기 항목(노드·문서)을 한 줄로 접어 둘지. 하위 주제가 있는 줄만 접는다:
 *  하위 주제와 항목이 한 목록에 섞이면 구조가 항목 더미에 묻힌다. */
export const foldsOwnRows = (n: { kids: unknown[] }): boolean => n.kids.length > 0;

/** 접은 항목 묶음의 펼침 키. 주제 경로와 같은 집합에 두되 겹치지 않게 접두를 붙인다. */
export const ownKey = (path: string): string => `own:${path}`;

/** 노드가 지금도 맞는가 — 3D 몸통의 historical 상태가 읽는다.
 *
 *  그래프 읽기는 이미 두 벌의 유효구간을 싣는다: 기록은 열(`valid_until`), 결정은 속성
 *  (`props.valid_until` · `props.superseded_by`). 닫힌 구간이거나 후속이 있으면 지난 것이다.
 *  날짜는 사전식 비교다(YYYY · YYYY-MM · YYYY-MM-DD 가 앞자리부터 같은 순서로 정렬된다).
 *  반열린 구간이라 `valid_until` 이 오늘이면 이미 지났다. */
export type Validity = { valid_until?: string; props?: Record<string, unknown> | null };

export function isHistorical(n: Validity, today: string): boolean {
  const until = n.valid_until || (typeof n.props?.valid_until === "string" ? n.props.valid_until : "");
  if (until && until <= today) return true;
  const next = n.props?.superseded_by;
  return typeof next === "string" && next.trim() !== "";
}

/** 서로 어긋나는 주장 — 같은 열쇠(`props.entity`·`predicate`·`scope`)에 다른 값(`props.value`)을
 *  단 둘이 유효구간이 겹치면 둘 다 충돌이다. 서버의 `store/search_temporal.annotate_claims` 와 같은
 *  규칙이다: 제목이나 주제가 비슷한 것은 같은 주장이 아니고, 열쇠가 명시돼 있어야 한다. 이미 지난
 *  주장(`isHistorical`)은 빼고 잰다. SUPERSEDES 관계만 있고 속성에 후속이 없는 대체는 여기서 못
 *  보므로 그런 짝은 충돌로 남을 수 있다(서버는 관계로 은퇴시킨다). */
export type Claim = Validity & { id: string; kind?: string; valid_from?: string };

export function conflictIds(nodes: Claim[], today: string): Set<string> {
  const key = (n: Claim): string | null => {
    const p = n.props || {};
    if (!p.entity || !p.predicate || p.scope === undefined || p.scope === null) return null;
    return JSON.stringify([n.kind || n.id.split(":")[0], p.entity, p.predicate, p.scope]);
  };
  const span = (n: Claim): [string, string] => {
    const p = n.props || {};
    const from = n.valid_from || (typeof p.valid_from === "string" ? p.valid_from : "") || "";
    const until = n.valid_until || (typeof p.valid_until === "string" ? p.valid_until : "") || "";
    return [from || "0000", until || "9999"];
  };
  const groups = new Map<string, Claim[]>();
  for (const n of nodes) {
    const k = key(n);
    if (!k || isHistorical(n, today) || !("value" in (n.props || {}))) continue;
    const g = groups.get(k);
    if (g) g.push(n); else groups.set(k, [n]);
  }
  const out = new Set<string>();
  for (const g of groups.values()) {
    for (let i = 0; i < g.length; i++) for (let j = i + 1; j < g.length; j++) {
      const a = g[i], b = g[j];
      if (JSON.stringify(a.props!.value) === JSON.stringify(b.props!.value)) continue;
      const [af, au] = span(a), [bf, bu] = span(b);
      if (af < bu && bf < au) { out.add(a.id); out.add(b.id); }
    }
  }
  return out;
}

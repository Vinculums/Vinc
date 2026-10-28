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

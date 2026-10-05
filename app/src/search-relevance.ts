/** 서버가 보정해서 표시한 약한 후보만 별도로 둔다. RRF/거리 문턱은 화면에서 추측하지 않는다. */
export function partitionSearchResults<T extends { relevance?: { level?: string } }>(rows: T[]): {
  primary: T[]; weak: T[];
} {
  const primary: T[] = [], weak: T[] = [];
  for (const row of rows) (row.relevance?.level === "weak" ? weak : primary).push(row);
  return { primary, weak };
}

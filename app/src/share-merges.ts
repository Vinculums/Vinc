/** 공유 전 목적지의 같은 id를 읽는다. 조회 실패를 '없는 항목'으로 바꾸지 않는다. */
export async function shareMerges(
  nodes: { id: string; title?: string }[],
  read: (id: string) => Promise<{ executed: boolean; title?: string; note?: string }>,
): Promise<{ id: string; title: string }[]> {
  const unique = [...new Map(nodes.map((n) => [n.id, n])).values()];
  const matches: { id: string; title: string }[] = [];
  // 큰 팩도 동시에 목적지를 압박하지 않는다. 화면에는 팩의 원래 순서로 보여 준다.
  for (let i = 0; i < unique.length; i += 4) {
    const rows = await Promise.all(unique.slice(i, i + 4).map(async (n) => {
      const r = await read(n.id);
      if (r.executed === true) return { id: n.id, title: r.title || n.title || n.id };
      if (r.executed === false && r.note === `unknown node: ${n.id}`) return null;
      throw new Error("destination inspection failed");
    }));
    matches.push(...rows.filter((r) => r !== null));
  }
  return matches;
}

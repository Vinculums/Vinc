/** 팩을 허브 본문 상한 아래 조각으로 나눈다.
 *
 *  ⚠ 이 파일은 import 0 이어야 한다 — `app/dev/packchunks-check.ts` 가 브라우저 없이
 *  노드로 직접 돈다.
 *
 *  **순서가 계약이다: 노드 전부 → 엣지 → 문서.** 엣지는 양 끝 노드가 이미 저장돼 있어야
 *  붙고, 문서의 MENTIONS 는 그 순간 있는 개념에만 걸린다. 같은 문서를 다시 보내면 내용이
 *  같다고 건너뛰므로(`api/share.py` `import_share_pack`) 연결을 나중에 채울 기회도 없다.
 *  주제별로 나누면 주제를 건너는 엣지와 여러 주제에 걸친 문서가 조용히 빠지는 이유가 이것이다.
 *  근거: [[decision:space-transfer-sends-size-bounded-chunks-nodes-then-edges-then-documents]] */

/** 허브는 4MB 넘는 본문을 받지 않는다(`vinc_mcp/http_server.py` `MAX_BODY`). 매니페스트와
 *  감싸는 키, 계정 프록시를 지나며 붙는 몫을 남기고 3MB 에서 자른다. */
export const CHUNK_BYTES = 3 * 1024 * 1024;

export type Stage = "nodes" | "edges" | "documents";
export type StageCounts = Record<Stage, number>;
export type Chunk = { stage: Stage; count: number; pack: Record<string, unknown> };

const enc = new TextEncoder();
const bytesOf = (x: unknown) => enc.encode(JSON.stringify(x)).length;

/** 혼자서 상한을 넘는 항목은 싣지 않고 `tooBig` 으로 센다 — 보내 봐야 허브가 거절하고,
 *  그 조각 하나 때문에 뒤의 조각이 전부 멈춘다. */
export function chunkPack(pack: any, limit = CHUNK_BYTES): { chunks: Chunk[]; tooBig: number } {
  const head = { vinc_pack: pack?.vinc_pack, manifest: pack?.manifest };
  const chunks: Chunk[] = [];
  let tooBig = 0;
  const cut = (stage: Stage, items: unknown[],
               wrap: (xs: unknown[]) => Record<string, unknown>) => {
    let cur: unknown[] = [];
    let size = 0;
    const flush = () => {
      if (cur.length) chunks.push({ stage, count: cur.length, pack: { ...head, ...wrap(cur) } });
      cur = [];
      size = 0;
    };
    for (const it of items) {
      const n = bytesOf(it) + 1;   // 배열 구분자 몫
      if (n > limit) { tooBig++; continue; }
      if (size + n > limit) flush();
      cur.push(it);
      size += n;
    }
    flush();
  };
  const f = pack?.fragment || {};
  cut("nodes", f.nodes || [], (xs) => ({ fragment: { nodes: xs, edges: [] } }));
  cut("edges", f.edges || [], (xs) => ({ fragment: { nodes: [], edges: xs } }));
  cut("documents", pack?.documents || [],
      (xs) => ({ fragment: { nodes: [], edges: [] }, documents: xs }));
  return { chunks, tooBig };
}

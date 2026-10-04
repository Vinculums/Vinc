/** 실제 공유 시트의 합성 목적지. 계정 호출·쓰기 없이 확인·취소·결과를 검증한다. */
import "../src/app.css";
import { api, cfg } from "../src/api";
import { openShareSheet } from "../src/sheets";
const opts = new URLSearchParams(location.search);
const nodes = [{ id: "concept:example", type: "concept", title: "Incoming concept", domain: "example" },
  ...[1, 2, 3].map((n) => ({ id: `record:example-${n}`, type: "record", title: `Record ${n}`, domain: "example" }))];
Object.defineProperty(cfg, "team", { get: () => "synthetic-team" });
document.body.dataset.imports = "0";
document.body.dataset.reads = "0";
api.share = async () => ({ executed: true, vinc_pack: 1, kind: "share", fragment: { nodes, edges: [] },
  documents: [], manifest: { scope: {}, counts: { concepts: 1, records: 3, decisions: 0, edges: 0, documents: 0 } } });
api.nodeContext = async (_owner, id, _limit, scope) => {
  if (scope?.team !== null) throw new Error("wrong destination");
  const reads = Number(document.body.dataset.reads) + 1;
  document.body.dataset.reads = String(reads);
  if (opts.has("failure") && reads === 1) throw new Error("synthetic offline");
  return id === "concept:example" && !opts.has("new") ? { executed: true, title: "Existing <concept>" }
    : { executed: false, note: `unknown node: ${id}` };
};
api.importPack = async (_pack, _owner, scope) => {
  if (scope?.team !== null) throw new Error("wrong destination");
  document.body.dataset.imports = String(Number(document.body.dataset.imports) + 1);
  return { executed: true, merged: { nodes: { created: opts.has("new") ? 4 : 3, matched: opts.has("new") ? 0 : 1 } }, documents: { applied: 0 } };
};
await openShareSheet({ domain: "example" }, opts.has("direct")
  ? { from: { team: "synthetic-team" }, into: { team: "" } } : {});

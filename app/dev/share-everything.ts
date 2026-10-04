/** 실제 공유 시트를 로컬 합성 데이터로 띄운다. 실제 계정으로 쓰지 않는다. */
import "../src/app.css";
import { api, cfg } from "../src/api";
import { openShareSheet } from "../src/sheets";

const topicOnly = new URLSearchParams(location.search).has("topic");
const documentsOnly = new URLSearchParams(location.search).has("documents");
const nodes = documentsOnly ? [] : [{ id: "concept:linked", type: "concept", title: "Linked concept", domain: "example" }];
const documents = [{ doc_id: "linked", title: "Linked document", text: "Linked text." },
  { doc_id: "imported", title: "Imported without a topic", text: "Imported text." }];
cfg.team = "qa-source";
api.share = async () => ({
  executed: true, vinc_pack: 1, kind: "share",
  fragment: { nodes, edges: [] },
  documents: documentsOnly ? documents.slice(1) : topicOnly ? documents.slice(0, 1) : documents,
  doc_refs: documentsOnly ? { imported: [] } : topicOnly ? { linked: ["concept:linked"] } : { linked: ["concept:linked"], imported: [] },
  manifest: { scope: {}, counts: { concepts: nodes.length, records: 0, decisions: 0, edges: 0, documents: topicOnly || documentsOnly ? 1 : 2 } },
});
await openShareSheet(topicOnly ? { domain: "example" } : {});

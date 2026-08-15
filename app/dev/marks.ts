/** Mark-spec visual fixture — one node per primitive (D20–D22), edge grammar,
 *  and D31 prefs applied. Not part of the app shell; open at /dev/marks.html
 *  with `npm run dev` for design QA of the 3D mark spec. */
import { mount3d, type L3, type N3 } from "../src/surfaces/view3d";

const nodes: N3[] = [
  { id: "d", label: "Domain — math", kind: "domain", records: 9, x: -0.5, y: 0, z: 0 },
  { id: "c", label: "Concept — Fourier", kind: "concept", degree: 30, x: -0.22, y: 0, z: 0 },
  { id: "b", label: "Bridge — wave⇄price", kind: "bridge", degree: 20, x: 0.02, y: 0, z: 0 },
  { id: "e", label: "Episode — session", kind: "episode", degree: 20, x: 0.26, y: 0, z: 0 },
  { id: "r", label: "Record — source.md", kind: "record", degree: 12, x: 0.5, y: 0, z: 0 },
  { id: "g", label: "physics › gateway", kind: "concept", degree: 14, gateway: true, domain: "physics", x: 0.02, y: 0.26, z: -0.08 },
  { id: "f", label: "focus node", kind: "concept", degree: 14, focus: true, x: 0.02, y: -0.26, z: 0.08 },
];
const links: L3[] = [
  { source: "d", target: "c", type: "RELATED_TO", weight: 2 },
  { source: "c", target: "b", type: "PREREQUISITE_OF" },      // 화살촉
  { source: "b", target: "e", type: "RELATED_TO", cross: true }, // 크로스도메인 세선
  { source: "e", target: "r", type: "DERIVES_FROM" },          // 화살촉
  { source: "g", target: "b", type: "RELATED_TO", cross: true },
  { source: "f", target: "b", type: "RELATED_TO" },
];

mount3d(document.getElementById("stage")!, nodes, links, {});

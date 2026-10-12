import http from "node:http";
import { timingSafeEqual } from "node:crypto";
import { fileURLToPath } from "node:url";
import ELK from "elkjs/lib/elk.bundled.js";
import { AvoidLib } from "libavoid-js";
import { routeAcrossTopics } from "../src/view-layout-avoid.ts";
import { createLayoutService, validRequest } from "../src/layout-service.ts";

/** The `vinc-layout` container: POST /v1/layout with the hub's token, GET /health.
 * Internal ingress only. The hub is its one caller. */
const token = process.env.VINC_LAYOUT_TOKEN || "";
const port = Number(process.env.PORT || 8080);
const maxBody = 64 * 1024 * 1024;
if (!token) { console.error("VINC_LAYOUT_TOKEN is not set"); process.exit(1); }

await AvoidLib.load(process.env.VINC_LAYOUT_WASM || fileURLToPath(new URL("./libavoid.wasm", import.meta.url)));
const elk = new ELK();
const service = createLayoutService(elk, async (ns, es, batching) =>
  es.length ? routeAcrossTopics(AvoidLib.getInstance(), ns, es, batching) : [],
{ maxBytes: Number(process.env.VINC_LAYOUT_MAX_BYTES || 800 * 1024 * 1024) });

const authorised = (header) => {
  const given = Buffer.from(header?.startsWith("Bearer ") ? header.slice(7) : ""), want = Buffer.from(token);
  return given.length === want.length && timingSafeEqual(given, want);
};
const send = (res, status, body) => {
  res.writeHead(status, { "content-type": "application/json" });
  res.end(JSON.stringify(body));
};

http.createServer((req, res) => {
  if (req.method === "GET" && req.url === "/health") return send(res, 200, { ok: true, ...service.health() });
  if (req.method !== "POST" || req.url !== "/v1/layout") return send(res, 404, { error: "not found" });
  if (!authorised(req.headers.authorization)) return send(res, 401, { error: "unauthorised" });
  const chunks = [];
  let size = 0;
  req.on("data", chunk => {
    size += chunk.length;
    if (size > maxBody) { send(res, 413, { error: "too large" }); req.destroy(); return; }
    chunks.push(chunk);
  });
  req.on("end", async () => {
    if (res.headersSent) return;
    let body;
    try { body = JSON.parse(Buffer.concat(chunks).toString("utf8")); } catch { return send(res, 400, { error: "bad json" }); }
    if (!validRequest(body)) return send(res, 400, { error: "bad request" });
    try {
      const { answer, stats } = await service.layout(body);
      // 로그에는 크기와 시간만 남긴다. id 는 남기지 않는다.
      console.log(JSON.stringify({ event: "layout", nodes: body.nodes.length, edges: body.edges.length, ...stats }));
      send(res, 200, { answer, stats });
    } catch (error) {
      console.error(JSON.stringify({ event: "layout_failed", nodes: body.nodes.length, error: String(error).slice(0, 300) }));
      send(res, 500, { error: "layout failed" });
    }
  });
}).listen(port, () => console.log(JSON.stringify({ event: "listening", port })));

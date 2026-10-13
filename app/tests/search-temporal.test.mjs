import test from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const result = await build({ entryPoints: [fileURLToPath(new URL("../src/search-temporal.ts", import.meta.url))],
  bundle: true, platform: "node", format: "esm", write: false });
const { temporalEntries } = await import(
  `data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString("base64")}`);

test("unknown dates stay unknown and update time does not become validity", () => {
  const entries = Object.fromEntries(temporalEntries({ temporal: { updated_at: "2026-10-13" } }));
  assert.equal(entries.updated_at, "2026-10-13");
  for (const key of ["created_at", "valid_from", "valid_until", "superseded_by", "validity"])
    assert.equal(entries[key], "unknown");
});

test("both chunk and node evidence retain conflicts and explicit replacement evidence", () => {
  const temporal = { created_at: "2020", valid_until: "2021", claim_status: "conflict",
    conflicts: ["record:other"], supersession: [{ evidence: "Synthetic ruling", status: "comparable" }] };
  for (const row of [{ id: "record:a", temporal }, { doc_id: "synthetic", temporal }]) {
    const entries = Object.fromEntries(temporalEntries(row));
    assert.equal(entries.conflicts, "record:other");
    assert.match(entries.supersession, /Synthetic ruling/);
    assert.equal(entries.claim_status, "conflict");
  }
});

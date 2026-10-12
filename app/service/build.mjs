// Bundles the layout service into one file next to the libavoid wasm it loads.
// Usage: node service/build.mjs [outDir]   (default: dist/layout-service)
import { build } from "esbuild";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const out = path.resolve(process.argv[2] || path.join(here, "..", "dist", "layout-service"));
fs.mkdirSync(out, { recursive: true });
await build({ entryPoints: [path.join(here, "layout-server.mjs")], outfile: path.join(out, "layout-server.mjs"),
  bundle: true, platform: "node", format: "esm", target: "node22",
  // elkjs 의 번들판은 CommonJS 라 ESM 출력에서 require 가 필요하다.
  banner: { js: "import { createRequire } from 'node:module'; const require = createRequire(import.meta.url);" } });
fs.copyFileSync(path.join(here, "..", "node_modules", "libavoid-js", "dist", "libavoid.wasm"), path.join(out, "libavoid.wasm"));
console.log(out);

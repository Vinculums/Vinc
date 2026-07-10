import { defineConfig } from "vite";

// Dev proxy — the scaffold serves the app and forwards sidecar endpoints
// same-origin, so no CORS surface is added to the sidecar. The Tauri shell
// (P4) replaces this with its own origin policy.
const SIDECAR = process.env.VINC_SIDECAR || "http://127.0.0.1:8787";
const PATHS = ["/health", "/atlas", "/graph", "/recall", "/search", "/domains",
  "/learner", "/decide", "/activate", "/backup", "/pin", "/tag", "/touch",
  "/ingest", "/jobs", "/plan", "/share", "/coupling"];

export default defineConfig({
  server: {
    proxy: Object.fromEntries(PATHS.map((p) => [p, { target: SIDECAR, changeOrigin: true }])),
  },
});

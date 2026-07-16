import { defineConfig } from "vite";
import { version } from "./package.json";

// Dev proxy — the scaffold serves the app and forwards sidecar endpoints
// same-origin, so no CORS surface is added to the sidecar. The Tauri shell
// (P4) replaces this with its own origin policy.
const SIDECAR = process.env.VINC_SIDECAR || "http://127.0.0.1:8787";
const PATHS = ["/health", "/atlas", "/graph", "/recall", "/search", "/domains",
  "/learner", "/decide", "/activate", "/backup", "/pin", "/tag", "/touch",
  "/ingest", "/jobs", "/plan", "/share", "/coupling", "/suggest-ties"];

export default defineConfig({
  // 진단 노출용 앱 버전(빌드 시 상수 폴딩) — sidecarDiag 가 리포트에 버전을 박아
  // "어느 버전에서 났는지" 혼동을 원천 차단한다(macOS v0.1.6 리포트 대응).
  define: { __APP_VERSION__: JSON.stringify(version) },
  server: {
    proxy: Object.fromEntries(PATHS.map((p) => [p, { target: SIDECAR, changeOrigin: true }])),
  },
});

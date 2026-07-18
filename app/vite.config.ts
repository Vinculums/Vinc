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
  // base: 셸/브라우저 dev 는 루트("/"). 웹 Vinc 는 vincs.app/app/ 로 서빙하므로
  // 웹 빌드만 VITE_BASE=/app/ 로 뽑는다 — 셸 빌드(release.yml)는 env 미설정 → "/"
  // 불변(방금 릴리스된 자동업데이트 앱의 asset 경로를 절대 건드리지 않는다). 앱은
  // 해시 라우팅이라 서브패스에서도 문서 경로가 /app/ 로 고정돼 상대 asset 이 맞는다.
  base: process.env.VITE_BASE || "/",
  // 진단 노출용 앱 버전(빌드 시 상수 폴딩) — sidecarDiag 가 리포트에 버전을 박아
  // "어느 버전에서 났는지" 혼동을 원천 차단한다(macOS v0.1.6 리포트 대응).
  define: { __APP_VERSION__: JSON.stringify(version) },
  server: {
    proxy: Object.fromEntries(PATHS.map((p) => [p, { target: SIDECAR, changeOrigin: true }])),
  },
});

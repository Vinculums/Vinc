import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Tauri 셸이 소비하는 프론트엔드 빌드. 제어(IPC)=@tauri-apps/api, 데이터=사이드카 HTTP.
// @see vinc.arch — WebView 는 그래프/질의 로직을 담지 않고 localhost 사이드카만 호출.
const host = process.env.TAURI_DEV_HOST;

export default defineConfig({
  plugins: [react()],
  // Tauri 는 고정 포트를 기대한다. 실패 시 조용히 다른 포트로 넘어가지 않도록 strict.
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    host: host || false,
    hmr: host ? { protocol: "ws", host, port: 1421 } : undefined,
    watch: { ignored: ["**/src-tauri/**"] },
  },
  // Tauri CLI 가 읽는 산출 디렉토리와 정합.
  build: {
    target: "es2021",
    minify: !process.env.TAURI_DEBUG ? "esbuild" : false,
    sourcemap: !!process.env.TAURI_DEBUG,
  },
});

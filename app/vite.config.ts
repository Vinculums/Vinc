import { execSync } from "node:child_process";
import { defineConfig } from "vite";
import { version } from "./package.json";

// 빌드 식별자 — **버전 문자열만으로는 부족하다.** 웹 Vinc 는 태그와 무관하게
// 배포되므로(site-deploy 가 Vinc main HEAD 를 체크아웃), 데스크탑 v0.6.7 설치본과
// 웹이 서로 다른 코드인데 `__APP_VERSION__` 은 둘 다 "0.6.7" 이 된다 — 실측으로
// 그 상태였다(태그 이후 app/ 14파일 변경). 진단 리포트에 버전을 박아 "어느
// 버전에서 났는지 혼동을 원천 차단"한다는 원래 취지가 웹 경로에서 무력화되므로
// 짧은 커밋 SHA 를 함께 싣는다. CI 는 GITHUB_SHA, 로컬은 git, 둘 다 없으면 빈 값.
const buildSha = (() => {
  const fromCi = process.env.GITHUB_SHA;
  if (fromCi) return fromCi.slice(0, 7);
  try {
    return execSync("git rev-parse --short=7 HEAD", { encoding: "utf8" }).trim();
  } catch {
    return "";
  }
})();

// Dev proxy — the scaffold serves the app and forwards sidecar endpoints
// same-origin, so no CORS surface is added to the sidecar. The Tauri shell
// (P4) replaces this with its own origin policy.
const SIDECAR = process.env.VINC_SIDECAR || "http://127.0.0.1:8787";
// ⚠ 이 목록은 **드리프트 원천**이다: 사이드카에 라우트를 더하고 여기 등재를 잊으면
// 브라우저 dev 에서 그 요청만 index.html(200)을 받아, 앱은 "사이드카가 꺼졌다"고
// 오진한다(실측: /ledger·/doc·/node-context 12종이 빠져 Sankey·Reader 가 통째로
// 죽어 있었다). 개별 열거 대신 **접두 정규식 하나**로 바꿔 새 라우트가 자동으로
// 실린다 — 앱이 부르는 경로는 전부 아래 접두 중 하나로 시작한다.
// (앱 자산은 /, /src, /@vite, /node_modules 로 오므로 충돌하지 않는다.)
const API_RE = new RegExp("^/(health|view|graph|ledger|recall|search|domains|learner"
  + "|decide|activate|backup|pin|tag|touch|ingest|jobs|plan|share|coupling"
  + "|suggest-ties|sync|connect|doc|docs|docs-in-range|node|node-context|node-edges|edge-context"
  + "|ledger-drilldown|nav-tree|delete|delete-edge|merge-nodes|promote-candidates"
  // 관례·어휘(GM-8) — `conventions`(관례 읽기/재생성)와 `themes`/`types`(재가 대기
  // 목록·승인·거부·개명). 앱이 여태 어휘 라우트를 부른 적이 없어 빠져 있었다.
  + "|conventions|themes|types"
  // 변경 표식(VIN-129) — 열린 화면이 12초마다 묻는다(`live.ts`). 빠지면 index.html 을
  // 받아 JSON 파싱이 조용히 실패하고, 브라우저 dev 에서만 저절로 새로워지지 않는다.
  + "|changes"
  // 저장 설정과 재기동 전 종료(VIN-130). 빠지면 설정의 로컬 모드 덩어리가 브라우저 dev 에서
  // 조용히 사라진다(설정을 못 읽으면 저장 줄만 남긴다).
  + "|config|shutdown"
  + "|domain|evidence)(/|$|\\?)");

export default defineConfig({
  // base: 셸/브라우저 dev 는 루트("/"). 웹 Vinc 는 vincs.app/app/ 로 서빙하므로
  // 웹 빌드만 VITE_BASE=/app/ 로 뽑는다 — 셸 빌드(release.yml)는 env 미설정 → "/"
  // 불변(방금 릴리스된 자동업데이트 앱의 asset 경로를 절대 건드리지 않는다). 앱은
  // 해시 라우팅이라 서브패스에서도 문서 경로가 /app/ 로 고정돼 상대 asset 이 맞는다.
  base: process.env.VITE_BASE || "/",
  // 진단 노출용 앱 버전(빌드 시 상수 폴딩) — sidecarDiag 가 리포트에 버전을 박아
  // "어느 버전에서 났는지" 혼동을 원천 차단한다(macOS v0.1.6 리포트 대응).
  define: {
    __APP_VERSION__: JSON.stringify(version),
    __BUILD_SHA__: JSON.stringify(buildSha),
  },
  server: {
    proxy: { [API_RE.source]: { target: SIDECAR, changeOrigin: true } },
  },
});

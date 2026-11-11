import { execSync } from "node:child_process";
import { defineConfig, type Plugin } from "vite";
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

// Dev proxy (browser dev only): vite serves the app and forwards sidecar endpoints
// same-origin, so no CORS surface is added to the sidecar. The Tauri shell does not
// go through it: there the app calls the sidecar on 127.0.0.1 directly (port and token
// over IPC, `api.ts` `connectSidecar`) under the shell's CSP (`src-tauri/tauri.conf.json`).
const SIDECAR = process.env.VINC_SIDECAR || "http://127.0.0.1:8787";
// ⚠ 이 목록은 **드리프트 원천**이다: 사이드카에 라우트를 더하고 여기 등재를 잊으면
// 브라우저 dev 에서 그 요청만 index.html(200)을 받아, 앱은 "사이드카가 꺼졌다"고
// 오진한다(실측: /ledger·/doc·/node-context 12종이 빠져 Sankey·Reader 가 통째로
// 죽어 있었다). 개별 열거 대신 **접두 정규식 하나**로 바꿔 새 라우트가 자동으로
// 실린다 — 앱이 부르는 경로는 전부 아래 접두 중 하나로 시작한다.
// (앱 자산은 /, /src, /@vite, /node_modules 로 오므로 충돌하지 않는다.)
// 목록은 `app/src` 가 실제로 부르는 경로와 같아야 한다. 앱이 더는 부르지 않는 이름은 뺀다
// (`ledger`·`node` 처럼 접두가 같은 형제는 `ledger-drilldown`·`node-context` 로 따로 실린다).
const API_RE = new RegExp("^/(health|view|graph|recall|search|domains"
  + "|activate|backup|pin|ingest|jobs|share|coupling"
  + "|suggest-ties|sync|connect|doc|docs|docs-in-range|node-context|edge-context"
  + "|ledger-drilldown|nav-tree|delete|delete-edge|promote-candidates"
  // 0.9.8 — 주제 설명(`topic-description`, 사이드카 라우트)과 계정 평면의 두 경로
  // (`conversion/*` 주제→팀 전환, `spaces` 팀 목록). 뒤의 둘은 사이드카에 라우트가 없어
  // 브라우저 dev 에서는 404 JSON 을 받는다. 빠져 있으면 index.html(200)을 받아 JSON
  // 파싱이 실패하고, 앱은 그것을 고장으로 읽는다.
  + "|topic-description|conversion|spaces"
  // 관례·어휘(GM-8) — `conventions`(관례 읽기/재생성)와 `themes`/`types`(재가 대기
  // 목록·승인·거부·개명). 앱이 여태 어휘 라우트를 부른 적이 없어 빠져 있었다.
  + "|conventions|themes|types"
  // 변경 표식(VIN-129) — 열린 화면이 12초마다 묻는다(`live.ts`). 빠지면 index.html 을
  // 받아 JSON 파싱이 조용히 실패하고, 브라우저 dev 에서만 저절로 새로워지지 않는다.
  + "|changes"
  + "|roles"
  // 저장 설정과 재기동 전 종료(VIN-130). 빠지면 설정의 로컬 모드 덩어리가 브라우저 dev 에서
  // 조용히 사라진다(설정을 못 읽으면 저장 줄만 남긴다).
  + "|config|shutdown"
  + "|domain|evidence)(/|$|\\?)");

// 웹 빌드(/app/)의 첫 그림을 셸 JS 보다 먼저 맞춘다. 스킨·테마는 main.ts 가 정하는데 그건
// 모듈이 돈 뒤라, 첫 페인트가 데스크톱 스킨·기본 테마로 그려졌다가 바뀔 수 있고, 공홈 GNB 를
// main.ts 가 주입하는 순간 셸 전체가 GNB 높이만큼 밀린다. 그래서 html 에 스킨을 박고, 테마를
// 미리 칠하고, GNB 자리를 빈 머리로 잡아 둔다(gnb.js 는 `#site-gnb` 가 있으면 그것을 갈아
// 끼운다). 셸 빌드("/")는 건드리지 않는다.
// 근거: [[record:web-app-shifted-and-flashed-before-the-shell-ran]]
const WEB_BASE = "/app/";
const PREPAINT = "try{var l=localStorage,p=l.getItem('vinc.theme')||l.getItem('vinc.site.theme.v2')||'dark';"
  + "document.documentElement.dataset.theme=p==='light'||(p==='auto'&&!matchMedia('(prefers-color-scheme: dark)').matches)?'light':'dark'"
  + "}catch(e){}";
function webShell(): Plugin {
  let base = "/";
  return {
    name: "vinc-web-shell",
    configResolved(config) { base = config.base; },
    transformIndexHtml(html) {
      if (base !== WEB_BASE) return html;
      // 셀프호스트 허브의 번들(VITE_SELF_HOSTED=1)에는 사이트 머리(gnb.js)가 없다: 스킨과 테마만 맞춘다.
      if (process.env.VITE_SELF_HOSTED === "1") {
        return html
          .replace("<html lang=\"en\">", "<html lang=\"en\" data-skin=\"web\">")
          .replace("</head>", `<script>${PREPAINT}</script>\n  </head>`);
      }
      return html
        .replace("<html lang=\"en\">", "<html lang=\"en\" data-skin=\"web\">")
        .replace("</head>", `<script>${PREPAINT}</script>`
          + "<link rel=\"preload\" href=\"/assets/gnb.js\" as=\"script\" />\n  </head>")
        .replace("<body>", "<body>\n    <header id=\"site-gnb\" aria-hidden=\"true\"></header>");
    },
  };
}

export default defineConfig({
  plugins: [webShell()],
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

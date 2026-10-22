import { cpSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

// 읽기 전용 커뮤니티 그래프의 따로 선 빌드. 사이트가 /community-view/ 아래에
// 그대로 얹는다. 앱 빌드(vite.config.ts)에 입력을 더하지 않는 이유는 데스크톱 셸과
// /app/ 산출물을 한 바이트도 바꾸지 않기 위해서다.
const here = (p: string) => fileURLToPath(new URL(p, import.meta.url));
const outDir = here("./dist-community-view");

export default defineConfig({
  root: here("./community-view"),
  base: "/community-view/",
  // 앱의 public/(파비콘 등)은 이 화면의 것이 아니다. 배치 라이브러리(elkjs·libavoid)는 같이
  // 실려 나가므로 그 라이선스 고지만 앱과 같은 자리(third-party/)에 함께 둔다.
  publicDir: false,
  build: { outDir, emptyOutDir: true },
  plugins: [{
    name: "third-party-notices",
    apply: "build",
    closeBundle() { cpSync(here("./public/third-party"), `${outDir}/third-party`, { recursive: true }); },
  }],
});

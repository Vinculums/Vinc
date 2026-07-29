# Vinc app — 상단 바 단일 셸(D56) · 표면 3 + Settings 시트

데스크탑 Tauri 셸과 웹 Vinc(`vincs.io/app`)가 공유하는 프론트엔드 패키지 — v0.6.7 라이브.
정본 설계는 `Vinc-docs vinc/D/*`(표면 설계서)와 `vinc.d.ux-research`(연구·DEC 근거),
카피는 D18 카피 덱(`src/copy.ts`, en/ko 이중 언어 — D42 언어 전환).

> **표면 수 정정(2026-07-29):** 구 제목의 "5 surfaces + Settings (D15)"는 현행이 아니다.
> D56(좌측 레일 폐지 → 상단 바 단일) 이후 라우팅되는 표면은 **3**이다 — `src/main.ts` 의
> `SURFACES`/`ORDER` 가 `atlas`(사용자 표기 `Home`)·`search`·`packs` 이고, 구 표면 해시는
> `ALIAS`(`intake→packs`·`records→search`)로 흡수된다. `src/surfaces/` 에 파일이 더 있는 것은
> **모듈 분할**이지 표면 수가 아니다(`atlas*.ts` 만 5개). Settings 는 표면이 아니라 시트다.

```
npm install
npm run dev        # http://localhost:5173 — 사이드카(api.cli serve) 필요
npm run build      # tsc --noEmit + vite build (게이트)
```

- **데이터 평면 = 런타임 오리진에 따라 3갈래**(`src/api.ts`). 그래프/질의 로직은 UI 에
  없다(레이아웃 좌표도 사이드카/프록시가 계산 — D17).
  - **Tauri 셸**: 로컬 사이드카(`127.0.0.1` + Bearer 세션 토큰)만 호출. 포트·토큰은
    셸이 `sidecar_conn` IPC 로 주입하는 동적값이고 `8787` 은 그 전까지의 폴백 기본값.
    외부 왕복 없음.
  - **웹 Vinc**(`WEB` = 셸도 localhost 도 아닌 호스티드): 데이터 평면이 로컬 사이드카가
    아니라 **계정 그래프 프록시**(`/api/graph/*`)로 간다. 인증 = 계정 세션 쿠키
    (`credentials: include`), 쓰기 = CSRF 더블서브밋(`X-CSRF-Token` ← `__Host-csrf`),
    owner 는 클라가 보내지 않고 허브가 세션 sub 로 강제한다. 401 → `/login`.
    부팅 시 `ensureWebAuth` 가 `/api/me` 로 세션을 **먼저** 확인하고 미인증이면 서피스를
    그리기 전에 `/login` 으로 보낸다(화면 깜빡임 제거).
  - **로컬 vite dev**(localhost): `WEB=false` — 기존 same-origin 프록시를 그대로 쓴다.
- **Settings(팝오버)** — Lock(D7)·MCP·Data·Theme·Language 는 공통. 아래는 셸/로컬 전용
  (웹모드에선 렌더하지 않음 — owner 를 세션이 강제하고 로컬 사이드카가 없으므로):
  - **MCP 커넥터 연결/제거**: 이 설치본을 로컬 Claude Desktop/Code 에 등록·해제
    (`mcp_install`/`mcp_uninstall` IPC → 번들 사이드카 `mcp install|uninstall`).
  - **계정 그래프 동기화(S5)**: 회원 키(`vinc_sk_…`, 이 기기 localStorage 에만)·동기
    도메인 선택·[지금 동기화] 버튼. 로컬↔계정 양방향 sync(v0.3.0)를 잡큐로 실행.
  - **Workspace(owner)**·**Advanced(사이드카 주소·토큰)** — 개발/로컬 스캐폴드 편의.
- **자동 업데이트(v0.3.1)** — `src/update.ts::checkForUpdate` 를 기동 직후 비차단 호출
  (`main.ts`). 셸 전용(웹 Vinc·브라우저 dev 는 no-op). Tauri v2 updater 가
  `tauri.conf.json` 의 pubkey 로 다운로드 아티팩트의 minisign 서명을 강제하며, 새 버전이
  **있을 때만** 하단 토스트로 알린다("나중에" 가 기본, 확인 실패는 무음).
- **커뮤니티 공유(웹 전용)** — Packs 표면의 `Share to community` CTA(WEB 게이트) →
  `api.share` 도메인 슬라이스 팩 → 동의 게이트 게시 시트 → `community.publish`
  (`/api/community/publish`) → 공개 갤러리 `/community`. owner 는 세션 강제(`cfg.owner` 무시).
- **디자인 토큰**: `src/tokens.css` 는 `Vinc-docs/DESIGN`(design-tokens.json →
  gen_tokens.py)의 소비 사본 — 수기 수정 금지, 갱신은 gen_tokens.py 재실행 후 복사.
- **Atlas**: L0(`GET /atlas`) → L1(`GET /graph?domain=`, 게이트웨이 = 결합 노드) →
  L2(`GET /graph?seed=&depth=`). hover = 이웃 유지·비이웃 페이드, double-click = refocus,
  breadcrumb 후퇴.
- **Tauri 셸(제어 = IPC)** 은 v0.1.0 부터 이 프론트엔드를 감싸 패키징돼 배포 중(현 v0.6.7).
  파일 다이얼로그·창 제어·사이드카 수명이 IPC 로 가고, 본 패키지의 fetch 데이터 평면은
  셸/웹 공용으로 그대로 유지된다.

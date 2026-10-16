# Vinc app

데스크톱 Tauri 셸과 웹 Vinc(`vincs.io/app`)가 함께 쓰는 프론트엔드 패키지(Vite, TypeScript)다.
그래프와 질의 로직은 여기 없다. 레이아웃 좌표까지 사이드카와 계정 프록시가 계산하고, 이 패키지는
HTTP 라우트만 부른다. 팀 매뉴얼은 그래프의 `vinc.manual.app`, 표면 구성의 정본은
`vinc.spec.surface-tree` 다.

## 명령

```
npm ci
npm run dev          # http://localhost:5173, 사이드카가 따로 떠 있어야 한다
npm run typecheck    # tsc --noEmit
npm run build        # typecheck, node 단위 테스트, vite build 순서. CI 의 app 게이트
npm run tauri dev    # 셸까지 띄운다(beforeDevCommand 가 npm run dev)
```

- 브라우저 dev 는 `vite.config.ts` 의 프록시가 사이드카 경로(`API_RE` 접두 목록)를 같은 오리진으로
  넘긴다. 대상은 `VINC_SIDECAR`, 없으면 `http://127.0.0.1:8787` 이다. 사이드카는 리포 루트에서
  `python -m api.cli serve --port 8787` 로 띄운다(`--port` 기본값은 0, 즉 임의 포트). 세션 토큰은
  Settings > Advanced 에 넣는다.
- 단위 테스트를 하나만 돌리려면 `npm run test:view-layout` 처럼 `package.json` 의 `test:*` 스크립트를 쓴다.
- `app/service/` 는 허브가 부르는 레이아웃 서비스(`vinc-layout` 컨테이너)다.
  `npm run build:layout-service` 가 번들을 만들고 `npm run test:layout-service` 가 검사한다.

## 표면과 시트

라우터(`src/main.ts`)가 아는 표면은 둘이다. `SURFACES` 와 `ORDER` 가 같은 목록이다.

| 해시 | 모듈 | 키 |
|---|---|---|
| `#view` | `src/surfaces/view.ts` (2D·3D·타임라인·번들 viewpoint 는 `view*.ts` 로 나뉜다) | `1` |
| `#search` | `src/surfaces/search.ts` (빈 질의의 문서 목록은 `records.ts`) | `2` |

모르는 해시는 `view` 로 간다. 옛 표면 해시는 `ALIAS` 가 정규 해시로 바꿔 쓰고 다시 들어오며,
`#intake`·`#packs` 는 `ALIAS_TOOLS` 에 따라 Settings > Data 의 Import·Share 탭을 함께 연다.
`src/surfaces/` 에 파일이 더 있는 것은 모듈 분할이지 표면 수가 아니다.

나머지는 표면이 아니라 시트(`components.ts` 의 `openSheet`)다. 지금 화면 위에 열리고 이동해도 남는다.

- **Settings** (`surfaces/settings.ts`, `openSettings`): 왼쪽 분류 레일과 본문. 분류는 General ·
  Reading · Account · AI · Data · GRAPH.md · Advanced · About 이고, AI 와 Advanced 는 셸 전용이라
  웹에서는 숨긴다. Data 분류가 Tools 를 담는다.
- **Tools** (`tools.ts`, `openTools`): Create · Import · Share 탭. 상단 바 단추로 바로 열 수도 있다.
- **Share** (`sheets.ts`, `openShareSheet`): 팩을 만들고 목적지(파일, 팀에 있을 때 내 스페이스, 웹이면
  커뮤니티)를 고른다.
- **Import** (`sheets.ts`, `openImportSheet` · `openDocImportSheet`): `.vincpack` 과 원문 파일을 받는다.
  셸에서는 `.vincpack` 더블클릭도 여기로 온다(`openfiles.ts`).

`Cmd/Ctrl K` 는 명령 팔레트, 숫자 키는 `ORDER` 순서의 표면 이동이다.

## 데이터 평면

실행 위치는 `src/api.ts` 가 오리진으로 가른다.

- **셸** (`IN_SHELL`: `tauri:` 프로토콜 또는 `tauri.localhost`): 이 기기의 사이드카를
  `127.0.0.1` 과 Bearer 세션 토큰으로 부른다. 포트와 토큰은 셸의 `sidecar_conn` 커맨드에서 받고,
  `8787` 은 그 전까지의 기본값일 뿐이다.
- **웹 Vinc** (`WEB`: 셸도 localhost 도 아님): 경로 앞에 `/api/graph` 를 붙여 계정 그래프 프록시로
  간다. 인증은 계정 세션 쿠키, 쓰기는 CSRF 더블 서브밋이고, owner 는 클라이언트가 보내지 않는다.
  부팅 때 `ensureWebAuth` 가 세션을 먼저 확인하고, 없으면 화면을 그리기 전에 `/login` 으로 보낸다.
- **로컬 vite dev**: `WEB` 이 거짓이라 같은 오리진 프록시로 로컬 사이드카를 본다.

셸에는 데이터 모드가 둘 있다(`dataMode`, `decideDataMode`).

- **계정 모드**: 화면이 계정 그래프를 직접 읽고 쓴다. 호출은 사이드카의 통과로(`/space/proxy`)를 타고
  계정으로 넘어가며, 회원 키는 OS 키체인에 있다.
- **로컬 모드**: 그래프가 사람이 고른 폴더의 스토어에만 있다.
- 계속 도는 동기화는 없다. 데이터는 Settings > Account 에서 모드를 바꿀 때만 옮긴다(`storage-mode.ts`).
  정본 설정은 사이드카의 `/config` 이고 localStorage 는 그 사본이다.
- 열린 화면은 `live.ts` 가 `/changes` 표식을 읽어 저절로 새로 고친다.

## 사이드카 계약

`src/api.ts` 의 호출 함수가 갈래를 나눈다.

- `call`: 기본 진입점. 셸의 계정 모드이고 경로가 `SIDECAR_ONLY` 밖이면 `accountCall`, 아니면 `localCall`.
- `localCall`: 이 기기의 사이드카(웹이면 `/api/graph` 프록시)로 직접 간다. 모드 전환과 잡 진행처럼
  늘 이 기기의 일인 호출이 쓴다.
- `accountCall`: 셸 계정 모드의 데이터 호출. 계정의 오류 봉투를 로그인 필요, 연결 불가, 기타로 가른다.
- `viaProxy`: `accountCall` 이 쓰는 한 번의 `/space/proxy` 왕복.
- 헬스 대기: `connectSidecar` 가 `/health?live=1` 을 약 12초까지 기다린다(`waitForSidecar`).
  로그인 직전 `ensureSidecar` 는 죽은 사이드카를 `sidecar_restart` 로 다시 띄우고,
  `restartSidecar` 는 폴더를 바꾼 뒤 약 30초까지 기다린다. 응답의 `degraded`·`migrating` 은
  진단과 화면 신호로 넘긴다.

새 라우트는 사이드카와 앱만의 일이 아니다. 웹에서 쓰려면 계정 프록시 허용목록 두 곳에도 올라야
한다(`vinc.manual.app` §2).

## 디자인 토큰

`src/tokens.desktop.css`, `src/tokens.web.css`, `src/theme.ts` 는 `design-hub` 의 생성물이다.
손으로 고치지 않는다. 값은 `design-hub/DESIGN/design-tokens.json` 에서 바꾸고
`design-hub/scripts/sync_out.py --apply` 로 내보낸다(`theme.ts` 는 `gen_theme_ts.py` 가 만든다).
런타임에는 `main.ts` 가 `data-skin`(`web` 또는 `desktop`)을 정해 토큰 세트를 고르고,
dev 에서는 `?skin=web` 으로 바꿔 볼 수 있다. 화면 문구는 `src/copy.ts` 에 영어와 한국어가 함께 있다.

## Tauri 셸

`src-tauri/src/main.rs` 하나가 진입점이다(`lib.rs` 는 없다). 사이드카(`binaries/vinc`)를 동적 포트로
띄우고, 웹뷰에 다음 커맨드를 연다.

- 사이드카: `sidecar_conn`, `sidecar_status`, `sidecar_alive`, `sidecar_restart`
- MCP 커넥터: `mcp_install`, `mcp_uninstall`
- CLI 링크: `cli_link_status`, `cli_link_install`, `cli_link_remove`
- 키체인: `secret_get`, `secret_set`, `secret_delete`, `secret_available`
- 파일: `take_pending_files`, `pack_file::save_pack_file`

`tauri.conf.json` 은 `withGlobalTauri: false` 다. 셸 API 는 셸일 때만 동적 import 한다(`shellInvoke`).
자동 업데이트는 `src/update.ts` 의 `checkForUpdate` 가 기동 뒤 비차단으로 확인하고, updater 플러그인이
같은 파일의 pubkey 로 서명을 검증한다. 웹과 브라우저 dev 에서는 아무것도 하지 않는다.

## 커뮤니티 공유 (웹 전용)

Share 시트의 커뮤니티 목적지는 `WEB` 에서만 보인다. `publishToCommunity`(`sheets.ts`)가
`/api/community/agents/destinations` 로 사용 가능 여부를 확인하고, 팩을 `sessionStorage` 에 맡긴 뒤
사이트의 `/community/share/` 작업으로 넘긴다. 공개 맥락과 동의는 그 작업이 받는다.
데스크톱에는 아직 커뮤니티 목적지가 없다.

## 테스트

- `app/tests/*.test.mjs`: `node --test` 단위 테스트. `npm run build` 가 돌린다.
- `app/dev/*-check.ts`: 브라우저 없이 도는 검사. CI 의 app 잡이
  `node --experimental-strip-types` 로 전부 돌린다.
- `app/dev/*.mjs` 와 `*.html`: Playwright 나 브라우저로 손으로 돌리는 확인. 실행법은 각 파일 첫 줄에 있다.
- `tests/app/*.py`: TypeScript 소스 텍스트를 읽는 계약 테스트. 리포의 pytest 가 돌리므로
  이름을 바꾸면 같은 PR 에서 함께 고친다. `tests/app/sidecar_health_deadline.mjs` 는 헬스 대기 함수를
  떼어 실제로 실행한다.

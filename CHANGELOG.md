# Changelog

Vinc 데스크톱 앱 릴리스 기록. 사용자 노출 카피 정본은 `site/release/`(웹 릴리스 노트)이며,
이 파일은 개발자용 요약이다.

## v0.1.4 — 2026-07-14

- **fix(sidecar)**: **첫 실행 적재의 진짜 원인 — CORS 부재** 해소. 패키지 앱 웹뷰
  (`http://tauri.localhost` / `tauri://localhost`)는 사이드카(`http://127.0.0.1:8787`)와
  교차출처인데 사이드카가 `Access-Control-Allow-Origin` 을 안 보내 브라우저가 fetch 를
  차단("Failed to fetch") → 샘플·팩 import 실패(dev 는 same-origin 프록시라 안 걸렸음).
  모든 응답에 `Access-Control-Allow-Origin: *` + OPTIONS 프리플라이트 처리 추가(루프백 +
  Bearer 토큰이라 무단 접근은 토큰에서 차단). v0.1.3 진단(`Failed to fetch · token=64ch ·
  health TIMEOUT`)으로 토큰은 정상·연결만 막힘을 확인해 특정. (`api/server.py`)
- v0.1.3 의 진단 노출(sidecarDiag)·토큰 견고화는 유지.

## v0.1.3 — 2026-07-14

- **diag/fix(app)**: v0.1.2 에서도 첫 실행 적재가 실패하는 사례 대응. 토큰 획득을 더 견고하게
  (`__TAURI__` 주입 대기 후 `sidecar_conn` invoke) 하고, 적재 실패 시 **실제 에러와 연결
  상태(base · token 유무 · HTTP 상태 · health)를 화면에 노출**해 원인을 즉시 특정한다.
  샘플/파일 import 둘 다 동일 지점(사이드카 `/ingest/pack` HTTP)에서 실패 확인 → 데이터가
  아니라 연결/인증 문제로 좁혀짐. (`app/src/api.ts` sidecarDiag, `app/src/surfaces/intake.ts`)

## v0.1.2 — 2026-07-14

- **fix(app)**: 패키지 앱에서 **사이드카 세션 토큰이 프론트에 안정적으로 전달되지 않아
  모든 데이터 호출이 401 로 거부**되던 문제(샘플 팩·파일 import 포함) 해소. 셸이 `win.eval`
  로 localStorage 에 토큰을 넣던 방식이 페이지 로드 타이밍에 불안정했음 → main.rs 에
  `sidecar_conn` Tauri 커맨드를 추가해 프론트가 기동 시 포트·토큰을 직접 받아 설정(정본,
  `win.eval` 은 폴백 유지). `connectSidecar` 가 `/health` 응답까지 대기해 기동 지연에 의한
  첫 상호작용 실패도 방지. `withGlobalTauri` 활성화(invoke 노출).
  (`app/src-tauri/src/main.rs`, `app/src/api.ts`, `app/src/main.ts`, `tauri.conf.json`)
- 진단: 사이드카 직접 호출 시 인증 토큰과 함께면 `/domains` 200·`executed:true`, 무토큰
  401 확인 → 스토어·프로비저닝·적재는 정상, 토큰 전달만이 결함이었음.

## v0.1.1 — 2026-07-13

- **fix(sidecar)**: 첫 실행 데이터 적재 실패 해소. 신규 설치본에서 로컬 스토어 스키마가
  프로비저닝되지 않아 모든 적재(동봉 샘플 팩 포함)가 `Table Concept does not exist` 로
  실패하던 문제 — `VincApp.open` 이 기동 시 스키마를 멱등 생성한다. (`api/server.py`, 0d8106c)
- **fix(app)**: 패키징된 셸에서 프론트엔드 데이터 호출이 사이드카가 아닌 webview origin
  (`tauri://localhost` 등)으로 향하던 문제 — 셸 감지 시 기본 base 를 셸이 고정한
  `http://127.0.0.1:8787` 로 설정. Settings 의 수동 지정은 계속 우선한다. (`app/src/api.ts`)
- **feat(app)**: Intake 에서 **지식 팩 파일 import** — `.vincpack`/`.json` 을 파일 선택 또는
  드래그&드롭으로 `/ingest/pack`·`/ingest/fragment` 에 적재(HTML File API, 플러그인 불요;
  셸 드롭은 `dragDropEnabled:false` 로 웹뷰 수신). 자리표시자("데스크톱 앱에서 제공") 제거,
  **본인 문서→그래프는 Claude/MCP 경로**임을 Intake 에 정직하게 안내. (`app/src/surfaces/intake.ts`)
- **build(installer)**: Windows NSIS 설치관리자 per-user 설치(관리자 권한 불요) + 설치 중
  한국어/영어 언어 선택. (`tauri.conf.json`, 26455b4)
- site: `/download` 첫 실행 우회 안내(미서명 빌드) · SignPath 귀속 문구, `/release` v0.1.0 노트.

## v0.1.0 — 2026-07-13

- 첫 공개 빌드 — 오프라인 코어(적재·그래프·의미 검색·팩) + Tauri 데스크톱 셸 + MCP 커넥터.
- 설치본: macOS .dmg(aarch64) · Windows .exe(NSIS) · Linux .AppImage/.deb.
- 알려진 문제: **신규 설치에서 데이터 적재 불가**(v0.1.1 에서 수정).

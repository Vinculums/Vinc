# Changelog

Vinc 데스크톱 앱 릴리스 기록. 사용자 노출 카피 정본은 `site/release/`(웹 릴리스 노트)이며,
이 파일은 개발자용 요약이다.

## v0.1.8 — 2026-07-16

macOS 리포트(`Load failed · base=http://127.0.0.1:49358 token=64ch · health TIMEOUT`)
추적 중 드러난 **진단 불능** 자체를 없애는 릴리스. 실제 Apple Silicon 러너에 배포본을
설치해 3차에 걸쳐 실측한 결과 — 격리(quarantine)·콜드스타트 지연·웹뷰 혼합콘텐츠
차단 **가설 3개가 모두 반증**됐고(깨끗한 macOS 에선 v0.1.6 이 정상 동작), 원인이
그 기기 고유임이 드러났다. 그런데 앱은 "health TIMEOUT" 외에 아무것도 말해주지
못했다 — 그 침묵이 진짜 결함이다.

- **fix(sidecar)**: **프로비저닝 실패가 사이드카를 통째로 죽이던 문제** 해소.
  `VincApp.open` 이 `store.provision` 예외를 밖으로 흘려 serve 가 즉사 → 포트에
  아무도 남지 않고 앱엔 원인 불명 "health TIMEOUT" 만 남았다(구버전 잔존 스토어·락·
  손상이면 재현). 이제 기동을 계속하고 사유를 stderr + `/health` 로 표면화한다.
  (`api/server.py`)
- **fix(sidecar)**: `/health` 가 **성치 않은 기동을 숨기지 않는다** — 스토어 부재나
  프로비저닝 실패 시 `{"status":"degraded", "provision_error"|"error": …}`. 기존
  정상 경로는 `{"status":"ok"}` 그대로(하위호환). 계약 테스트 3종 추가. (`api/server.py`,
  `tests/test_server.py`)
- **feat(app)**: 셸이 **사이드카 수명 이벤트(stderr·종료코드·시그널·스폰 에러)를 수집**해
  `sidecar_status` 커맨드로 노출, 프론트가 health 실패 시 진단에 그대로 싣는다
  (`… health TIMEOUT · sidecar[terminated: code=1 …]`). 그동안 이 이벤트를 버리고
  있어 사이드카 즉사가 원인 불명이었다. (`app/src-tauri/src/main.rs`, `app/src/api.ts`)
- **fix(app)**: health 대기 5s→12s(PyInstaller 단일파일 콜드 스타트 여유), degraded
  응답을 진단에 표기, 진단 문자열에 **앱 버전·셸이 준 실제 포트**를 각인
  (`v0.1.8 conn.port=… base=… token=…`) — 리포트만으로 버전·포트 혼동이 없게.
  (`app/src/api.ts`, `app/vite.config.ts`)

## v0.1.7 — 2026-07-16

- **fix(app)**: **세부 뷰(도메인/노드 포커스)에서 2D 모드 미적용 — 노드가 원점에 뭉치던 결함**
  수정. 좌표를 `fx/fy/fz` 핀에만 의존하지 않고 렌더 속성(x/y/z)에도 직접 기록해, 비동기 digest
  가 메시를 재생성한 뒤 cooldown 0 으로 틱 없이 멈추는 타이밍에 전 노드가 (0,0,0)에 뭉치던
  경합을 제거 — rAF 로 메시 생성을 기다렸다가 `d3ReheatSimulation()` 으로 동기화 프레임을 1 회
  강제한다. (`app/src/surfaces/atlas3d.ts`)
- **fix(app)**: 그리드(Multiples) 뷰에서 **각 카드의 그래프가 카드 밖으로 잘리던 결함** 수정 —
  세로 fov 만으로 고정하던 카메라 거리를 카드 비율(aspect, 좁은 축 기준)로 재계산해 좌우/상하
  노드가 프레임 안에 들어온다. (`app/src/surfaces/atlas3d.ts`)
- **fix(app)**: 창 크기 변경 시 **캔버스가 컨테이너를 따라가지 않던 문제** 수정 — 세부 뷰·그리드
  뷰 양쪽에 `ResizeObserver` 를 붙여 실측 크기로 추적, 캔버스 넘침으로 생기던 불필요한
  스크롤바도 함께 사라진다. (`app/src/surfaces/atlas3d.ts`)
- **fix(sidecar)**: **Windows 에서 Claude Desktop 이 Vinc 를 실행하면 즉시 종료되던 크래시**
  수정 — 콘솔/파이프 기본 인코딩(cp949)에서 비-ASCII 직렬화(MCP stdio 포함)가
  UnicodeEncodeError 로 죽던 문제. 표준 입출력 스트림을 UTF-8 로 강제 재구성. 이제 "Connect
  Claude Desktop" 이후 도구가 정상 동작한다. (`api/cli.py`)
- **fix(api)**: **vinc_domains 가 Episode 만 세던 편향** 수정 — Concept·Record 를 포함한 3종을
  도메인별로 집계해 `{episodes, concepts, records, total}` 로 병합 응답(`episodes` 필드는
  하위호환 유지). (`api/query.py`)
- **fix(api)**: vinc_graph — **연결로만 등장한 노드의 label 이 id 로 노출되던 버그** 수정(title
  반환), **시드 미존재(`found:false`)와 고립 노드(연결 0, 시드 단독 반환)를 구분**하는 선조회
  추가. (`api/query.py`)
- **fix(api)**: vinc_ingest — 일부 엣지가 스킵됐는데도 `executed:true` 만 보고 전량 성공으로
  오인되던 문제 — **warnings 존재 시 `partial:true` 신호 추가**. `relates_to` 는 무방향으로
  취급해 역방향 엣지가 이미 있으면 재생성을 skip(양방향 재적재로 degree 가 부풀지 않도록),
  skip 수는 `edges.skipped_duplicate` 로 보고. 도구 설명(`tools/list`)에 엣지 허용 (from,to)
  매트릭스를 명시. (`api/fragment.py`, `vinc_mcp/rpc.py`)
- **fix(tools)**: vinc_search — **검색 인덱스 부재 시 조용히 실패하던 문제** 대응. 자동 인덱스
  빌드 시도 + Concept/Record 제목 검색 폴백(`node_hits`) + 명시적 `error` 신호.
- **feat(tools)**: 신규 **`vinc_delete`**(노드 삭제) · **`vinc_merge_nodes`**(중복 concept 병합)
  — 오염 정리용 비가역 도구.

## v0.1.6 — 2026-07-15

- **fix(app)**: **사이드카를 고정 8787 대신 동적 포트로 기동** — 앱을 비정상 종료(강제
  종료·크래시)해 옛 사이드카가 8787 을 물고 있으면, 재실행 시 새 사이드카가 bind 실패하고
  앱이 그 **옛(구버전) 사이드카에 붙어** 신기능이 빠진 채 오작동하던 문제를 근절. 이제
  `pick_free_port()` 가 스폰 직전 OS 배정 빈 포트를 잡아 사이드카에 넘기고, 그 포트를
  `sidecar_conn`/폴백으로 프론트에 전달 → 옛 프로세스가 남아 있어도 충돌하지 않는다.
  (증상: v0.1.5 에서 OPTIONS 프리플라이트가 501 → 적재 "Failed to fetch"; 실은 8787 을 문
  구버전 사이드카였음.) (`app/src-tauri/src/main.rs`, `app/src/api.ts`)

## v0.1.5 — 2026-07-14

- **feat(app)**: Settings→MCP 에 **로컬 Claude 연결 버튼**(Claude Desktop / Claude Code).
  main.rs `mcp_install` Tauri 커맨드가 번들 사이드카 `mcp install --client <desktop|code>` 를
  실행해 클라이언트 설정에 Vinc(stdio MCP)를 등록 → 재시작하면 Claude 가 로컬 그래프를
  읽고/쓴다("내 문서→그래프"의 실제 경로). 그동안 Settings 의 MCP 는 안내 텍스트뿐이었음.
  (`app/src-tauri/src/main.rs`, `app/src/surfaces/settings.ts`)
- 참고: 원격 커넥터(`mcp.vincs.app/mcp`)는 서버 가동·OAuth 디스커버리 200 확인 — claude.ai
  커스텀 커넥터 등록(발주자 1회)만 남음.

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

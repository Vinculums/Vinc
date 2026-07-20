# Changelog

Vinc 데스크톱 앱 릴리스 기록. 사용자 노출 카피 정본은 `site/release/`(웹 릴리스 노트)이며,
이 파일은 개발자용 요약이다.

## v0.5.4 — 계정 동기화 pull 수정 + fragment 배치 크래시 방어 (2026-07-21)

> 계정 키로 로그인 후 동기화해도 원격 데이터가 안 불러와지던 문제의 근본원인을
> 고쳤다(호스티드 허브 쪽 버그 — 허브 서버는 이미 핫픽스 배포 완료, 이 릴리스는
> 데스크탑 앱에 같은 코드베이스의 수정을 반영한다).

- **계정 동기화 pull — CRITICAL**: 허브의 `/sync/pull-delta` 핸들러가 로컬 기기
  전용(owner 미분리) 메타키에 걸려 **모든 회원의 pull 이 매번 결정론적으로 빈
  데이터**였다(간헐적 아님). owner 의 실제 도메인을 명시로 넘기도록 수정 —
  허브(`mcp.vincs.app`)엔 이미 핫픽스 배포·검증 완료, 이 릴리스로 데스크탑
  코드베이스도 동일하게 맞춘다(로컬 사이드카는 이 경로를 직접 쓰진 않지만 같은
  파일이라 일관성 유지).
- **ingest 배치 크래시 방어**: `vinc_ingest`(MCP)로 Bridge 노드에 `domain` 필드를
  같이 보내면(Bridge 는 그 컬럼이 없음) 배치 전체(다른 노드+모든 엣지 포함)가
  통째로 실패하던 버그 수정 — 이제 그 노드만 경고로 스킵하고 나머지는 정상 처리.

## v0.5.3 — CRITICAL: 그래프 스토어 미기동 수정 + kuzu 제거 (2026-07-21)

> **v0.5.1/v0.5.2 사용자는 즉시 업데이트 필요.** 사이드카가 그래프 스토어를
> **전혀 열지 못하는** 회귀가 있었다(`/health` → "no reachable store") — 설치는
> 되지만 읽기·쓰기가 모두 조용히 실패했다.

- **원인(실측 확정)**: ladybug 는 네이티브 서브모듈(`._lbug` pybind, `._lbug_capi`
  ctypes)을 `importlib.import_module("._lbug", __package__)` 로 **동적** 로드한다
  (`ladybug/_backend.py`). PyInstaller 의 정적 AST 분석은 동적 import 를 못 보므로
  `hiddenimports=["ladybug"]` 만으로는 두 서브모듈이 빌드에서 통째로 빠졌다.
  `import ladybug` 는 성공하지만 `ladybug.Database()` 호출이
  `ModuleNotFoundError: No module named 'ladybug._lbug_capi'` 로 실패 →
  `EmbeddedBackend.open` 이 `except Exception: return None` 으로 조용히 삼켜 스토어가
  안 열렸다. 재현·수정 모두 로컬 PyInstaller 빌드로 실측(frozen exe 안에서
  Database open→schema→write→read 성공 확인).
- **수정**: `packaging/sidecar.spec` 이 `collect_all("ladybug")` 로 pkgutil 기반
  서브모듈·네이티브 바이너리 수집(정적분석 의존 없음) — Windows 실제 프로덕션 빌드로
  재검증(`import`·`Database`·스키마·쓰기·읽기·G0 확장 전부 성공, `/health`="ok").
- **CI 스모크 테스트 강화**: 이전엔 `--help` 만 실행해 "빌드 성공"만 확인했다(그래서
  이 회귀가 CI 초록으로 통과·발행됐다). 이제 3-OS 매트릭스 전부에서 실제 `serve` 를
  띄우고 `/health`="ok" 를 확인한다 — macOS·Linux 도 같은 동적-import 코드 경로라
  동일 영향 범위이므로, 이 테스트가 플랫폼별 실증을 대신한다.
- **kuzu 완전 제거**: 테스트 기간·기존 사용자 없음 확인(발주자) → 듀얼드라이버
  (마커 파일·자동감지·안전 이관 레일)를 전부 폐기하고 **ladybug 단일 무조건 호출**로
  단순화(`_import_driver`/`EmbeddedBackend.open`/`reader_view`). `store/migrate.py`
  는 범용 논리 export/import 도구로 남기고 전환-레일 프레이밍만 제거. 회귀 없음
  (git stash A/B 비교: 리팩토링 전후 동일 11건 실패·415건 통과 — 전부 기존
  [[windows-embedded-test-fails]] 환경 이슈, 무관).

## v0.5.2 — 사이드카 파일락·프로세스 누수 수정 (2026-07-20)

> 설치·자동업데이트 중 "다음 파일을 열 수 없습니다: …\vinc-sidecar.exe" → '무시'해야
> 진행되던 문제와, 사이드카(`vinc-sidecar.exe`)가 실행마다 누적(관측 7개)되던 누수를
> 근본 수정. 원인은 Defender 오탐이 아니라 **실행 중 사이드카가 .exe 이미지를 물고 있어
> 생긴 파일 락** + 종료·크래시 시 회수 실패.

- **설치 파일락 해제 — NSIS preinstall 훅**: 덮어쓰기 직전 잔존 사이드카 트리를
  `taskkill /F /T /IM vinc-sidecar.exe` 로 종료(락 해제). `bundle.windows.nsis.installerHooks`
  → `windows/hooks.nsh`(PREINSTALL·PREUNINSTALL). 설치 땐 MCP 브리지도 같은 이미지라
  함께 정리(필수·자동 재기동). (`tauri.conf.json`, `windows/hooks.nsh`)
- **종료 시 트리-kill(Windows)**: `RunEvent::Exit` 에서 `taskkill /F /T /PID <child>` 로
  PyInstaller onefile 부트로더+파이썬 자식을 트리째 회수 — 부트로더만 죽고 자식이
  고아로 .exe 락을 유지하던 경로 차단. (`src/main.rs`)
- **중복 인스턴스 차단 — single-instance 플러그인**: 두 번째 실행은 기존 창 포커스 후
  즉시 종료 → 사이드카 중복 spawn 원천 차단. 가장 먼저 등록. (`Cargo.toml`, `src/main.rs`)
- **`--parent-pid` heartbeat 배선 + Windows-안전화**: 셸 PID 를 사이드카에 넘겨 셸이
  크래시·강제종료로 죽으면(=`RunEvent::Exit` 미발생) 사이드카가 ~1s 내 자가종료(E6).
  `_heartbeat` 의 부모 생존 확인을 **`os.kill(pid,0)`(Windows 에서 파괴적) → 조회 전용
  `OpenProcess(SYNCHRONIZE)+WaitForSingleObject`** 로 교체(`_pid_alive`, POSIX 는 종전 유지).
  실측 검증: 부모 생존 시 오종료 없음·부모 사망 시 감지. (`src/main.rs`, `api/server.py`)
- 시작 시 이미지명 일괄 회수(`/IM`)는 **하지 않는다** — Claude 클라이언트에 붙은 브리지
  (`vinc-sidecar.exe mcp serve`)를 오살상하기 때문(적대적 리뷰로 확인·회피). 누수는 위
  4중 방어로 대신 막고 구버전 잔존 고아는 설치 시 훅이 정리.
- **동반 — 계정 @username 핸들 표시(009)**: 로그인 계정을 Settings→Account 에서
  `@username`(없으면 이메일)으로 표시. 디바이스 플로우 콜백/상태가 username 을 함께
  실어 나르고(server, ≤20자 클램프·표시 전용), 웹은 `/api/me`. 미설정 계정은 이메일 폴백.
  (`api/server.py`, `app/src/surfaces/settings.ts`, `tests/test_server.py`)

## v0.5.1 — UI 오버홀·데스크탑 아이덴티티·LadybugDB 전환 (2026-07-20)

> **이 릴리스의 사이드카는 LadybugDB 전용 빌드로 발행한다**(release.yml
> `VINC_SIDECAR_DRIVER=ladybug`). 테스트 기간·기존 사용자 없음(발주자 확인)이라
> kuzu→ladybug 강제 이관 이슈 없이 신규 설치부터 ladybug. G0 확장 오프라인 동봉.
> (v0.4.x UI 오버홀·아이콘·프레임리스 타이틀바도 이 릴리스에 첫 포함.)

- **Packs 폭 수정**: 카드 `max-width` 제거 → surface 전폭(절반만 차던 문제 해소).
- **사이드카 PyInstaller 오탐 완화(§9)**: Windows 버전 리소스(`version_info`) 추가 —
  제품/회사/버전 메타데이터로 Defender 휴리스틱 오탐 표면 축소. `upx=False` 유지.
  비-Windows 무시. (근본 해결 = OS 코드서명, 인증서 확보 후속.)
- **G0 — LadybugDB 오프라인 확장 번들 실증·구현(2026-07-20)**: ladybug 확장(vector/
  fts/algo)은 런타임 다운로드형이라 오프라인 불변식(§1)을 깬다. **실측 확정**: 캐시
  없이 `LOAD EXTENSION <name>` 은 실패하나 **`LOAD EXTENSION '<번들 경로>'` 는
  네트워크 없이 로드 성공**(이후 벡터/FTS 인덱스 정상). → `_load_ext` 가 번들된
  `.lbug_extension` 을 명시 경로로 로드(`_bundled_ext_path`), spec 은
  `VINC_SIDECAR_DRIVER=ladybug` 변형에서 datas 로 동봉. **기본은 여전히 kuzu(옵트인
  전환).** 회귀 가드 `tests/test_ladybug_g0.py`. (`store/embedded_backend.py`,
  `store/config.py`, `packaging/sidecar.spec`, `.github/workflows/release.yml`)
- **LadybugDB 전환 3중 실증 + 안전 전환 기반(2026-07-20)**: py3.13+ladybug 0.18.2 실측 —
  ① **앱 완전 호환**(전체 스위트 ladybug 실패 집합 = kuzu 베이스라인과 동일, 신규 실패 0)
  ② **G0 오프라인**(위) ③ **교차 이관 무손실**(kuzu export→ladybug import 데이터 일치). →
  **안전 전환 규율** `_resolve_driver`: 마커(`<store>.driver`) 기반 — 기존 kuzu 스토어는
  kuzu 로(레거시 보호·강제 이관 없음), **신규 설치는 ladybug**. kuzu 전용 빌드에선 무동작
  (전방호환). `reader_view` 를 열린 드라이버와 일치. spec `VINC_SIDECAR_DRIVER=both`
  (kuzu+ladybug 동봉)로 안전 전환 사이드카 빌드. 기존 테스트 무회귀.

## v0.5.0 — 그래프 RAG 고도화 + LadybugDB 전환 레일 (2026-07-19)

그래프 RAG 리서치 교차 분석(`RESEARCH-GRAPHRAG-2026-07`)의 발전 과제 E-G1~G7 을
전면 구현하고, LadybugDB 전환 레일(T1·T2·T4·T5)을 안착시켰다. 스키마
`SCHEMA_VERSION=3`(기동 시 자동 마이그레이션 — 하위호환), 실엔진 검증 완료.
전 항목 백엔드 중립(GraphStore 추상화 뒤) · 반입 금지(외부 왕복 0·LLM 추론 0) ·
결정론 · 제안-재가 · owner 스코프(SEC-1) 불변 준수.

- **그래프-검색 접속(E-G1) — 이부 그래프로 다중 홉 가교 해소**: `MENTIONS(Chunk→
  Concept)` rel 테이블 신설 + 결정론 링커(`engines/mentions_link.py` — source
  basename·title 토큰 AND·stem 매칭, LLM 없음). `search()` 에 그래프 암 추가 —
  상위 Chunk 의 MENTIONS 이웃 개념 + 1-hop 을 title 폴백과 RRF 융합(`node_hits`
  증강, `arms` 매치 표기). **질문과 title 매치가 없는 개념도 관련 청크가 언급하면
  검색에 등장한다**(HippoRAG 이부 그래프의 결정론 번역). embed 잡에 링크 편입.
  (`store/embedded_schema.py`, `engines/mentions_link.py`·`embed_pipeline.py`,
  `api/query.py`)
- **ego-Personalized PageRank(E-G2) — 시드 편향 랭킹**: `ppr_scores()` 결정론
  순수 함수(반복 고정, 대칭 RWR + 엣지 타입 가중 — Bridge CONNECTS·방향 관계
  우대, 댕글링 재분배). `vinc_graph(seed=)` ego 뷰·`decide` 노드에 `ppr` 부여
  (가산 메타). (`api/query.py`)
- **구조 진단 위임(E-G3) — WCC·Louvain**: `graph_algo()` 어댑터가 algo 확장
  (kuzu 정적 링크)에 project_graph 멱등 생성→실행→드랍으로 위임. `component_
  diagnosis`(고아·컴포넌트 "연결 안 된 섬")·`communities`(Louvain 군집+지배도메인,
  테마 proposed 제안 근거) 조회 전용. MCP `vinc_diagnose` + `/diagnose`.
  (`store/embedded_backend.py`, `api/query.py`, `api/server.py`, `vinc_mcp/tools.py`)
- **ER 병합 후보 제안(E-G4) — 자동 병합은 영구 금지(T1)**: `merge_candidates()`
  결정론 순수 함수(정규 타이틀 완전일치·토큰 자카드·부분집합, 동일도메인 우선).
  조회 전용 — 확정은 기존 `vinc_merge_nodes` 경유. MCP `vinc_merge_candidates` +
  `/merge-candidates`. 중복 개념 오염 예방 표면. (`api/query.py`, 서버·MCP)
- **관측 창구·증거·경로(E-G5/G6/G7)**: ① `vinc_neighbors` — 개념의 1-hop 관계망
  관측(SoG Observe-then-Navigate 경량 프리미티브, 토큰 절감) ② CC 4종+ABOUT rel
  테이블에 `evidence`/`confidence` 선택 속성(스키마 v3 ALTER, fragment 계약 하위
  호환 수용) ③ `vinc_path` — 두 개념 간 `* ALL SHORTEST` 경로 증거 체인(분산 증거
  단편화 해소). (`store/embedded_schema.py`, `api/fragment.py`·`query.py`·`server.py`,
  `vinc_mcp/tools.py`)
- **LadybugDB 전환 레일(T1·T2·T4·T5) — G0 게이트까지 kuzu 기본 유지**: 저장 포맷
  비호환(ladybug 가 kuzu DB 파일 열기 불가 — 실측)이라 전환은 논리 이관만 가능.
  `store/migrate.py` 드라이버 무관 export/import(스키마 SSoT 파생 — 추적열·
  preference 신호·evidence·embedding 전량 보존, 두 프로세스: kuzu export→ladybug
  import) + `vinc-migrate` CLI. 무손실 왕복 리허설 + 방언 재검증 매트릭스(M3
  TRAIL·M4 label(r)·UNION ALL·ALL SHORTEST) 테스트. 드라이버 노브 D14 재평가
  주석(G0 오프라인 확장 번들 게이트 전제). (`store/migrate.py`·`config.py`·
  `embedded_schema.py`, `tests/test_migrate_driver.py`)
- MCP 도구 4종 추가(`vinc_diagnose`·`vinc_merge_candidates`·`vinc_neighbors`·
  `vinc_path`, 전부 조회 전용 read-only annotations). 테스트 418 passed.

## 미출시 (Unreleased) — main, v0.4.0 이후

- **엣지 툼스톤(S4 확장) — 연결선 삭제가 기기 간 전파된다**: 엣지는 타임스탬프가
  없어 하드삭제만으론 상대 기기 사본이 다음 동기화에 부활하던 공백을 닫았다.
  ① 삭제 표면 신설 — `vinc_delete_edge` MCP 도구 + `POST /delete-edge`(동기화
  rel 5종: CC 4종+about, RELATED_TO 는 무방향이라 양방향 삭제, SEC-3 owner 스코프)
  ② `_EdgeTombstone` 표식(부팅 시 멱등 생성) → delta 번들 `edge_tombstones` 로
  전파·적용·재전파, 90일 GC 동참 ③ **대칭 수리**: 신규/재생성 엣지는 양 끝 노드를
  revstamp — 기존 노드 사이 엣지 추가가 증분 동기화에 영영 안 실리던 선행 공백도
  함께 해소(재생성이 낡은 삭제 표식을 상쇄 — '재생성이 삭제를 이긴다' 의미론).
  sync apply 경로는 스탬프 억제(에코 차단). (`store/embedded_schema.py`,
  `api/query.py` delete_edge, `api/fragment.py`, `api/sync.py`,
  `vinc_mcp/tools.py`·`http_server.py`)
- **계정 연결 = 로그인 승인으로(키 복붙 종식)**: Settings 의 계정 키 입력을
  [계정 연결] 버튼으로 대체 — 시스템 브라우저에서 vincs.app/connect-app 로그인·
  승인만 하면 발급된 회원 키가 사이드카 루프백 메일박스(state 단일사용 게이트,
  RFC 8252 변형)로 자동 전달된다. 앱은 계정 API 를 호출하지 않는다(B1 유지 —
  무계정 기본 D7 불변). 수동 키 입력·작업 공간(owner)은 고급(Advanced)으로 이동.
  루프백 도달 실패 시 페이지가 키 1회 노출 수동 폴백. (`api/server.py`
  /connect/start·callback·status, `app/src/surfaces/settings.ts`, 사이트
  `connect-app/` — Vinc-homepage)

## v0.4.0 — 그래프 구조 수리 9건 (2026-07-19)

`vinc.d.graph-structure-analysis` 냉철 분석(F1–F9)의 전면 수리. 스키마
`SCHEMA_VERSION=2`(기동 시 자동 마이그레이션 — 하위호환), 실엔진 검증 완료.

- **개방층(Entity/TYPED) 재활성화 — 라이브 포팅(F1)**: 휴면이던 D11-R2 사용자
  저작 타입 적재를 `api/fragment.py`(단일 쓰기 표면)에 포팅 — 미지 노드 타입은
  skip 이 아니라 Entity 캐리어로 영속, 양끝 개방 타입 엣지는 TYPED{type}, 어휘
  proposed 편입 + `proposed_types` 응답. revstamp(동기화)·SEC-3·dedupe 승계.
  MCP `vinc_ingest` 스키마 type enum 제거, `theme_id`(선택) 전달. 미지 접두사
  시드(`theory:x`)의 Entity 프로브·전용 L2 순회. (`api/fragment.py`,
  `vinc_mcp/tools.py`·`rpc.py`, `api/query.py`)
- **Atlas Concept 단일 축 해소(F2)**: 축 플랜 3종(Bridge CONNECTS·Episode
  ABOUT·Record ATTACHES_TO)이 L1/L2 에 합류 — 브릿지 매개 개념이 엣지 없이 떠
  있던 '보이지 않는 접착제' 제거(매개자 keep 편입), 팔면체·육각기둥 마크가
  실데이터로 렌더. (`api/query.py`, `app/src/surfaces/atlas.ts`)
- **degree 정직화(F3)**: CONNECTS/ABOUT/ATTACHES_TO/TYPED 전부 양끝 카운트 —
  브릿지 팔만 가진 개념이 '고아'로 잇기 제안을 받던 모순 제거. 상세 레일에
  부착 카운트 표기.
- **ABOUT(Episode→Concept) 관계 신설(F4)**: episodic↔semantic 다리 — fragment
  `about` 엣지 + 동기화 export 편입(`_episode_about_plan`).
- **Bridge 소유 편입(F5, 스키마 v2)**: `owner_id` 열 + purge 자동 커버 +
  ties/coupling/seed/merge 재지향 owner 스코프 — 호스티드 L0 교차테넌트 집계
  차단. 레거시 행은 `''` 과도기 술어(`_bridge_owner_pred`), bridges_ingest
  재실행이 백필 경로.
- **위키링크 보수(F6/F8)**: 방향 관계(PREREQUISITE_OF·DERIVES_FROM)는
  frontmatter 명시만, 키워드 추론은 RELATED_TO 강등 + 정준화·역방향 dedupe
  (fragment 정책 일치). (`engines/concepts_ingest.py`)
- **connects_via role(F9)**: fragment 경로도 선택 `role` 지원(bridges_ingest
  MERGE 의미론 동형).
- props 경계 명문화(F7)·엣지 톰스톤 부재 등 잔여 한계는 `vinc.c.dataplane` v0.4
  등재.

### 함께 출시 — main, v0.3.1 이후 (2026-07-18 기준)

v0.3.1 태그(`18d7231`) 이후 `main` 에 올라간 작업. 웹 Vinc·커뮤니티·엣지
하드닝은 이미 vincs.app 에 라이브지만, 데스크톱 설치본에는 v0.4.0 이 첫 포함이다.
사용자 노출 카피 정본은 여전히 `site/release/`.

- **MCP 커넥터 제거**: Settings→MCP 에 [Desktop에서 제거]/[Code에서 제거] 버튼 —
  그동안 install 만 있고 uninstall 이 없었다. 셸 `mcp_uninstall` Tauri 커맨드 →
  번들 사이드카 `mcp uninstall --client desktop|code`. Desktop 은
  `claude_desktop_config.json` 을 **백업 후 `mcpServers.vinc` 키만** 제거(나머지
  보존, 항목 없으면 `removed:false` 로 멱등), Code 는 `claude mcp remove --scope
  user vinc`. (`vinc_mcp/install.py`, `api/cli.py`, `app/src-tauri/src/main.rs`,
  `app/src/surfaces/settings.ts`, `app/src/copy.ts`, b9b1cc3)
- **SEC-3 — 호스티드 쓰기 owner 격리**: 읽기(SEC-1/2)에 이어 **쓰기 경로**
  (delete·merge·pin·tag·ingest)도 owner 스코프로 강제한다 — 모든 Concept MATCH 에
  `owner_id` 술어. MCP `call()` 이 원격 타깃에서 클라이언트가 실어 보낸 owner 를
  `forced_owner` 로 덮어써 교차테넌트 쓰기를 봉쇄(로컬 stdio 는 단일 유저라
  무스코프 유지). 실엔진 2-owner 테스트로 검증. (`api/fragment.py`,
  `api/preference.py`, `api/query.py`, `vinc_mcp/tools.py`,
  `tests/test_write_tenancy.py`, f8942ed·cf8abb4)
- **MCP 쓰기 도구 3종 정착**: 라이브 MCP 표면은 도구 9종 — 읽기 6종
  (`vinc_search`·`vinc_recall`·`vinc_graph`·`vinc_decide`·`vinc_domains`·
  `vinc_suggest_ties`), 쓰기 3종(`vinc_ingest` + SEC-3 이 추가한 `vinc_delete`
  [DETACH DELETE]·`vinc_merge_nodes`[concept fold], 둘 다 owner 스코프 비가역
  정리 도구). (`vinc_mcp/tools.py`)
- **웹 Vinc (vincs.app/app/) 라이브**: 데스크톱 앱 프론트의 **브라우저 빌드**.
  인증 = 계정 세션쿠키(`__Host-session`) → 계정 `/api/graph` 프록시 → 서명
  Ed25519 `typ:"proxy"` 어서션 → 허브 `/internal/graph-owner`(owner = 세션 sub
  강제, B2 불변식: 공개키만 공유). 완전 쓰기 패리티(delete/merge/pin/tag/ingest).
  `ensureWebAuth` 부팅 게이트가 인증을 먼저 확인(`__Host-csrf` 동기 확인 or
  `/api/me`)해 로그아웃 시 즉시 `/login`(앱 깜빡임 제거·왕복 0). `vite base` 를
  `VITE_BASE` env 로 빼 **웹 빌드만** `/app/`. FORB '반입 금지'·'외부 왕복 없음'
  불변식은 네이티브 데스크톱 경계에만 해당하며, 웹 빌드는 계정 세션으로
  인증한다. (9bdd544·c7487a3·76679cd·b5010da·5868e28·10e1c6c)
- **커뮤니티 공유 (vincs.app/community/) 라이브**: 웹 Vinc Packs 표면에
  [커뮤니티에 공유] — 동의 게이트 게시 시트가 share 팩(= MCP fragment 동형)을
  페이로드로 공개 갤러리에 게시(owner 는 세션 강제). 계정 백엔드 `api.community` +
  `community_packs`(마이그레이션 008) + `packSummary`(도메인 분포·교차도메인%·
  top 태그·엣지 타입 시각요약). 즉시 공개 + 경량 안전 모더레이션. (bf6360f)
- **엣지 하드닝**: `deploy/Caddyfile` 을 배포 정본과 1:1 동기화 — 캐시(해시에셋
  `immutable` 1y, HTML `no-cache`, `/api`·`/internal` `no-store`), `encode zstd
  gzip`, 보안헤더(HSTS `max-age=1y includeSubDomains`, CSP `frame-ancestors
  'none'`, `X-Frame-Options DENY`, `X-Content-Type-Options nosniff`,
  `Referrer-Policy no-referrer`), `mcp.vincs.app` 의 `/internal/*` 공개 404(루프백
  8790 전용), `/api/*` → 계정 Hono 8080. (`deploy/Caddyfile`, 1710b07)
- **build(app)**: macOS 코드서명·공증 CI 스캐폴딩(env 훅) — **별개 마일스톤**으로
  표기, 아직 실제 서명 발효 전(v0.3.1 의 minisign 업데이터 서명과는 별개). (65e8479)

## v0.3.1 — 2026-07-18

자동 업데이트 릴리스. **설치된 앱이 스스로 새 버전을 받는다** — 지금까지는 매번
사이트에서 설치본을 다시 받아야 했다. 이 버전부터는 앱이 기동 시 조용히 확인하고,
새 버전이 있으면 하단에 알림을 띄운다. [설치하고 재시작] 한 번이면 끝.

- **Tauri v2 업데이터**: 기동 직후 비차단 확인. 있을 때만 하단 토스트(버전 안내 +
  [나중에]/[설치하고 재시작]). 확인 실패(오프라인·매니페스트 부재)는 무음 —
  앱 사용을 절대 막지 않는다. 웹 Vinc·브라우저 dev 에서는 no-op.
- **서명 강제**: 다운로드 번들은 Ed25519(minisign) 서명을 통과해야 설치된다.
  공개키는 앱에 박혀 있고(tauri.conf.json), 개인키는 CI 시크릿에만 있다 —
  서명 없는/변조된 번들은 클라이언트가 거부한다(중간자 방지).
- **CI 파이프라인**: 릴리스 빌드가 3 OS 업데이터 아티팩트를 서명하고
  `latest.json` 매니페스트를 조립해 draft 릴리스에 함께 올린다. 개인키 시크릿이
  없으면 빌드가 실패한다(무서명 릴리스 차단, fail-closed).
- **이관 주의**: 자동 업데이트 클라이언트는 이 버전부터 탑재된다. 이전 버전
  (≤v0.3.0) 사용자는 **한 번만** 수동으로 이 버전을 받으면, 이후로는 자동이다.

## v0.3.0 — 2026-07-18

계정 그래프 동기화 릴리스. 노트북과 웹·다른 기기가 **같은 지식**을 보게 된다 —
노트북이 꺼져 있어도. 오프라인 우선은 그대로: **고른 도메인만** 올라가고, 안 고른
것은 기기를 떠나지 않는다. 커넥터는 여전히 1개(원격), 앱 Atlas 는 로컬을 렌더한다.

- **양방향 동기화**: Settings → "계정에 동기화" 에서 도메인을 고르고 [지금 동기화].
  로컬↔계정 그래프가 delta 로 오간다. 회원 키(vinc_sk_)는 이 기기에만 저장된다.
- **필드단위 3-way 병합**: 노트북과 웹에서 **서로 다른 필드**를 고치면 둘 다 보존한다.
  **같은 필드**를 양쪽에서 고쳤을 때만 최신 편집이 이긴다. 노드 통째 최신승이 아니라
  마지막 동기화 기준본(base) 대비 필드별로 병합한다.
- **스키마 제자리 마이그레이션**: 기존 설치의 스토어에 수정시각·버전 열을 추가한다
  (ALTER+백필) — 데이터 손실 없이. 재기동에도 안전(멱등).
- **변경추적**: 모든 쓰기가 실제 변경일 때만 updated_at(HLC)·rev 를 찍는다 — 멱등
  재적재는 무스탬프라 가짜 충돌이 없다. recall(access 빈도)은 동기화 대상이 아니다.
- **owner 격리 강화**(SEC-1·SEC-2): search·graph·decide·share/export 전 읽기 경로가
  owner 스코프를 강제한다 — 호스팅 멀티테넌트에서 남의 지식이 새지 않는다.
- **MCP 디렉토리 준비**: 도구 안전 주석(readOnly/destructive), 브라우저 CORS,
  도구 결과 크기 상한, 개인정보 처리방침·지원 채널.
- **계정 삭제 → 그래프 삭제**: 계정을 지우면 호스팅 지식 그래프도 서명된 요청으로
  함께 삭제된다(확인 실패 시 계정 미삭제, fail-closed).

## v0.2.0 — 2026-07-17

회원 커넥터 온보딩 릴리스. 커넥터를 붙일 때마다 **관리 키를 복붙**하던 과정을
끝내고, 공홈에 로그인한 회원이 스스로 연결하도록 바꿨다. 원격 그래프는 회원별로
분리되고, 무료/Pro 티어 한도가 생겼다. 데스크톱 앱 자체 동작은 v0.1.9와 같다
(변경은 사이드카에 실리는 MCP 어댑터 쪽).

- **회원 로그인 승인 흐름**: claude.ai 커넥터가 `mcp.vincs.app` 을 치면 MCP 가
  서명한 요청을 `vincs.app/connect` 로 넘기고, 로그인 회원이 **서버가 검증한**
  앱 이름·복귀 주소를 확인한 뒤 승인한다. 관리 키가 등장하지 않는다.
- **회원 API 키**: 공홈 계정에서 `vinc_sk_*` 를 발급해 Bearer 로 쓸 수 있다
  (데스크톱 원격 타깃·bearer 지원 클라이언트용). 키당 마지막 사용 시각 표시.
- **회원별 그래프 분리**: 원격 인증경로가 해석한 소유자를 도구 호출에 강제
  스탬핑한다 — 클라이언트가 남의 `owner` 를 실어 보내도 덮어써진다.
- **재기동에도 커넥터 유지**: OAuth 상태를 인메모리 dict → SQLite(WAL) 로 옮겼다.
  이 버전 배포 시 **1회에 한해** 기존 커넥터가 재인가를 요구하고, 이후로는 없다.
- **티어 쿼터**: 무료 하루 15회 도구 호출 → 초과 시 12시간 잠금, Pro 는 높은 상한.
  initialize/tools/list/ping 등 핸드셰이크는 무료라 커넥터가 죽지 않는다.
- **크로스서비스 신뢰**: 계정↔MCP 는 서로의 **공개키만** 핀한다(시크릿·DB 공유 0).
  MCP 는 stdlib 만 쓰므로 Ed25519(RFC 8032)를 벤더링했고, Node 네이티브 구현과
  바이트 단위로 상호 검증되는 교차 벡터를 양측 테스트에 고정했다.

## v0.1.9 — 2026-07-17

연결 계층 신뢰성 릴리스. 다리(원격↔로컬) 검증 중 드러난 구조 결함들을 잡았다 —
MCP 브리지가 고정 8787 로 자기 사이드카를 따로 띄워 앱과 같은 `~/.vinc` 를 두
프로세스가 여는(데이터 상호 불가시·좀비·손상 위험) 문제가 핵심.

- **fix(app·mcp)**: **세션 핸드셰이크** — 앱이 기동 시 `~/.vinc/session.json`
  `{port, token, pid}` 을 원자적 기록(종료 시 삭제)하고, MCP 브리지가 이를 읽어
  **앱 사이드카에 attach** 한다(자기 사이드카를 8787 에 따로 spawn 하지 않음).
  세션이 없거나 stale 이면 **빈 포트 선점 + `--parent-pid`** 로 자기 spawn(고정
  8787·좀비 폐기 — 어댑터가 죽으면 사이드카 자동 정리). (`app/src-tauri/src/main.rs`,
  `vinc_mcp/bridge.py`)
- **feat(mcp)**: **데스크탑 커넥터 1개 통합** — 도구 9종에 `target`(`local`|`remote`,
  기본 local) 파라미터. `remote` 는 `mcp.vincs.app/mcp` 로 JSON-RPC 프록시(신규
  `vinc_mcp/remote.py`, stdlib urllib). Claude 가 `target` 만 바꿔 원격↔로컬 다리를
  놓는다. 원격 인증은 env `VINC_REMOTE_MCP_TOKEN`(v0.2.0 회원 발급 키), owner 는
  원격이 토큰으로 정하므로 전송하지 않는다(멀티테넌트 안전). (`vinc_mcp/tools.py`,
  `vinc_mcp/remote.py`)
- **fix(api)**: `vinc_search` 노드 폴백을 **토큰 AND** 로 — 예전엔 쿼리 전체를 통짜
  substring 매치해 "agentic automation orchestration" 처럼 단어가 여러 필드에 흩어지면
  0건이었다. 이제 공백 토큰화(상한 8)해 모든 토큰이 어느 필드(title/one_liner/props)든
  포함되면 매치(단일 토큰은 하위호환). (`api/query.py`)
- **fix(api)**: `vinc_graph` 가 노드의 **props·tags 를 반환** — 다리로 복사할 때 요약이
  유실돼 `vinc_decide`(49컬럼 토큰 폭발)를 우회해야 했다. props 는 dict 로 파싱해 바로
  되먹일 수 있다. (`api/query.py`)
- **fix(api)**: `vinc_decide` **응답 정규화** — 경로 순회의 49컬럼 합집합·시드 중복
  직렬화(토큰 폭발)를 걷어내고, 검증된 하위 planner 를 조합해 `{nodes, edges,
  conflicts}`(MCP-INTERFACE §4 계약)로 축약한다. conflicts = 크로스도메인 엣지 투영.
  (`api/query.py`)
- 참고: 원격 인증(`VINC_REMOTE_MCP_TOKEN`)은 v0.1.9 에선 MCP 클라이언트 설정의 env 로
  수동 지정한다 — 회원 로그인 기반 커넥터 발급·자동 유지는 **v0.2.0**. 앱 Settings 에
  원격 필드를 두지 않은 건 target=remote 가 MCP 어댑터 계층(앱 사이드카 아님)에서
  처리되어 앱 UI 와 무관하기 때문이다.

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

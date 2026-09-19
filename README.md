# Vinc

**설치 없이 브라우저에서 되는 유일한 지식 그래프.** 원문을 정본으로 보관해 모든
개념·관계에 근거 문장과 원문 오프셋이 붙는다. 무설치 웹(`vincs.io/app`)과 설치형
데스크톱은 별개 제품이 아니라 **같은 프론트엔드·같은 그래프 계약**을 공유하는 한
도구의 두 표면이다 — 웹은 계정 세션으로 호스티드 그래프를, 데스크톱은 로컬
사이드카로 이 기기의 그래프를 같은 화면·같은 조작으로 다룬다.

데스크톱 쪽은 오프라인 우선이다: 원문·문서를 적재(ingest) → 그래프·벡터로 구조화 →
탐색·검색·패턴 추출 → 팩으로 내보내는 전 과정이 외부 인프라·API 키·서버 없이 설치
직후 동작한다. 회원은 선택적으로 공홈 계정에 로그인해 고른 도메인을 클라우드와
동기화하거나(웹·다른 기기와 공유) 지식팩을 커뮤니티에 공개할 수 있다.

> 제품명 Vinc — 라틴어 *vinculum*(결속·연결 고리). 데이터 마이닝 도구이며
> "기억/학습" 서사를 사용하지 않는다.

**현재 v0.8.7 라이브** — GitHub Releases + [vincs.io](https://vincs.io) 다운로드
(구 도메인 `vincs.app` 의 A 레코드는 삭제됐다 — 접속 경로는 vincs.io 하나이고,
그 도메인에 남은 것은 발신 메일 레코드뿐이다).
개발자용 릴리스 요약은 [CHANGELOG.md](CHANGELOG.md).

## 정본 (SSoT)

**정본은 계정 그래프다.** 아래는 파일 경로가 아니라 `doc_id` 이고, `vinc_doc_get`
(MCP) 또는 `vinc_search` 로 읽는다. Vinc 는 자기 지식을 자기 제품에 담는다.

| 무엇 | doc_id |
|---|---|
| 용어·표기·금지 어휘 | `vinc.canon.terminology` |
| 아키텍처 불변식 · 임베디드 백엔드 계약 | `vinc.canon.principles` |
| 아키텍처 as-built(두 빌드, 한 계약) | `vinc.spec.architecture` |
| 웹·계정 as-built | `vinc.spec.web-system` |
| 문서 정본(Chunk=SSoT, D45) | `vinc.canon.chunk-ssot` |
| MCP 인터페이스·도구 계약 | `vinc.canon.mcp-contract` |
| 그래프 쓰기 관례(GRAPH.md) | `vinc.canon.graph-conventions` |
| 결정 등기 | `vinc.canon.decisions` |
| 배포 as-built | `vinc.ops.deployment` |
| 제품 개요·코퍼스 색인 | `vinc.spec.product-overview` |

> 이 표는 2026-08-11 에 다시 썼다. 전에는 여덟 행이 전부 **이 리포 바깥의 파일**을
> 가리켰는데(`weg-9000-docs/...` 와 `Vinculums/Vinc-docs ...`), 그 시점에 **여덟 행
> 모두 실재하지 않았다** — `weg-9000-docs` 는 그런 이름의 리포가 없고, `Vinc-docs` 는
> 폐기 대상이라 세션 규율이 "읽지 마라"고 금지한 리포였다. 제품 README 가 프로젝트
> 자신의 정본 규칙을 어기고 있었다. 같은 날 `Vinc-docs` 를 아카이브하면서 정리했다.

**반입 금지(F1~F7)는 문서가 아니라 실행체다** — `scripts/gate_forbidden.py` 가 유일한
정본이고 0건을 요구한다. 근거와 경계는 `vinc.canon.principles` 에 있다. 발원 문서였던
`weg-9000-docs/WORK-ORDERS/WO-MEM-SA-001.md` 는 이 워크스페이스에 없다.

이행 이전의 구 마크다운(`Vinc-docs`)은 `_migration/` 에 원장과 함께 보존돼 있다.
지식 조회처가 아니라 이력이다 — 찾을 것은 그래프에서 찾는다.

## 아키텍처

두 축으로 구성된다: (1) **네이티브 데스크탑**(오프라인 우선, 기본)과 (2) **호스티드
표면**(vincs.io, 회원 옵트인).

### 네이티브 데스크탑 (전략 B — 임베디드 그래프 스토어)

```
Vinc 데스크톱 앱 (Tauri v2 셸, 제어 = IPC)
  → localhost HTTP (127.0.0.1:8787, 세션 토큰) → Python 사이드카(api/ + engines/)
    → GraphStore 인터페이스 → 임베디드 LadybugDB (Kùzu 계보 — v0.5.3 기본·유일 배포 백엔드)
    + 로컬 임베딩 local-en = bge-base-en-v1.5 (768d, D10)
```

- 데이터 홈: `~/.vinc/{db, packs/, inbox/, backups/}`(D43) — 백업 = 폴더 하나 복사.
- 스토어 백엔드 교체 가능(`store/backend.py` 의 `GraphStore` 추상화 뒤). 임베디드
  드라이버 = ladybug(LadybugDB, Kùzu 계보, v0.18.x) 단일(v0.5.3 — kuzu 제거).
  확장(벡터/FTS/algo)은 런타임 다운로드형이라 오프라인 배포 시 명시 경로로 번들
  (G0, §9 — `store/embedded_backend.py::_bundled_ext_path`).
- 임베딩: 기본 local-en(영어, 오프라인). 다국어 mpnet(`"local"`, 768d)은 legacy
  back-compat 전용, anthropic(voyage-3, 1024d)은 API 키 옵션.
- 자동 업데이트(v0.3.1): Tauri v2 updater 가 기동 시 `latest.json` 매니페스트를
  비차단 확인, minisign(Ed25519) 서명 검증을 통과한 번들만 설치한다. 확인
  실패(오프라인·매니페스트 부재)는 무음이며 앱 사용을 막지 않는다.
- 라이선스 전 구성 MIT/Apache 계열(GPLv3 미포함).

### 호스티드 표면 (vincs.io — 회원 옵트인)

```
Azure Static Web Apps `vinc-site` (Standard, 관리형 TLS)
  ├ vincs.io        → 정적 홈페이지 + 웹 Vinc(/app/) + 커뮤니티(/community/)
  └ /api/*          → Linked Backend 로 계정 백엔드에 자동 프록시(브라우저엔 같은 오리진)
Azure Container Apps (리소스 그룹 vincsRG, Korea Central)
  ├ vinc-account (api.vincs.io)  → Node 22 + Hono → MySQL Flexible Server `vinc-mysql01`
  └ vinc-hub     (mcp.vincs.io)  → 사이드카 + 호스티드 MCP, 그래프는 Azure Files 볼륨(/data)
```

- **2026-07-28/29 전면 이관** — 가비아 VM(Caddy + systemd, 수동 SSH 배포)이 삭제되고
  위 3종 관리형 PaaS + GitHub Actions CD 로 재구축됐다. 배포 정본(as-built)은
  그래프의 `vinc.ops.deployment` — 표면 5개·CD 워크플로·교차 표면 순서
  제약이 거기 있다. 경계 급소 둘: ① Linked Backend 를 걸면 Azure 가 계정 컨테이너에
  EasyAuth 를 자동으로 켜서 **컨테이너 FQDN 직접 호출은 401**(SWA 경유만 통과) ②
  허브는 SWA 에 링크돼 있지 않아 `mcp.vincs.io/internal/*` 가 공개 도달 가능하며
  **Ed25519 서명만이 관문**이다.
- **허브는 단일 writer** — 임베디드 스토어가 배타 파일 락을 쓰므로 신·구 리비전이
  같은 Azure Files 볼륨을 동시에 물면 새 리비전이 스토어를 못 연다. `hub-deploy.yml`
  이 "이미지 갱신 → 구 리비전 deactivate → 새 리비전 Running" 순서를 강제하고,
  그래서 배포 중 짧은 다운타임이 불가피하다(계정 백엔드는 무상태라 무관).
- **웹 Vinc**(`vincs.io/app/`): 데스크탑 앱의 브라우저 빌드. 인증 = 계정 세션쿠키
  (`__Host-session`) → 계정 `/api/graph` 프록시 → 서명 Ed25519 `typ:"proxy"`
  어서션 → 허브 `/internal/graph-owner`(owner = 세션 sub 강제, **B2 불변식: 공개키만
  공유**). 완전 쓰기 패리티(delete/merge/pin/tag/ingest).
- **커뮤니티 공유**(`vincs.io/community/`): 회원이 지식팩을 공개 갤러리에 게시 +
  시각요약(도메인 분포·교차도메인%·top 태그·엣지 타입). 즉시 공개 + 경량 모더레이션.
- **회원 커넥터·티어**(v0.2.0): 공홈에서 `vinc_sk_` API 키 발급, `/connect` 온보딩으로
  claude.ai 커넥터 승인(관리 키 복붙 종식), 무료/Pro 플랜 쿼터.
- **owner 격리**(SEC-1/2/3): 읽기·쓰기 전 경로가 owner 스코프(모든 Concept MATCH 에
  `owner_id` 술어), MCP 호출은 클라이언트가 보낸 owner 를 강제로 덮어쓴다(forced_owner) —
  실엔진 2-소유자 테스트로 검증.
- **계정 삭제 → purge 캐스케이드**: 계정을 지우면 서명된 `/internal/purge-owner` 로
  호스티드 그래프도 함께 삭제한다(확인 실패 시 계정 미삭제, fail-closed).

### 경계 불변식

**반입 금지**(과금·외부 인증·학습 = 0)와 **외부 왕복 없음**은 **네이티브 데스크탑**
경계의 불변식이다. 네이티브 앱은 절대 계정 API 를 호출하지 않는다. **웹 빌드**는 이
경계 밖에 있으며 계정 세션으로 인증한다 — 네이티브 vs 웹 빌드를 구분해서 읽어야 한다.

## MCP (두 번째 정문)

Vinc 는 그래프**와 문서 정본**을 MCP 서버로도 노출한다 — MCP 호스트가 직접 읽고
쓴다(D45 이후 `vinc_doc_*` 로 원문 자체가 도구 표면에 올라왔다). 서버는
**벤더 중립**이다(JSON-RPC + MCP 인가 스펙만 구현): Claude Desktop·Claude Code·
Cursor·Codex CLI·Kimi CLI·VS Code 등 stdio 를 말하는 호스트면 무엇이든 붙고,
원격은 claude.ai 커넥터와 ChatGPT 커스텀 커넥터(개발자 모드)가 같은 OAuth 요건을 쓴다.

- 도구 23종(정본 = `vinc_mcp/tools.py` 의 `TOOLS` — 그 독스트링의 수와
  `len(TOOLS)` 는 `tests/test_tool_contract.py` 가 대조한다). 읽기 15 — 조회
  `vinc_search`·`vinc_brief`·`vinc_recall`·`vinc_graph`·`vinc_decide`·`vinc_domains`·
  `vinc_suggest_ties`, 관측·진단 `vinc_neighbors`·`vinc_path`·
  `vinc_merge_candidates`·`vinc_merge_rejections`·`vinc_diagnose`(E-G3~G7),
  문서 `vinc_doc_get`·`vinc_doc_list`(D45), 관례 `vinc_conventions`(GM-1).
  쓰기 8 — `vinc_ingest` + SEC-3 의 `vinc_delete`[DETACH DELETE]·
  `vinc_delete_edge`·`vinc_merge_nodes`[concept fold]·`vinc_merge_reject` +
  D45 의 `vinc_doc_put`·`vinc_doc_extract`·`vinc_doc_delete`.
  **쓰기 표면은 하나가 아니다.**
- **주제(theme)·어휘(type) 관리는 HTTP 전용이며 MCP 에 노출하지 않는다** — 조회
  `GET /themes`·`GET /types`, 변경 `POST /themes`·`/themes/activate`·
  `/types/ratify|reject|rename`. MCP 호스트는 재가 없이 제안만 남긴다.
- 타입은 고정 전역 enum 이 아니라 **주제별 사용자 저작 어휘**(D11-R2)다. 어휘 밖
  타입은 스킵되지 않고 `status:"proposed"` 로 편입돼(개방층 Entity/TYPED 캐리어)
  응답의 `proposed_types` 로 재가를 요청한다. `theme_id` 는 명시 → 활성 → default
  순으로 lazy 해결된다. 상세는 [[vinc.canon.vocabulary-model]].
- 도구 `target`(local|remote)으로 로컬 스토어 또는 호스티드 계정 그래프로 라우팅
  (`vinc_mcp/remote.py`, `VINC_REMOTE_MCP_TOKEN`).
- 원격 디렉토리 제출용 하드닝: 도구 안전 주석(readOnly/destructive Hint), 브라우저
  CORS, 결과 크기 상한.
- 로컬 호스트 연결/제거: 앱 **Settings → Connections** 의 버튼
  (desktop·code·cursor·codex). 등록 관례는 두 종류뿐 — JSON 병합
  (`mcpServers.vinc` 키만, 백업 후) 또는 호스트 CLI 위임(`claude|codex mcp add`).
  터미널에서도 된다 — `vinc mcp install --client <host>`(v0.8.3부터). 데스크톱
  앱 바이너리 자신이 CLI 디스패처를 겸해서(`app/src-tauri/src/main.rs` 의
  `cli_forward`/`run_cli`), 알려진 하위 명령이면 창을 열지 않고 번들 엔진
  번들 엔진(`vinc`)으로 인자를 그대로 넘기고 종료코드까지 전달한다.
  리눅스 deb·rpm 은 패키지가 이미 앱 바이너리를 `/usr/bin/vinc` 로 깔아서
  바로 된다. macOS·Windows·AppImage 는 앱 **설정 → Connections → 명령줄(CLI)
  → "설치하기"** 버튼으로 표준 위치에 `vinc` 링크를 한 번 걸어야 한다(채널별
  현황과 근거는 그래프 `vinc.plan.vinc-command-on-path`). 체크아웃에서 직접
  돌릴 땐 여전히 `python -m api.cli mcp install --client <host>`.
- 그 밖의 호스트: `vinc mcp manual` 이 붙여넣을 stdio 설정 조각을 출력한다.
  자동 등록이 없을 뿐 **서버는 동일하게 동작**한다.

## 게이트

```bash
python scripts/gate_forbidden.py          # 반입 금지 F1–F7 (WO §3 하드 룰) — 0건 요구
python scripts/version_sync.py --check    # 버전 10곳(파일 6개) 일치
python verify.py                          # 플랜 순수성(무 DB 강등)
python e2e_live.py --backend embedded     # 임베디드 백엔드 e2e
```

`version_sync.py --check` 는 **릴리스 하드 게이트**다 — `.github/workflows/release.yml`
의 `version-gate` 잡이 태그와 파일을 대조해(`--expect ${태그#v}`) 어긋나면 빌드 자체를
세운다. `latest.json` 의 version 은 git 태그에서, 클라이언트 자기 버전은
`tauri.conf.json` 에서 오므로, 태그만 올리고 파일을 안 올리면 설치해도 버전이 그대로라
업데이트 토스트가 영원히 반복된다.

## 상태

**v0.8.7 라이브** — 코드 이식 완료, GitHub Releases + vincs.io 에서 배포 중.
부트스트랩(M1) → 코드 이식(M2~) → 패키징(P4) → 호스티드 롤아웃(P5)까지 진행됐다.
출하된 능력(전부 [CHANGELOG.md](CHANGELOG.md) 대조):

- **v0.8.7** 돈다고 적혀 있던 것들이 실제로 돈다 — 계정 연결이 브라우저에서 앱까지
  닿고(연결 페이지 CSP 가 루프백을 막고 있었다), 로그인하면 동기화가 신호 넷으로
  알아서 돈다. 인용 게이트가 생성 id 인용을 아예 안 보고 있었고 매니페스트 생성기는
  한 번도 성공한 적이 없었다. 허브 워치독의 "5회 실패하면 포기" 분기는 도달 불가였다.
- **v0.8.6** 고르는 것과 들여다보는 것을 가른다 — 노드 카드가 디자인 정본을 따라
  줄 순서로 우선순위를 말한다(Details and source → Links → Ask → Zoom). 깊이 숫자
  1/2/3 은 크기를 말해 주지 않아 늘 경고를 달고 다녔으므로, 이름 붙은 프롬프트 셋이
  대신한다.
- **v0.8.5** 그래프가 자기 주장을 무효화하기 시작한다 — 문서에 적힌 주장이 조용히
  낡는 것을 그래프 자신이 잡는 장치 아홉(R1~R9). `vinc_ingest` 가 그 종류에 열이
  없는 필드를 조용히 버리지 않고 이름을 대며 경고하고, `vinc_diagnose(kind='claims')`
  가 같은 파일을 두고 어긋난 기록을 짝지어 내민다.
- **v0.8.4** 한국어가 번역투를 벗고, 감사가 지목한 자리를 닫는다 — 2026-08-18
  재감사가 지목한 다섯 자리. 어휘 테마가 소유자에서 파생되고, 어서션에 본문 지문과
  일회성이 붙는다.
- **v0.8.3** 앱이 스스로를 죽이던 것을 멈추고, 빈 화면이 길을 낸다 —
  `vinc_mcp/bridge.py` 의 `_pid_alive` 가 `os.kill(pid, 0)` 을 썼는데 **Windows 에서
  그것은 TerminateProcess 다.** 조회하는 pid 는 `session.json` 에 적힌 앱 자신이라,
  브리지가 "앱이 살아 있나" 묻는 행위가 곧 앱을 죽이는 행위였다.
- **v0.8.2** 문서가 자기 자리를 말한다 — `/doc/context`(개념 칩·목차·청크 오프셋),
  코퍼스의 `[[doc_id]]` 링크 활성, 한 번 눌러 지도와 문서가 함께 열린다 · 타임라인
  계보. **깨는 변경**: 씨앗 층 L2(`여기서 추적`) 제거 — 스코프는 전량(L0)·주제(L1)
  둘뿐이다.
- **v0.8.0** **`Atlas` → `View` 전면 개명**(F8 — `scripts/gate_forbidden.py` 가 강제,
  라우트 `/atlas` → `/view`. 서피스 안의 관점 어휘는 `Viewpoint` 로 갈랐다) ·
  넷째 관점 `Weave` · 주제 필터와 틴트.
- **v0.7.4** 뜨는 것은 누른 자리에서 자란다(모션 한 벌 — 없던 것은 퇴장 애니메이션과
  기준점이었다) · 문서를 지도 옆에 놓고 편집.
- **v0.7.3** 묻기가 후보를 알파벳순으로 고르던 것 · 커넥터 상태 파일이 배포마다
  지워지는 자리에 있던 것 — 둘 다 재현 후 수정하고 그 재현을 테스트로 박았다 ·
  스페이스 간 팩 공유.
- **v0.7.2** 지도 가시성(축소 시 소멸 정지 · 대비 바닥, D75·D76 — CI 가 그 측정을
  지킨다) · 검색 잘림 수정 · 계정 정책(AP-1).
- **v0.7.1** 도메인 문자열을 경로로 읽어 좌표에 위계를 싣는다(주제 트리) · 범례가
  필터가 된다 · 무LLM 대화.
- **v0.7.0** 화면 구조 개편(D69~D72) · 한국어 의미 검색 실가동 · 어시스턴트가 쓰기
  전에 읽는 관례 파일 **GRAPH.md**(GM-1~GM-8) · 스키마 v6 → v8.
- **v0.6.7** 상단 바 단일 셸(D56 — 좌측 레일 폐지) · 검색이 조회의 제1 진입점(D57) ·
  첫 화면 = 주제 표 + 결합 미니그래프(D58) · 스토어 close 파일락 수정(전체 493건 통과).
- **v0.6.6** Atlas 2D 를 독립 렌더러로(D46, sigma/WebGL — 구 2D 는 3D 씬 투영이었다) ·
  합계 3열 생키(D47) · 자체 툴팁(D48-T, WCAG 1.4.13) · **MCP 호스트 중립화**
  (`--client` 에 Cursor·Codex CLI 추가 + `vinc mcp manual`) · 정본 도메인 vincs.io.
- **v0.6.5** **문서 정본(Chunk=SSoT, D45)** — 원문을 Chunk 에 무손실 저장(외부 파일
  비의존), 문서 CRUD(HTTP `/doc/*` · MCP `vinc_doc_*` · CLI `doc`), 뷰별 원문 표면,
  앱 내 편집기(CodeMirror 6 라이브 프리뷰 + 개념 후보 원클릭 반영). 정본은
  [[vinc.canon.chunk-ssot]].
- **v0.6.0** Atlas 평면 관점(D43). 현행 관점은 **4종**(3D·2D·타임라인·합계) —
  흐름은 D53 에서 폐기됐다.
- **v0.5.x** 그래프 RAG 고도화(E-G1~G7 — MENTIONS 이부 그래프·ego-PPR·WCC/Louvain
  진단·ER 병합 후보·경로 증거) · **LadybugDB 단일 전환**(v0.5.3, kuzu 완전 제거) ·
  사이드카 파일락·프로세스 누수 수정(v0.5.2).
- **v0.4.0** 그래프 구조 수리 F1~F9(개방층 Entity/TYPED 라이브 포팅 · Atlas 축 3종
  합류 · degree 정직화 · Bridge 소유 편입) · SEC-3 쓰기 owner 격리 · 웹 Vinc ·
  커뮤니티 공유.
- **v0.3.x 이하** Tauri v2 자동 업데이트(v0.3.1) · 로컬↔계정 양방향 sync(v0.3.0,
  필드단위 3-way 병합) · 계정삭제 purge 캐스케이드 · 회원 커넥터·티어(v0.2.0) ·
  호스티드 MCP + owner 격리(SEC-1/2/3).
- **인프라** 가비아 VM(Caddy·systemd, 수동 배포) → Azure 전면 이관(2026-07-28/29).
  표면 2~4(정적/웹앱·계정·허브)에 처음으로 CD 가 생겼다 — DB 스키마만 수동 유지.
- **미출시(main)** Intake 드롭존과 상단 바 `가져오기` 가 원문 문서 파일
  (`.md`/`.markdown`/`.txt`/`.rst`)을 직접 받는다: `/doc/put`(origin=파일명) →
  `/doc/extract` 미리보기 → 승인 시 반영. 같은 파일을 다시 떨구면 origin 색인으로
  기존 문서를 **갱신**한다(중복 문서를 만들지 않는다).

출하된 것의 정본은 [CHANGELOG.md](CHANGELOG.md) 와 그래프의 `vinc.release.changelog`
(릴리스를 어떻게 끊는지 포함)다. 코퍼스 색인은 `vinc.spec.product-overview`.

⚠ **앞으로 할 것을 담은 마일스톤 정본은 지금 없다.** 여기 `Vinc-docs PLAN.md` 라고
적혀 있었는데 그 리포는 2026-08-11 에 아카이브됐고, 그 파일은 이력으로
`_migration/legacy/PLAN.md` 에 남겼을 뿐 갱신되지 않는다. 살아 있는 계획을 다시
세우려면 그래프에 `vinc.plan.*` 로 만든다 — 없는 정본을 가리키느니 없다고 적는다.

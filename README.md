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

**현재 v0.6.7 라이브** — GitHub Releases + [vincs.io](https://vincs.io) 다운로드
(구 도메인 `vincs.app` 은 병행 유지 — 기존 설치본의 연결 경로가 계속 동작한다).
개발자용 릴리스 요약은 [CHANGELOG.md](CHANGELOG.md).

## 정본 (SSoT)

| | |
|---|---|
| 설계 SSoT | `weg-9000-docs/CONTEXT/reference-docs/weg/C/weg.c.memory-standalone.md` (v0.4) |
| 흡수 절차 | `weg-9000-docs/WORK-ORDERS/WO-MEM-SA-001.md` (§3 반입 금지 = 하드 룰) |
| 용어·금지 어휘 | `Vinclums/Vinc-docs vinc/A/vinc.a.terms.md` |
| 아키텍처 SSoT | `Vinclums/Vinc-docs vinc/ARCHITECTURE.md`, `vinc/B/vinc.b.architecture.md` |
| 웹·계정 as-built | `Vinclums/Vinc-docs vinc/WEB-vincs-app.md` |
| 문서 정본(Chunk=SSoT, D45) | `Vinclums/Vinc-docs vinc/C/vinc.c.chunk-ssot.md` ([[vinc.c.chunk-ssot]]) |
| 배포 as-built | `Vinclums/Vinc-docs vinc/DEPLOYMENT.md` (Azure, 표면 5개) |
| 실행 계획 | `Vinclums/Vinc-docs PLAN.md` |

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
  `Vinclums/Vinc-docs vinc/DEPLOYMENT.md` — 표면 5개·CD 워크플로·교차 표면 순서
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

- 도구 18종(정본 = `vinc_mcp/tools.py` 의 `TOOLS`). 읽기 12 — 조회
  `vinc_search`·`vinc_recall`·`vinc_graph`·`vinc_decide`·`vinc_domains`·
  `vinc_suggest_ties`, 관측·진단 `vinc_neighbors`·`vinc_path`·
  `vinc_merge_candidates`·`vinc_diagnose`(E-G3~G7), 문서 `vinc_doc_get`·
  `vinc_doc_list`(D45). 쓰기 6 — `vinc_ingest` + SEC-3 의 `vinc_delete`
  [DETACH DELETE]·`vinc_delete_edge`·`vinc_merge_nodes`[concept fold] +
  D45 의 `vinc_doc_put`·`vinc_doc_delete`. **쓰기 표면은 하나가 아니다.**
- **주제(theme)·어휘(type) 관리는 HTTP 전용이며 MCP 에 노출하지 않는다** — 조회
  `GET /themes`·`GET /types`, 변경 `POST /themes`·`/themes/activate`·
  `/types/ratify|reject|rename`. MCP 호스트는 재가 없이 제안만 남긴다.
- 타입은 고정 전역 enum 이 아니라 **주제별 사용자 저작 어휘**(D11-R2)다. 어휘 밖
  타입은 스킵되지 않고 `status:"proposed"` 로 편입돼(개방층 Entity/TYPED 캐리어)
  응답의 `proposed_types` 로 재가를 요청한다. `theme_id` 는 명시 → 활성 → default
  순으로 lazy 해결된다. 상세는 [[vinc.b.theme]].
- 도구 `target`(local|remote)으로 로컬 스토어 또는 호스티드 계정 그래프로 라우팅
  (`vinc_mcp/remote.py`, `VINC_REMOTE_MCP_TOKEN`).
- 원격 디렉토리 제출용 하드닝: 도구 안전 주석(readOnly/destructive Hint), 브라우저
  CORS, 결과 크기 상한.
- 로컬 호스트 연결/제거: `vinc mcp install|uninstall --client desktop|code|cursor|codex`
  (또는 앱 Settings → Connections 의 버튼). 등록 관례는 두 종류뿐 — JSON 병합
  (`mcpServers.vinc` 키만, 백업 후) 또는 호스트 CLI 위임(`claude|codex mcp add`).
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

**v0.6.7 라이브** — 코드 이식 완료, GitHub Releases + vincs.io 에서 배포 중.
부트스트랩(M1) → 코드 이식(M2~) → 패키징(P4) → 호스티드 롤아웃(P5)까지 진행됐다.
출하된 능력(전부 [CHANGELOG.md](CHANGELOG.md) 대조):

- **v0.6.7** 상단 바 단일 셸(D56 — 좌측 레일 폐지) · 검색이 조회의 제1 진입점(D57) ·
  첫 화면 = 주제 표 + 결합 미니그래프(D58) · 스토어 close 파일락 수정(전체 493건 통과).
- **v0.6.6** Atlas 2D 를 독립 렌더러로(D46, sigma/WebGL — 구 2D 는 3D 씬 투영이었다) ·
  합계 3열 생키(D47) · 자체 툴팁(D48-T, WCAG 1.4.13) · **MCP 호스트 중립화**
  (`--client` 에 Cursor·Codex CLI 추가 + `vinc mcp manual`) · 정본 도메인 vincs.io.
- **v0.6.5** **문서 정본(Chunk=SSoT, D45)** — 원문을 Chunk 에 무손실 저장(외부 파일
  비의존), 문서 CRUD(HTTP `/doc/*` · MCP `vinc_doc_*` · CLI `doc`), 뷰별 원문 표면,
  앱 내 편집기(CodeMirror 6 라이브 프리뷰 + 개념 후보 원클릭 반영). 정본은
  [[vinc.c.chunk-ssot]].
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

마일스톤 정본은 `Vinclums/Vinc-docs PLAN.md`.

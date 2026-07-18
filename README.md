# Vinc

**독립 설치형 데이터 마이닝 데스크톱 앱.** 원문 코퍼스를 적재(ingest) →
그래프·벡터로 구조화 → 탐색·검색·패턴 추출 → 팩으로 내보내는 오프라인 우선
도구다. 네이티브 앱은 외부 인프라·API 키·서버 없이 설치 직후 동작한다. 회원은
선택적으로 공홈 계정에 로그인해 고른 도메인을 클라우드와 동기화하거나(웹·다른
기기와 공유) 지식팩을 커뮤니티에 공개할 수 있다.

> 제품명 Vinc — 라틴어 *vinculum*(결속·연결 고리). 데이터 마이닝 도구이며
> "기억/학습" 서사를 사용하지 않는다.

**현재 v0.3.1 라이브** — GitHub Releases + [vincs.app](https://vincs.app) 다운로드.
개발자용 릴리스 요약은 [CHANGELOG.md](CHANGELOG.md).

## 정본 (SSoT)

| | |
|---|---|
| 설계 SSoT | `weg-9000-docs/CONTEXT/reference-docs/weg/C/weg.c.memory-standalone.md` (v0.4) |
| 흡수 절차 | `weg-9000-docs/WORK-ORDERS/WO-MEM-SA-001.md` (§3 반입 금지 = 하드 룰) |
| 용어·금지 어휘 | `Vinclums/Vinc-docs vinc/A/vinc.a.terms.md` |
| 아키텍처 SSoT | `Vinclums/Vinc-docs vinc/ARCHITECTURE.md`, `vinc/B/vinc.b.architecture.md` |
| 웹·계정 as-built | `Vinclums/Vinc-docs vinc/WEB-vincs-app.md` |
| 실행 계획 | `Vinclums/Vinc-docs PLAN.md` |

## 아키텍처

두 축으로 구성된다: (1) **네이티브 데스크탑**(오프라인 우선, 기본)과 (2) **호스티드
표면**(vincs.app, 회원 옵트인).

### 네이티브 데스크탑 (전략 B — 임베디드 그래프 스토어)

```
Vinc 데스크톱 앱 (Tauri v2 셸, 제어 = IPC)
  → localhost HTTP (127.0.0.1:8787, 세션 토큰) → Python 사이드카(api/ + engines/)
    → GraphStore 인터페이스 → 임베디드 Kùzu (kuzu 0.11.3 — D14 기본·유일 배포 백엔드)
    + 로컬 임베딩 local-en = bge-base-en-v1.5 (768d, D10)
```

- 데이터 홈: `~/.vinc/{db, packs/, inbox/, backups/}`(D43) — 백업 = 폴더 하나 복사.
- 스토어 백엔드 교체 가능(`store/backend.py` 의 `GraphStore` 추상화 뒤). 임베디드
  드라이버 기본은 kuzu(벡터/FTS 정적 링크 → 오프라인 즉시); ladybug(LadybugDB,
  Kùzu 계보, v0.18.0)는 확장 런타임 다운로드가 필요해 오프라인 배포엔 부적합 —
  옵트인 노브만 제공(`store/config.py::embedded_driver`).
- 임베딩: 기본 local-en(영어, 오프라인). 다국어 mpnet(`"local"`, 768d)은 legacy
  back-compat 전용, anthropic(voyage-3, 1024d)은 API 키 옵션.
- 자동 업데이트(v0.3.1): Tauri v2 updater 가 기동 시 `latest.json` 매니페스트를
  비차단 확인, minisign(Ed25519) 서명 검증을 통과한 번들만 설치한다. 확인
  실패(오프라인·매니페스트 부재)는 무음이며 앱 사용을 막지 않는다.
- 라이선스 전 구성 MIT/Apache 계열(GPLv3 미포함).

### 호스티드 표면 (vincs.app — 회원 옵트인)

```
Caddy (자동 TLS, root /srv/www) — 엣지 하드닝(캐시·압축·보안 헤더)
  ├ vincs.app       → 정적 홈페이지 + 웹 Vinc(/app/) + 커뮤니티(/community/)
  ├ /api/*          → 계정 백엔드 Hono 127.0.0.1:8080 (MySQL)
  └ mcp.vincs.app   → 호스티드 MCP 루프백 127.0.0.1:8790
                       (/internal/* 는 공개 404 — 루프백 전용)
```

- **웹 Vinc**(`vincs.app/app/`): 데스크탑 앱의 브라우저 빌드. 인증 = 계정 세션쿠키
  (`__Host-session`) → 계정 `/api/graph` 프록시 → 서명 Ed25519 `typ:"proxy"`
  어서션 → 허브 `/internal/graph-owner`(owner = 세션 sub 강제, **B2 불변식: 공개키만
  공유**). 완전 쓰기 패리티(delete/merge/pin/tag/ingest).
- **커뮤니티 공유**(`vincs.app/community/`): 회원이 지식팩을 공개 갤러리에 게시 +
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

Vinc 는 그래프를 MCP 서버로도 노출한다 — Claude 등 MCP 호스트가 직접 읽고 쓴다.

- 도구 9종: 읽기 6(`vinc_search`·`vinc_recall`·`vinc_graph`·`vinc_decide`·
  `vinc_domains`·`vinc_suggest_ties`) + 쓰기 3(`vinc_ingest` + `vinc_delete`
  [DETACH DELETE] + `vinc_merge_nodes`[concept fold]; 뒤 둘은 SEC-3 추가).
- 타입은 고정 전역 enum(노드 `record|concept|domain|bridge|episode`; 엣지
  `relates_to|prerequisite_of|derives_from|inherits_from|connects_via`); 미지 타입은
  경고와 함께 SKIP.
- 도구 `target`(local|remote)으로 로컬 스토어 또는 호스티드 계정 그래프로 라우팅
  (`vinc_mcp/remote.py`, `VINC_REMOTE_MCP_TOKEN`).
- 원격 디렉토리 제출용 하드닝: 도구 안전 주석(readOnly/destructive Hint), 브라우저
  CORS, 결과 크기 상한.
- 로컬 커넥터 연결/제거: `vinc mcp install|uninstall --client desktop|code`
  (또는 앱 Settings 의 [Remove from Desktop/Code] 버튼).

## 게이트

```bash
python scripts/gate_forbidden.py          # 반입 금지 F1–F7 (WO §3 하드 룰) — 0건 요구
python verify.py                          # 플랜 순수성(무 DB 강등)
python e2e_live.py --backend embedded     # 임베디드 백엔드 e2e
```

## 상태

**v0.3.1 라이브** — 코드 이식 완료, GitHub Releases + vincs.app 에서 배포 중.
부트스트랩(M1) → 코드 이식(M2~) → 패키징(P4) → 호스티드 롤아웃(P5)까지 진행됐다.
출하된 능력: Tauri v2 자동 업데이트(v0.3.1), 로컬↔계정 양방향 sync(v0.3.0, 필드단위
3-way 병합), 웹 Vinc, 커뮤니티 공유, 회원 커넥터·티어(v0.2.0), 호스티드 MCP +
owner 격리(SEC-1/2/3), 계정삭제 purge 캐스케이드, Caddy 엣지 하드닝. 마일스톤
정본은 `Vinclums/Vinc-docs PLAN.md`.

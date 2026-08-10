<!-- vinc-export
doc_id: vinc.c.dataplane
title: Vinc 데이터플레인 참조 — P1 as-built
origin: Vinc-docs/vinc/C/vinc.c.dataplane.md@5090690
chunks: 11
verified: true
content_hash: a83e5f336da2f41ca1ae684344b3dcde5aa9264579a599770498eed43af9066b
updated_at: 2026-08-01T16:20:23.640Z
-->

---
doc_id: vinc.c.dataplane
layer: C
domain: dataplane
title: Vinc 데이터플레인 참조 — P1 as-built (store·engines·api·search·migration·sync)
status: draft
authority: Delta
version: v0.5
owner: weg-9000
last_updated: 2026-07-29
inherits_from: [weg.c.memory-standalone, vinc.b.architecture]
tags: [vinc, C, dataplane, store, engines, query, search, migration, sync, tenancy]
---

# Vinc 데이터플레인 참조 — P1 as-built

> **C 계층 참조 Delta.** P1(M2·M3) 로 구축된 데이터플레인의 **구현 계약·데이터 흐름**을
> 한 곳에서 추적 가능하게 엮는다. 제품 사실·불변 원칙·방언 판정은 재기재하지 않고
> [[weg.c.memory-standalone]](설계 SSoT) 와 [[vinc.b.architecture]](아키텍처 SSoT) 를
> `#§N` 으로 참조한다(C 는 참조만 — 재기재 금지). **코드 권위는 라이브 코드**(org
> `Vinclums`, 로컬 작업 리포 `Vinc/`)이며, 본 문서는 그 진입점 지도다. 미구현·화면(UI)·
> 미확정은 범위 밖.
>
> v0.5 갱신(2026-07-29): stale 정정 — 드라이버는 **ladybug 단일**(§1, kuzu 는 v0.5.3 에서
> 제거) · 문서 정본(D45) 표면 등재(§4)와 지식 입력 **세 경로**(§8) · 원격 기본 호스트
> `mcp.vincs.io`(§6). 마이그레이션 이력(§7)의 Kùzu 실측 서술은 역사라 그대로 둔다.
>
> v0.4 갱신(2026-07-19): 그래프 결함 9건 수리 반영 — 개방층(Entity/TYPED) 라이브 포팅
> (§3), ABOUT 관계·동기화 편입(§2·§6), Bridge 소유 편입 v2(§2·§5·§7), props 경계
> 명문화(§2), 잔여 한계 등재(§5).
>
> v0.3 갱신: v0.3.0 동기화(§6)와 SEC-1/2/3 소유 스코프 격리(§5)까지 반영. 원문 스코프
> "임베디드 전용·오프라인 우선"은 **네이티브 데스크탑 데이터플레인의 불변**이며, 호스티드
> (계정 그래프)·웹 Vinc 는 같은 데이터플레인 코드를 계정 세션/원격 프로파일로 두른 층이다
> ([[vinc.b.architecture]] · [[vinc.c.web-vincs-app]]).

## 1. 스토어 계약 — 스토어를 여는 유일 지점

Vinc 는 임베디드 전용이다(설계 [[weg.c.memory-standalone#§1]]). 데이터플레인 전체가
`GraphStore` 추상화 뒤에서 백엔드 1개(`store/embedded_backend.py`)를 본다.

| 요소 | 코드 지점 | 계약 |
|---|---|---|
| 개방 단일 지점 | `store/backend.py` `open_store(cfg)` | `backend="embedded"` 만 지원. 도달 불가(드라이버 미설치·락·손상) 시 `None` 반환 → 호출부가 `executed=False` 로 강등(불변 I2, [[vinc.b.architecture#§1]]) |
| 표면 | `store/backend.py` `GraphStore` Protocol | `run` · `session` · `provision` · `vector_search` · `fulltext_search` · `snapshot` · `close` (+ `reader_view` — 아래) |
| 드라이버 결합 상한 | `store/embedded_backend.py` `_import_driver` | 드라이버명 결합 1곳(R1). **`ladybug` 단일**(v0.5.3 에서 kuzu 완전 제거 — 듀얼 드라이버·마커 이관 레일도 함께 폐기). 미설치면 `(None, None)` → 호출부가 스토어 unreachable 로 강등(I2). 구 kuzu-first(D14 — [[vinc.b.architecture#§2]])와 공존 불가 제약은 **계보 서술**로만 유효 |
| 세션 어댑터 | `EmbeddedBackend.session()` | neo4j 동형(`session.run(cypher, **kwargs)`) — 엔진 적재부 무변경 통과(설계 [[weg.c.memory-standalone#§4]]) |
| 원자성 | (계약) | 다문장 트랜잭션 미보장 — 호출부가 MERGE 멱등으로 방어(불변 I3, [[vinc.b.architecture#§1]]) |
| 서버 read 경로 | `EmbeddedBackend.reader_view()` (M4) | 단일 Database 에서 별도 read Connection 파생 — 쓰기 잡 진행 중 조회 응답성(불변 I4·R4, [[weg.c.memory-standalone#§3]]) |
| 스키마 마이그레이션 | `EmbeddedBackend.migrate(dim)` | base DDL(멱등) + 버전 게이트 ALTER — 신규·구 설치 모두 최종형 수렴(§7). `provision()` 은 이제 `migrate()` 로 위임 |

세부 백엔드 계약(드라이버 취득·스키마 선행·인덱스 생명주기·하이브리드 검색)은
[[vinc.b.architecture#§3]] 가 정본이다.

## 2. 스키마 — 노드·관계 테이블 (스키마 선행)

`schema-free` 가 아니라 노드/관계 테이블을 먼저 선언한다(설계 [[weg.c.memory-standalone#§5]]).
`store/provision.py`·`store/embedded_schema.py` `render_embedded_ddl(dim)` 가 DDL 을
렌더하고 `EmbeddedBackend.migrate(dim)` 이 멱등 적용한다.

| 구성 | 코드 지점 | 참조 |
|---|---|---|
| 콘텐츠 노드 테이블 **9종** | `store/embedded_schema.py` `_NODE_TABLES` (Episode·Concept·Bridge·Domain·**Record**·SkillExecution·LearnerPattern·Chunk·**Entity**). **Bridge 는 v2 에서 `owner_id` 편입**(F5 — 레거시 행은 ALTER 백필 `''`) | [[weg.c.memory-standalone#§5]] |
| 관계 테이블 **9종** | 동 파일 `_REL_TABLES` — Concept→Concept **4종**(PREREQUISITE_OF·DERIVES_FROM·RELATED_TO·**INHERITS_FROM**) · Bridge→Domain(CROSSES)/Concept(CONNECTS{role}) · **ATTACHES_TO**(Record→Concept{type}) · **ABOUT**(Episode→Concept — F4 episodic↔semantic 다리, 2026-07-19) · **TYPED**(Entity→Entity{type}) | [[weg.c.memory-standalone#§5]] |
| **동기화 추적열(S0)** | `_TRACK_COLS` 를 `_TRACKED_LABELS`(**Concept·Episode·Record·Bridge·Entity**)에 부착 — `updated_at`(HLC STRING, 같은 필드 충돌 타이브레이크) · `rev`(INT64, 인과 순서) · `sync_seq`(INT64, owner별 단조 커서). Domain(bare)·Chunk/SkillExecution/LearnerPattern(파생·로그)은 추적 제외 | §6 |
| **동기화 메타 노드 4종** | `_META_TABLES`(`_`-접두 = 데이터플레인 구분) — `_SchemaMeta`(스키마 버전) · `_SyncMeta`(커서·선택도메인·last_hlc) · `_SyncBase`(3-way 병합 기준본) · `_Tombstone`(삭제 표식). `render_meta_tables()` 로 별도 렌더(관계 참조 없음) | §6 |
| `Chunk.embedding` 차원 | DDL 의 `dim` 치환 = `cfg.embedding_dim`(기본 `local-en` → 768) | [[vinc.b.architecture#§2]] |
| 타입 확정 | `completed_items`/`patterns` = STRING[] · `observations` = INT64 (§5-2 SSoT 이벤트) | [[vinc.b.architecture#§5]] |
| **사용자 저작 타입 캐리어(D11-R2)** | `Entity{entity_id,type,title,owner_id,theme_id,tags}` + `TYPED{type}(Entity→Entity)` — 레거시 4타입 밖의 사용자 저작 타입 노드/엣지의 물리 집. `theme_id` 로 노드 소유=주제 귀속 각인 | [[vinc.b.architecture#§5]] |
| **소유 스코프 SSoT** | `owner_scoped_labels()` = `owner_id` 열을 선언한 라벨(Concept/Episode/Record/Entity 등). 스코프 삭제·계정삭제 purge(§5)의 대상 집합을 스키마에서 파생 | §5 |

투기적 열은 금지 — 열 명세의 SSoT 는 엔진 SET 전수 조사다([[vinc.b.architecture#§5]]).

> **`props` 경계(F7 — 명문화, 2026-07-19).** `props` 열은 자유 속성의 **JSON 직렬화
> 문자열**이다(스키마-프리 열 부재의 우회). 계약: **표시·왕복 전용** — 파이썬
> 셰이핑(`query._props_to_obj`)에서 dict 로 파싱해 응답에 싣고 fragment 재적재로
> 되돌릴 수 있으나, **속성 조건 검색(WHERE props.x)·인덱스는 불가능**하다. 구조
> 개선(자주 쓰는 키의 실컬럼 승격 등)은 부채로 등재하되 현 규모에선 착수하지
> 않는다(발주자 결정 2026-07-19 — 현상 유지 + 경계 문서화).

> **어휘는 그래프 밖 메타에 산다(D11-R2 · IMPL §3).** 주제(Theme)·타입 어휘(어떤 타입명이
> 존재하는가)는 `<data_dir>.themes.json`(DB 파일 형제 · `store/theme_store.py`)에 저장되며 **그래프 테이블이
> 아니다** — 어휘는 해석의 *틀*이지 *내용*(노드·엣지)이 아니라서, 어휘 편집이 그래프 데이터를
> 건드리지 않도록 분리한다. 위 `Entity.type` 은 그 어휘의 *값*을 담는 content 열일 뿐, 어휘
> 자체의 소재지가 아니다(어휘 SSoT = themes.json). 상세: [[vinc.b.theme]].

## 3. 엔진별 적재 경로 (write side)

`engines/*` 는 원천 산출물을 `store.session()` 경유로 스토어에 MERGE 적재한다(멱등 —
불변 I3). 임베딩·검색 인덱스만 별도 생명주기를 탄다.

| 엔진 | 코드 지점 | 적재 대상 |
|---|---|---|
| recall | `engines/recall_ingest.py` `merge_episode` | Episode(+preference 열) |
| concepts | `engines/concepts_ingest.py` | Concept + Concept 관계 |
| bridges | `engines/bridges_ingest.py` | Bridge + Domain/Concept 관계 |
| skill | `engines/skill_tracker.py` | SkillExecution |
| learner | `engines/learner_ingest.py` | LearnerPattern(학습법 모델) |
| embed | `engines/embed_pipeline.py` | Chunk + 벡터/FTS 인덱스 |
| **MCP fragment(`vinc_ingest`)** | **`api/fragment.py` `merge_fragment`** | Record/Concept/Domain/Bridge/Episode + **개방층 Entity(임의 타입)** + 관계(RELATED_TO·PREREQUISITE_OF·DERIVES_FROM·INHERITS_FROM·CONNECTS{role}·ATTACHES_TO·**ABOUT**·**TYPED**). **라이브 ingest 정본** |

**MCP fragment 적재의 축(§4 표면 `/ingest` 도 이 경로로 수렴).** 상류(사용자의 Claude)가
저작한 `{nodes,edges,owner,[theme_id]}` fragment 를 **id 정확 매칭**으로만 MERGE 한다
(유사도 판단 금지 — D11 §3). **타입은 더 이상 고정 전역 enum 이 아니다**(2026-07-19,
D11-R2 라이브 포팅): 레거시 5종(record|concept|domain|bridge|episode)은 전용 테이블,
**그 밖의 모든 노드 타입은 Entity 캐리어**로, 양 끝이 개방 타입인 엣지는 **TYPED{type}**
로 영속된다(정보 손실 0 — T1). skip 은 형태 오류(id/title 부재)와 레거시 노드 간 미지원
(from,to) 쌍뿐 — warnings + `partial:True`. 반환은
`{merged:{...}, warnings, executed, [partial], [proposed_types]}`.

> **theme 어휘 적재의 지위(휴면 → 라이브 포팅, 2026-07-19).** 구 휴면 결정(2026-07-11)
> 은 '경로 통합'으로 해소됐다: D11-R2 의 Entity/TYPED·어휘 proposed 편입·`proposed_types`
> 반환이 **`api/fragment.py` 에 포팅**되어 revstamp(S0)·SEC-3·dedupe·partial 관례를
> 승계한 채 산다. `theme_id` 는 선택(부재 시 활성 테마 → 개방 타입 최초 조우에 "default"
> lazy 생성). 휴면 엔진 `engines/fragment_ingest.py` 는 revstamp 부재로 직접 복귀 시
> 동기화가 깨지므로 **호출부 없는 계보 참조**로만 남는다(`VincApp.write_ingest` 가
> `themes=self.themes` 전달). MCP `vinc_ingest` 스키마의 type enum 도 제거됐다.

embed 파이프라인은 **적재 전 `drop_search_indexes()` → `merge_chunks` → 적재 후
`build_search_indexes(dim)`** 순서다 — 인덱스가 걸린 embedding 열은 수정 불가하기
때문(생명주기 정본 [[vinc.b.architecture#§3]], 방언 [[weg.c.memory-standalone#§6]]).
실 모델 로드 e2e 는 네트워크 머신 잔여([[vinc.next-session]] §2 순위 5 — 구 "PLAN #10" 은
PLAN.md 재작성 시 제거된 항목 번호라 더 이상 유효하지 않다).

**변경감지 스탬핑(S0).** `merge_fragment._merge_node` 는 추적 라벨(§2)에 대해 **신규이거나
추적필드가 실제로 달라졌을 때만** `updated_at`(새 HLC)·`rev+1`·`sync_seq`(owner별 다음 번호)를
찍는다 — 멱등 재적재는 무스탬프라 가짜 동기화 충돌을 만들지 않는다. 원격에서 apply 되는
경로(§6)는 원격 스탬프를 그대로 보존하므로 이 스탬핑을 타지 않는다.

## 4. 질의 표면 (read side) + 강등 계약

`api/*` 는 순수 플랜(`QueryPlan`)을 만들고 `execute` 가 스토어에 실행한다. 스토어
도달 불가·미프로비저닝·미구현 방언이면 **순수 플랜을 `executed=False` 로 반환**한다
(무 크래시 — 불변 I2). 스코프(owner/domain/tenant) 필터는 검색 경로에도 선적용된다
(불변 I1, [[vinc.b.architecture#§1]]).

| 표면 | 코드 지점 | 성격 |
|---|---|---|
| recall / domains / learner / graph / decide | `api/query.py` | 플랜 빌더 → `execute` → `run`. graph 는 `shape_graph` 로 노드·엣지 정형 |
| search | `api/query.py` `search` | `fulltext_search`(BM25) + (주입 시)`vector_search` 를 `rrf_fuse`(k=60, 백엔드 밖 순수)로 융합. 쿼리 임베딩 주입형 |
| preference (pin/tag/touch) | `api/preference.py` | 쓰기 신호(선호). tag 는 `list_distinct(list_cat(...))` 합집합 |
| preference 랭킹 | `api/query.py` `score_preference` | 백엔드 밖 순수 함수(pin·빈도·태그 겹침). Cypher 내 점수식 없음 |
| activate | `api/activate.py` | 소유 데이터를 이식용 컨텍스트 팩(pack.md/json + MCP manifest)으로 내보냄. `include_model` 기본 False(D6, [[vinc.b.architecture#§2]]) |
| delete / merge-nodes | `api/query.py` `delete_node`·`merge_nodes` | 오염 정리 쓰기(T3c). **비가역** DETACH DELETE / concept fold. 잡 큐 직렬화(I4). owner 스코프(§5) |

방언 정합 지점(graph `label(r)` · decide `TRAIL`/실재 식별자 · 미사용 파라미터·리스트
컴프리헨션 회피)은 [[vinc.b.architecture#§4]] 가 정본이다.

**사이드카(M4) 노출:** 이 표면은 `api/server.py`(P2 사이드카)가 루프백 HTTP(127.0.0.1)로
CLI 서브커맨드와 매핑해 노출한다. 조회는 `reader_view()` 주입, 쓰기는 단일 워커 큐(불변
I4)로 직렬화한다 — 계약은 설계 [[weg.c.memory-standalone#§11]]. 라우트(`_route_get`/
`_route_post`):

| 메서드 | 라우트 | 비고 |
|---|---|---|
| GET | `/recall` `/domains` `/atlas` `/share` `/coupling` `/promote-candidates` `/suggest-ties` `/learner` `/graph` `/search` `/decide` | 조회(reader) |
| GET | `/themes` `/types` | 어휘 조회(그래프와 분리 — 어휘 write 표면은 MCP 아님, 사이드카 HTTP 전용) |
| GET | `/sync/domains` `/sync/pull-delta` `/plan/*` `/jobs/*` | 동기화 조회(§6) · 플랜 · 잡 상태 |
| POST | `/pin` `/tag` `/touch` `/activate` `/backup` `/ingest` | 선호·팩·적재(§3) |
| POST | `/delete` `/merge-nodes` | 오염 정리 쓰기(owner 스코프, §5) |
| POST | `/themes` `/themes/activate` `/types/ratify` `/types/reject` `/types/rename` | 어휘 뮤테이션(잡 큐 직렬화) |
| GET·POST | `/doc` `/docs` `/doc/highlights` `/docs-in-range` · `/doc/put` `/doc/put-file` `/doc/extract` `/doc/delete` `/doc/reembed` | **문서 정본(D45)** — 원문 보관·청킹·결정론 개념 파생. 여기선 표면 존재만 등재하고 계약 전수는 [[vinc.c.chunk-ssot]] 가 정본(C 는 참조만) |
| POST | `/sync/domains` `/sync/apply-delta` `/sync/purge-owner` `/sync` `/ingest/*` | 동기화 데이터플레인(§6) · 계정삭제 purge(§5) · 잡 제출 |

## 5. 소유 스코프 격리 (SEC-1/2/3) + 계정삭제 purge

스코프 선필터(불변 I1)는 이제 **읽기·쓰기 전 경로**에 강제된다 — 호스티드/웹이 같은
데이터플레인을 다중 소유자로 돌리기 때문이다([[vinc.b.architecture]] · SEC 계보).

- **읽기(SEC-1)**: `api/query.py` 의 모든 Concept/Episode/Record MATCH 에 `owner_id = $owner`
  술어(코드 실측: `recall`·`domains`·`learner`·`graph`·`coupling`·`suggest`·`decide` 전부).
- **쓰기(SEC-3)**: `delete_node`·`merge_nodes` 는 `owner` 를 주면 `_OWNER_SCOPED_LABELS`
  (Concept/Episode/Record)에 한해 소유 일치 시에만 실행한다. owner 강제 경로인데 라벨
  (Bridge 등)이 `owner_id` 를 안 가지면 **거부**한다(fail-closed — 무스코프 삭제 = 교차테넌트).
  로컬 앱은 owner 미전송(=None) → 무스코프(현행 유지).
- **fragment 쓰기(SEC-3)**: `merge_fragment`(§3)는 `owner_explicit`(호스티드/원격/웹)면
  owner-스코프 MATCH + **PK 탈취 skip** — 남이 이미 소유한 노드에 MERGE 하면 소유권 재스탬핑·
  덮어쓰기가 되므로, 소유자가 다르면 노드를 건너뛴다. 엣지도 owner-스코프 끝점만 MATCH 되어
  남의 서브그래프에 엣지 주입이 no-op 이 된다.
- **MCP `forced_owner`(B2 불변식)**: MCP 계층이 클라이언트 제공 owner 를 세션 sub 로 덮어써
  다중 소유자 접근을 봉쇄한다(정본 [[vinc.mcp]] · [[vinc.c.web-vincs-app]]). 실엔진 2-소유자
  테스트로 검증.

**계정삭제 → 소유 스코프 purge 캐스케이드.** `api/query.py` `purge_owner(owner)` 가
`owner_scoped_labels()`(§2, 스키마 파생) 전 라벨 + `_Tombstone`(owner_id 보유)을
`owner_id = $o` 로 DETACH DELETE 한다. 사이드카 표면은 `POST /sync/purge-owner`
(`write_sync_purge_owner`). 원격에선 계정 백엔드가 삭제 시 서명 요청을 보내고
`vinc_mcp` 의 `/internal/purge-owner` 가 **계정 서명 검증 후** 프록시하므로 owner 위조가
불가하며, 계정 하드삭제 **전에** 시도되는 fail-closed 캐스케이드다(웹/계정측 정본
[[vinc.c.web-vincs-app]]).

> **Bridge 소유 편입(F5, 2026-07-19 — 구 SEC-1 잔여 해소).** 스키마 v2 가 Bridge 에
> `owner_id` 를 부여(ALTER 백필 `''`)해 `owner_scoped_labels()` 가 자동 커버(purge 포함
> — 파생 설계의 실증). 쓰기: fragment 경로가 생성/명시 시 스탬프 + PK 탈취 skip,
> `bridges_ingest` 는 `MEMORY_DEFAULT_OWNER` 스탬프(재실행 = 레거시 `''` 자연 치유).
> 조회: `bridge_ties_plan`·`bridge_coupling_plan`·`seed_lookup`·축 플랜·`merge_nodes`
> CONNECTS 재지향이 owner 스코프 — 레거시 `''` 행만 **과도기 술어**
> `query._bridge_owner_pred` = `(owner OR '')` 로 노출된다(종전 전 테넌트 전량 노출의
> 단조 개선; 백필 보편화 후 OR 절 1줄 제거로 엄격 전환). 잔여 무소유 라벨은
> Domain/Chunk(구조·파생)뿐. 기타 기지 한계: `vinc_delete` 의 Entity 접두사
> 미라우팅 · 테마 어휘 스토어-전역(이름만·저민감). ~~엣지 톰스톤 부재~~ 는
> 2026-07-19 S4 확장으로 해소(§6).

## 6. 동기화 데이터플레인 (v0.3.0)

로컬↔계정 그래프 **양방향 동기화**. `api/sync.py`(export/apply·오케스트레이션) +
`api/syncmerge.py`(필드단위 3-way 병합·정규화) + `api/revstamp.py`(HLC·sync_seq).
share pack(전량 스냅샷·delta 없음)으로는 못 하는 "지난 번 이후 바뀐 것"만 나른다 —
S0 추적열 `sync_seq`(owner별 단조)가 그 커서다.

**동기화 단위.** `SYNC_FIELDS` = Concept(`title·domain·tags·props`), Episode(`title·date·
domain·summary·tags`) — **fragment 왕복 가능한 필드만**. Concept 의 `topic/one_liner/
status/stem`·Episode 의 `pinned` 은 fragment 가 안 써서(엔진·preference 소관) 동기화 대상
아님. `_SYNC_TYPES` = concept/episode 만 — Bridge/Entity 제외는 이제 **스코프 결정**이다
(F1 포팅으로 Entity 필드는 왕복 가능; Bridge 는 source/target_domain 이 fragment 미기록.
편입은 후속 과제). 엣지는 `_REL_TO_FRAGMENT`(CC 4종 + **ABOUT**, 2026-07-19) — Episode
소스 엣지는 전용 플랜 `sync._episode_about_plan` 이 뽑는다(번들 양끝 필터 동일).

> **엣지 툼스톤(S4 확장, 2026-07-19).** 엣지 삭제도 이제 전파된다: 삭제 표면
> `query.delete_edge`(`POST /delete-edge` · MCP `vinc_delete_edge`, 동기화 rel 5종,
> RELATED_TO 무방향 양삭제, SEC-3 owner 스코프)가 `_EdgeTombstone`(ekey=src|REL|dst)
> 을 남기고, delta 번들 `edge_tombstones` 로 export→apply(무가드 삭제 — 엣지는
> 타임스탬프 부재)→재전파, 노드 툼스톤과 같은 90일 GC. **대칭 수리**: 신규/재생성
> 동기화 엣지는 `fragment._stamp_edge_endpoints` 가 양 끝 노드를 revstamp 해 증분
> delta 에 실리게 한다(기존 노드 사이 엣지 추가가 full sync 전까지 안 실리던 선행
> 공백 해소; 재생성이 로컬 표식을 상쇄 = '재생성이 삭제를 이긴다'). sync apply 는
> `stamp_edges=False`(에코 차단).

| 단계 | 함수 | 계약 |
|---|---|---|
| export (S1) | `export_delta(owner,since,domains)` | owner + **선택 도메인**(`_SyncMeta.selected_domains`) 스코프의 `sync_seq > since` delta. `since<=0`(최초) 이면 선택 도메인 **전량**(`full:True`). 선택이 비면 **아무것도 기기를 떠나지 않음**(오프라인 우선 옵트인). 엣지는 양끝이 번들 안에 있는 것만 |
| apply (S3) | `apply_delta(owner,bundle)` | 노드별 **base(`_SyncBase`) 대비 local·remote 필드단위 3-way**. 서로 다른 필드 편집은 **둘 다 보존**, 같은 필드 충돌만 노드 타이브레이크(updated_at→rev→id)로 최신승. 병합이 로컬을 바꾸면 새 HLC 스탬프(push 가 집어감), base 는 **원격값으로** 갱신(에코 제거). 원격 스탬프는 보존(재스탬프 시 무한 왕복) |
| rev 가드 | `syncmerge.node_remote_newer` | 인바운드가 저장본보다 오래면 무시(늦게 온 옛 변경이 최신을 못 덮음) |
| 삭제 전파 (S4) | `_Tombstone` + `put/get/del_tombstone` | 하드삭제로 못 하는 삭제를 표식으로 전파. **부활 가드**: 로컬 tombstone 이 인바운드 편집보다 새/같으면 삭제 승(옛 편집으로 안 되살아남); 인바운드가 더 최신이면 tombstone 걷고 적용. 적용 시 **재편집 가드**(노드 updated_at > deleted_at 이면 삭제 무시) |
| GC | `gc_tombstones(keep_days=90)` | 안전 지평(보수적 90일 — 모든 기기 pull 가정) 지난 tombstone 청소 |
| 청킹 | `chunk_bundle(limit=3MB)` | 각 청크 자기완결(엣지는 양끝 노드 동봉). 원격 `MAX_BODY`(4MB) 아래 |

**오케스트레이터(S2).** `run(store,cfg,owner,remote)` = **pull 먼저**(원격 최신 병합 후 push
— lost-update 창 최소) → push. 잡 워커에서 돌아 로컬 쓰기가 단일 writer(I4)로 직렬화된다.
커서 2개(서로 다른 seq 공간): `cursor:push`(로컬 sync_seq) · `cursor:pull`(허브 sync_seq).
커서는 push 성공·apply 내구 **후에만** 전진(중단 시 재전송). 원격 어댑터 `RemoteHTTP`(회원
키·기본 `mcp.vincs.io` — 도메인 이전으로 `api/sync.py`·`vinc_mcp/syncclient` 기본값이 `.io` 로
바뀌었다, 인프라 정본 `Vinc-docs/vinc/DEPLOYMENT.md`)는 `vinc_mcp/syncclient` 를 감싸며,
인프로세스 스텁과 실 HTTP 양쪽으로 검증된다. 사이드카 표면: `GET /sync/domains`·
`/sync/pull-delta`, `POST /sync/domains`·`/sync/apply-delta`·`/sync`(202+job_id) —
8787 은 루프백 미노출(F4).

## 7. 스키마 마이그레이션 · 팩 오버레이

**S0 제자리 스키마 마이그레이션.** `EmbeddedBackend.migrate(dim)`(§1·§5) 3단계:
① base DDL(멱등 — 신규 설치는 추적열 포함 테이블 + 메타 테이블 생성, 구 설치는 기존 테이블
skip + 신규 메타만 생성) → ② 버전 게이트: `_SchemaMeta.schema_version` 기록값 이후
`migration_statements(from)` 의 ALTER 적용(v1 = 추적열 `ADD … DEFAULT` 백필, Kùzu 0.11.3
ADD+DEFAULT 로 기존 행 채움 실측 · **v2 = `Bridge ADD owner_id DEFAULT ''`**, F5) →
③ 버전 기록. `SCHEMA_VERSION=2`. 신규 rel 테이블(ABOUT 등)은 ①의 멱등 base DDL 재실행이
자동 생성하므로 마이그레이션 스텝 불요. **ALTER ADD 는
additive → 롤백 시 구코드가 새 열을 단순 무시**(무손상 되돌림). `provision()` 은 `migrate()`
로 위임해 기존 호출부가 무변경으로 마이그레이션을 얻는다. 어휘(themes.json) 이관은
`store/migrate_themes.py`(`python -m store.migrate_themes --apply`).

**팩 오버레이(pack_ingest, A+2 2단계).** `engines/pack_ingest.py` — 서버측 pack.json 을
임베디드 스토어로 import 한다(Vinc 는 서버 미접속). `episode_id` 멱등 MERGE 로 preference
신호(pinned/tags/access_count/last_access)를 오버레이하되, 구조 필드는 재수집분
우선(`coalesce`)이다. learner 오버레이는 현재 팩 투영에 식별자 부재로 범위 밖(기재만).
절차 정본은 [[vinc.b.architecture#§6]] · 설계 [[weg.c.memory-standalone#§10]].

## 8. 패키지 앱 첫 적재 브링업 (as-built)

> 지식 입력 모델은 **세 경로**다 — (1) Claude/MCP `vinc_ingest`(→`/ingest/fragment`, §3) ·
> (2) `.vincpack`/`.json` 팩 import · (3) **문서 정본(D45)** `/doc/put`·`vinc_doc_put`·앱 내
> 편집기(계약 정본 [[vinc.c.chunk-ssot]]). 앱은 원문 문서를 **읽는다**: 청킹·추출이 결정론
> (마크다운 헤딩 → `status='proposed'` 개념 후보)이라 오프라인·무LLM 을 깨지 않는다.
> **사용자 표면 정본은 [[vinc.d.ingest]]**. 여기서는 패키지 앱 첫 실행에서 적재가
> 웹뷰↔사이드카 경계 때문에 실패했던 as-built 사가만 기록한다.

패키지 앱(Tauri) 첫 실행 적재가 v0.1.0~0.1.3 에서 재현 실패 → **v0.1.4 해소.** 4중 원인:

| # | 원인 | 층 | 픽스 |
|---|---|---|---|
| ① | 스토어 스키마 미프로비저닝 | 사이드카 | `VincApp.open` 기동 시 멱등 프로비저닝(0d8106c) |
| ② | 셸 fetch base = 웹뷰 origin(`tauri.localhost`) | 셸(Tauri) | base 를 `127.0.0.1:8787` 로(bfee031) — 상세 [[vinc.d.desktop-shell]] |
| ③ | 세션 토큰 `win.eval` 전달 불안정 | 셸(Rust) | `main.rs` `sidecar_conn` invoke 커맨드로(v0.1.2) |
| ④ | **CORS 부재 → "Failed to fetch"** | 사이드카 | `api/server.py` `_send` 에 `Access-Control-Allow-Origin:*` + `do_OPTIONS` 프리플라이트(v0.1.4) |

핵심(④): 웹뷰(`tauri.localhost`) ↔ 사이드카(`127.0.0.1:8787`)는 **교차 출처**인데 CORS 헤더가 없어
브라우저가 차단했다(dev 는 same-origin 프록시라 미노출). **진단 정본:** Intake 에러에 `sidecarDiag`
(base·token·health) 노출(v0.1.3) → `Failed to fetch · token=64ch · health TIMEOUT`(토큰 정상·연결만
막힘)으로 CORS 특정. Rust 는 로컬 MSVC 부재로 CI 컴파일만, 사이드카(Python)는 로컬 검증 가능.

## 참조 문서 (개발·기획 원천)

| 원천 | 위치 | 관계 |
|---|---|---|
| 설계 SSoT | [[weg.c.memory-standalone]] (weg-9000-docs) | 제품 사실 상속·참조(§1·§3·§4·§5·§6·§10·§11) |
| 아키텍처 SSoT | [[vinc.b.architecture]] (동일 인스턴스 B 계층) | 불변 원칙·확정치·방언·스키마 타입·마이그레이션·소유 격리 정본 |
| 용어·표기 | [[vinc.a.terms]] (동일 인스턴스 A 계층) | 네이밍·금지 어휘 정본 |
| MCP 계약 | [[vinc.mcp]] | `vinc_ingest`·`forced_owner`·target(local\|remote) 정본 |
| 문서 정본(D45) | [[vinc.c.chunk-ssot]] (동일 인스턴스 C 계층) | 무손실 청킹·doc 정체성·변경감지·개념 파생·`/doc/*` 표면 정본(§4·§8 이 참조) |
| 웹/계정 as-built | [[vinc.c.web-vincs-app]] | 계정 프록시·B2 공개키·계정삭제 purge(§5)의 계정측 정본 |
| 구현 1차 | 라이브 코드(org `Vinclums`, 로컬 `Vinc/` store/·engines/·api/) | 본 문서가 지도하는 계약의 구현체 |
| 실행 계획 | `Vinc-docs PLAN.md` | 마일스톤·게이트(M1→P4). 잔여 임베딩 e2e 는 [[vinc.next-session]] §2 |

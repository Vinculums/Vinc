<!-- vinc-export
doc_id: vinc.d.graph-structure-analysis
title: Atlas 그래프 구조 분석 — 구(球) 형태·연결 형식·연결 다양성 정량
origin: Vinc-docs/vinc/D/vinc.d.graph-structure-analysis.md@f9884f8
chunks: 10
verified: true
content_hash: 5339b55267be4fbcc948ca21975b631a03003ef48a95b6d2af78bb50c71e5673
updated_at: 2026-08-02T13:24:56.071Z
-->

---
doc_id: vinc.d.graph-structure-analysis
layer: D
domain: graph-structure-analysis
title: Atlas 그래프 구조 분석 — 구(球) 형태·연결 형식·개념 연결 구조·연결 다양성 정량
status: draft
authority: research-input
version: v0.2
owner: vinc
last_updated: 2026-07-19
inherits_from: [vinc.d.atlas-visual-language, vinc.d.graph, vinc.c.dataplane, vinc.b.theme, vinc.a.terms]
tags: [vinc, D, atlas, graph, topology, diversity, analysis, as-built]
---

# Atlas 그래프 구조 분석 — 구(球) 형태·연결 형식·연결 다양성 정량

> **정본은 계정 그래프입니다**(2026-08-02 승격 · 발주자 결정). 이 파일은 승격 시점의
> **동결 사본**이고, 이후 갱신은 `vinc_doc_put`(doc_id `vinc.d.graph-structure-analysis`) 으로만 합니다.
> **리포 파일을 고쳐도 그래프는 바뀌지 않습니다.**

> **지위: 연구 입력(research-input) — as-built 분석.** 라이브 코드(org `Vinclums`,
> 로컬 작업 리포 `Vinc/`)를 전수 추적해 (1) 노드 "구"의 기하·재질·크기 규칙,
> (2) 전체 배치가 만드는 구면 구조, (3) 연결(엣지)의 물리 스키마와 렌더 형식,
> (4) 개념 간 연결의 3층 아키텍처를 사실 기술하고, 이를 기반으로 **구사 가능한
> 연결 다양성을 정량 계산**한다(§6). 시각 언어의 규정 정본은
> [[vinc.d.atlas-visual-language]], 데이터플레인 계약의 정본은 [[vinc.c.dataplane]],
> 타입 어휘 모델의 정본은 [[vinc.b.theme]] — 본 문서는 재기재하지 않고 코드
> 진입점과 수식만 보탠다. 코드 권위는 라이브 코드이며 본 문서의 수치는
> 2026-07-19 시점 스냅샷이다.
>
> **v0.2 정정(2026-07-19, 결함 9건 수리 반영):** v0.1 냉철 분석이 판정한 결함들이
> 같은 날 수리됐다 — ① 개방층(Entity/TYPED)은 휴면 해제: 휴면 엔진 복귀가 아니라
> **라이브 경로(api/fragment.py)에 포팅**되어 revstamp·SEC-3·dedupe 를 승계한 채
> 재활성화(§4.3), ② Atlas 는 Concept 단일 축에서 벗어나 부착 축(Bridge·Episode·
> Record)과 entity 시드를 렌더(§4.4), ③ degree 는 전 엣지 타입 카운트, ④ Bridge
> 는 owner_id(v2)를 얻어 소유 스코프 편입, ⑤ ABOUT(Episode→Concept) 관계 신설.
> §6 의 다양성 계산은 이제 **잠재치가 아니라 라이브 상한**이다.

## 0. 분석 범위·코드 진입점 지도

| 관심사 | 구현권위 | 정본 문서 |
|---|---|---|
| 노드/관계 물리 스키마 | `Vinc/store/embedded_schema.py` | [[vinc.c.dataplane]] |
| 도메인 내 개념 엣지 생성 | `Vinc/engines/concepts_ingest.py` | [[vinc.c.dataplane]] |
| 크로스도메인 Bridge 엣지 생성 | `Vinc/engines/bridges_ingest.py` | [[vinc.c.dataplane]] |
| 사용자 저작 타입(개방층) MERGE | `Vinc/engines/fragment_ingest.py` | [[vinc.b.theme]] · [[vinc.mcp]] |
| 타입 어휘 스토어 | `Vinc/store/theme_store.py` | [[vinc.b.theme]] |
| 그래프 조회·결정론 레이아웃 | `Vinc/api/query.py` | [[vinc.c.dataplane]] |
| 3D 렌더러(구·재질·엣지 렌더) | `Vinc/app/src/surfaces/atlas3d.ts` | [[vinc.d.atlas-visual-language]] |
| 레벨 조립(L0/L1/L2/Compare/Grid/Matrix) | `Vinc/app/src/surfaces/atlas.ts` | [[vinc.d.graph]] |

## 1. 노드의 "구" — 형태 = 타입 (닫힌 층)

### 1.1 프리미티브 매핑 (D35 개정 — as-built)

[[vinc.d.atlas-visual-language]] §1.1 v0.1 의 평면 표기(육각/원/마름모)는 D21
개정(전면 3D)과 D35 개정을 거쳐 아래 3D 프리미티브로 승격·확정됐다. 구현권위:
`Vinc/app/src/surfaces/atlas3d.ts` `nodeObject()` (v0.1 기준 L94–118).

| 노드 종류 | 3D 형태 | 기하 파라미터 | 개정 계보 |
|---|---|---|---|
| **Concept** | **구** (`SphereGeometry`) | 세그먼트 20×14 | 육각 → 구 (D35: 최다 노드종에 최소 잉크 형태 — 시각 비대 완화) |
| **Domain** (L0) | **구 + 적도 토러스 링** (토성형) | 링 반경 r×1.45 · 튜브 r×0.06 · 세그 8×24 | 이중육각 → 링구 (D35 개정 — 이중육각 계보는 기록물로 이관) |
| **Bridge** | 정팔면체 (`OctahedronGeometry`) | 반경 r | 마름모의 입체화 |
| **Episode / Record** | 정육각기둥 (`CylinderGeometry` 6각) | 변 = 높이 = r×0.75 (전 모서리 등길이) | 원(점 표기) → 육각기둥 |

- 형태=타입은 **닫힌 층**(D31): View 패널에 노출하지 않으며 사용자 프리퍼런스로
  변경 불가. 열린 층은 크기 스케일·라벨·화살촉·2D/3D·산개뿐.
- 2D 뷰에서는 기둥류(Episode/Record)를 카메라 쪽으로 눕혀 육각 "면"이 정면을
  보게 한다 — 평면에서도 형태=타입 판독 유지(D31 §1.5). Domain 링은 2D 에서
  카메라를 향해(xy) 두른다.

### 1.2 크기 — 연결수의 제곱근 (단일 변수 원칙)

구현권위: `atlas3d.ts` `nodeRadius()` (L77–87). 크기 채널에는 연결도(또는
레코드 수) **하나만** 싣는다 — 이중 인코딩 금지([[vinc.d.atlas-visual-language]] §1.1).

```
Domain    r = 6 + 2.2·√records
Concept 등 r = min(3 + 1.4·√degree, 12)     # 상한 12 — 허브의 화면 잠식 방지
uniform 모드  r = 5                            # 열린 층: 사용자 선택
pinned        r × 1.25                         # D32 — pin = 허브 승격(확정 한 급 위)
2D 뷰(도메인 제외) r × 0.7                     # 깊이 겹침이 없는 평면의 조밀 보정
```

### 1.3 재질 — 프로스티드 유리 (D40) · 모노크롬 규율 (D39)

구현권위: `atlas3d.ts` `material()`·`applyEnvironment()` (L61–74).

- `MeshPhysicalMaterial`: roughness 0.18 · metalness 0 · clearcoat 1 ·
  clearcoatRoughness 0.15 · envMapIntensity 0.9 + `RoomEnvironment` 환경맵
  — 클리어코트 하이라이트 + 환경 반사가 "유리 느낌"을 만든다.
- 불투명도 2단: 일반 노드 **0.72**(프로스트) · 게이트웨이 **0.35**("여기 소속
  아님"을 투명도로 발화). 완전 투명은 실루엣(형태=타입)을 무너뜨리므로 금지.
- 색은 잉크 축 모노크롬만: 라이트 ink `#14110d` 계열 / 다크는 반전 팔레트
  (D39, ink `#eae5dd` 계열). **유일한 유채색 포인트**(라이트 `#3e4c63` ·
  다크 `#8fa3c4`)는 "현재 초점" 1개 노드에만 허용.
- hover 시 비이웃 노드는 rAF 이징으로 opacity 0.12 까지 후퇴(스냅 금지, D32).

### 1.4 시각 상태 공간 (유한·닫힘)

노드 1개가 취할 수 있는 시각 상태는 형태 4 × 게이트웨이(투명도) 2 ×
초점(포인트색) 2 × pin(크기 승격) 2 = **32 상태**로 유한하다. §6.5 의 설계
비대칭(데이터 다양성 개방 ↔ 시각 어휘 고정) 논의의 전제 수치.

## 2. 전체 배치가 만드는 구면 — 결정론 레이아웃 (D17·D34)

### 2.1 원칙 — 좌표는 사이드카 정본, 클라이언트 시뮬레이션 없음

구현권위: `Vinc/api/query.py` `layout_positions()` (L624–665). **순수 함수 —
난수·시간 무의존**이라 같은 그래프는 항상 같은 좌표(D17, 플랜 순수성 계약과
동형). 렌더러는 `cooldownTicks(0)` + fx/fy/fz 핀으로 좌표를 고정하고 힘
시뮬레이션을 돌리지 않는다(`atlas3d.ts` L127–152).

배치는 2단 합성이다:

1. **도메인 클러스터 중심** — xy 단위원 둘레 등간격 `(cos 2πi/D, sin 2πi/D, 0)`
   (도메인 1개면 원점). 도메인 정렬은 이름 사전순 → 결정론.
2. **클러스터 내부** — 5개 프리셋(D34) 중 하나로 전개. 내부 정렬은
   `(pinned DESC, degree DESC, id)` — pin 이 허브 승격 1순위(D32).

### 2.2 프리셋 5종 — "구의 형태"의 본체는 shell

> ⚠ **이 절은 2026-07-19 시점의 as-built 다.** 그 뒤 D51 이 `shell` 을 퇴출했고
> **D74(v0.7.1 · 2026-08-02)가 프리셋을 `hub`·`rank` 2종으로 줄였다.** 아래 5종 표와
> "기본 = shell" 서술은 **현행이 아니다**. 코드 권위 `app/src/atlas-prefs.ts`
> `AtlasLayout = "hub" | "rank"`. 정정본: `record:fact-atlas-layout-presets`(그래프).

| 프리셋 | 기하 | 핵심 수식 (구현권위: query.py L483–616) |
|---|---|---|
| **shell** (기본) | **피보나치 구면 성단** — 허브 핵 + 구면 껍질 산개 | 반경 `r = 0.08·√i` · 극각 `φ = acos(1−2t)` (구면 균등) · 방위각 `θ = i × 2.39996…` (골든 앵글) |
| radial | Centralized Burst — BFS 깊이 = 동심 껍질 | 껍질 반경 `r = max(0.14d, 0.033k)` — 둘레 간격이 노드 지름(≈0.17)을 넘도록 노드 수 적응 |
| circle | Circular Ties — 원둘레 정렬, 엣지 = 현(chord) | `R = min(0.42, 0.10√count + 0.08)` |
| tree | Ramification — 방향 관계 계층 | 루트 = 방향 유입 0 · 층 = BFS 깊이 · 방향 무관 노드는 최하단 별도 층 |
| organic | 결정론화된 힘-지향 | 초기 = shell · 척력 1/d² + 엣지 스프링 · **60 반복 고정** · 80분위 반경 정규화 |

shell 이 곧 "각 도메인이 허브를 핵으로 한 **피보나치 구면(球面) 성단**"을
만든다 — 정렬 1위(pin > degree)가 중심(r=0), 이후 노드가 √i 로 커지는 반경의
구면 위에 골든 앵글 간격으로 균등 산개된다. 이 성단들이 단위원 궤도에 늘어선
것이 L1/L2 의 전체 형상이다.

- organic 의 방향 엣지 판정은 `_DIRECTED_RELS = (PREREQUISITE_OF, DERIVES_FROM,
  INHERITS_FROM)` — §3.2 의 화살촉 3종과 동일 집합(단일 정의 공유).
- **L0** 는 별도: 도메인 구들이 3D 타원 궤도 `(cos α, sin α × 0.25, sin α)` 에
  배열된다(2D 는 평면 원환). 구현권위: `atlas.ts` `showL0()` (L160–163).
- **2D 접기**는 직교 투영이 아닌 경사 사영 `y' = y + 0.55z` — z 로만 갈라진
  같은 클러스터 노드가 평면에서도 겹치지 않는다(`atlas3d.ts` L129–141).

## 3. 연결(엣지)의 형식

### 3.1 물리 스키마 — 관계 테이블 8종 (SSoT)

구현권위: `Vinc/store/embedded_schema.py` `_REL_TABLES` (L142–157). 임베디드
백엔드(Kùzu 계보)는 rel 테이블마다 FROM/TO 라벨이 고정이다 — 이 제약이 §5 의
"타입의 속성 승격" 설계를 강제한 물리적 원인이다.

| # | 관계 | FROM → TO | rel 속성 | 방향성 | 생성 주체 |
|---|---|---|---|---|---|
| 1 | `PREREQUISITE_OF` | Concept → Concept | — | 방향(화살촉) | concepts_ingest · fragment |
| 2 | `DERIVES_FROM` | Concept → Concept | — | 방향(화살촉) | concepts_ingest · fragment |
| 3 | `RELATED_TO` | Concept → Concept | — | 대칭 취급(화살촉 없음) | concepts_ingest · fragment · Atlas Bind(D32) |
| 4 | `INHERITS_FROM` | Concept → Concept | — | 방향(화살촉) | MCP fragment (D11 §5) |
| 5 | `CROSSES` | Bridge → Domain | — | 구조 | bridges_ingest (source/target 양쪽) |
| 6 | `CONNECTS` | Bridge → Concept | `role: STRING` | 구조 | bridges_ingest (`{domain}_inputs`) · fragment(`connects_via` + 선택 role, v0.2) |
| 7 | `ATTACHES_TO` | Record → Concept | `type: STRING` (자유) | 개방 | api/fragment (D11 §5 — 원 fragment 엣지 타입 보존) |
| 8 | `TYPED` | Entity → Entity | `type: STRING` (자유) | 개방 | **api/fragment (v0.2 — D11-R2 라이브 포팅)**; engines/fragment_ingest 는 계보 참조 |
| 9 | `ABOUT` | Episode → Concept | — | 구조 | api/fragment (`about`, v0.2 — F4 episodic↔semantic 다리, 동기화 편입) |

v0.2 부기: Bridge 는 스키마 v2 에서 `owner_id` 를 얻어 소유 스코프에 편입됐다
(레거시 행은 `''` — 조회는 과도기 술어 `(owner OR '')`, `query._bridge_owner_pred`
단일 정의). Episode 는 더 이상 관계 무참여 고립 노드가 아니다.

- `CONNECTS.role` 값은 `{domain}_input` 형식으로 도메인마다 파생된다
  (구현권위: `bridges_ingest.py` L223–238).
- `TYPED` 는 **exact `(from, type, to)` MERGE** — rel-property `type` 이 매칭
  키에 참여하므로 **같은 노드 쌍 사이에 type 이 다른 엣지가 공존**할 수 있다.
  유사도 병합 영구 금지(THESIS T1)의 물리적 구현.
- 노드 테이블은 9종(Episode·Concept·Bridge·Domain·Record·SkillExecution·
  LearnerPattern·Chunk·Entity)이나 관계에 참여하는 라벨은 5종(Concept·Bridge·
  Domain·Record·Entity)뿐 — Chunk(벡터/FTS)·SkillExecution·LearnerPattern 은
  고립 노드층(검색·통계 전용)이다.

### 3.2 렌더 형식 — 헤어라인 + 잉크 농도 (D33)

구현권위: `atlas3d.ts` L156–173 · `linkColorFn` (L250–258).

- **선**: `linkWidth` 미지정(0) = 튜브 메시가 아닌 `THREE.Line` 헤어라인.
  곡률 0.18(겹친 선 분리), opacity 0.85.
- **가중치 = 잉크 농도** (굵기 개정, D33): weight ≥6 heavy / ≥2 mid / 기본
  base / 무가중 크로스도메인 cross(저농도) / hover 비인접 fade — **5단 농도**.
  다크는 반전(D39).
- **방향 화살촉은 3종에만**: `PREREQUISITE_OF`·`DERIVES_FROM`·`INHERITS_FROM`
  (길이 2.6, 도착 노드 표면 바로 밖 — 고정 94% 는 허브 메시에 파묻히므로 노드
  반경 기반 동적 산출). `RELATED_TO` 는 대칭 관계로 방향 무의미 — 상세 레일의
  in/out 분리 표기도 방향 3종만 집계(`atlas.ts` `openDetail()` L186–189).
- 화살촉 표시 여부는 열린 층(D31 — View 패널 arrows on/off).

## 4. 개념 간 연결 구조 — 3층 아키텍처

### 4.1 ① 도메인 내부 — 위키링크 + frontmatter (v0.2 보수)

구현권위: `concepts_ingest.py` `extract_edges()`. v0.1 의 줄-키워드 분류
("다음/선행"→PREREQUISITE_OF 등)는 일상어 오탐(F8)으로 폐기됐다. 현행:

| 원천 | 관계 |
|---|---|
| frontmatter `prerequisite_of: [stem…]` (명시) | `PREREQUISITE_OF` |
| frontmatter `derives_from: [stem…]` (명시) | `DERIVES_FROM` |
| `[[위키링크]]` 추론 전부 | `RELATED_TO` (끝점 정렬 정준화 — A↔B 1개) |

같은 도메인 안(stem 정합)에서만 연결한다. 방향 화살촉(강한 시각 주장)은 명시
저작 위에만 서고, RELATED_TO 는 같은 무순서쌍에 방향 엣지가 있으면 억제 +
MERGE 시 DB 역방향 dedupe(fragment 경로와 정책 일치 — degree 부풀림 방지).

### 4.2 ② 크로스도메인 — Bridge 1급 노드 매개

실측(weg-9000 허브 데이터, 2026-07-10): 개념 간 **직접** 크로스도메인 엣지는
0 이고, 도메인 간 결합은 전부 `:Bridge` 가 1급으로 담는다(구현권위:
`query.py` `bridge_ties_plan()` L727–737 주석). 구조:

```
(Concept)←[:CONNECTS {role: a_input}]─(Bridge)─[:CONNECTS {role: b_input}]→(Concept)
                                        │
                        [:CROSSES]→(Domain a) · [:CROSSES]→(Domain b)
```

- L0 도메인 간 결합 가중치(= 엣지 잉크 농도의 weight)는 **직접 엣지 집계
  (`domain_ties_plan`) + Bridge 집계(`bridge_ties_plan`)의 합산**
  (`atlas_l0()` L803–826).
- Compare 뷰(D22)의 결합쌍도 동일 이원 구조: 직접 엣지 ∪ Bridge 매개쌍,
  직접이 매개보다 강한 신호로 우선(`coupling()` L768–790).
- Bridge 소유(v0.2): 스키마 v2 의 `owner_id` 로 SEC-1 잔여가 해소됐다 —
  `bridge_ties_plan`·`bridge_coupling_plan`·`seed_lookup`·`merge_nodes` 재지향이
  전부 owner 스코프. 레거시 `''` 행만 과도기 술어로 노출되며(단조 개선 — 종전은
  전 테넌트 전량), 백필 보편화 후 OR 절 1줄 제거로 엄격 전환한다
  ([[vinc.c.dataplane]] §5 참조).

### 4.3 ③ 사용자 저작 개방층 — 테마 어휘 + Entity/TYPED 캐리어

구현권위(v0.2): **`api/fragment.py`(라이브 단일 쓰기 표면)** · `theme_store.py`.
정본: [[vinc.b.theme]].

> **v0.2 지위 정정:** v0.1 시점 이 층은 **휴면**이었다 — 설계·스키마·엔진
> (`engines/fragment_ingest.py`)·테스트는 존재했으나 라이브 `/ingest` 가 레거시
> 경로로 수렴하며 theme_id 를 무시(발주자 결정 2026-07-11). 2026-07-19 수리로
> **라이브 경로에 포팅**되어 재활성화 — revstamp(동기화 스탬핑)·SEC-3(PK 탈취
> 차단)·dedupe·partial 관례를 승계한다(휴면 엔진 직접 복귀는 revstamp 부재로
> 동기화를 깨므로 배제). MCP `vinc_ingest` 스키마의 type enum 도 제거돼 개방
> 타입이 호스트에서 차단되지 않는다. 미지 접두사 시드(`theory:x` 등)는 Entity
> 로 프로브·순회된다(`query._seed_table`). engines/fragment_ingest.py 는 호출부
> 없는 계보 참조로만 남는다.

- **타입은 전역 고정 enum 이 아니라 주제(theme)별 사용자 저작 어휘**다.
  구 전역 enum(concept/domain/bridge/episode · relates_to/prerequisite_of/
  derives_from/connects_via)은 폐기가 아니라 새 주제 생성 시 `system_draft`
  초안으로 지위 강등(T9 — 법이 아니라 제안).
- **어휘 밖 타입은 스킵되지 않는다**: `status:proposed, provenance:
  ingest_proposed` 로 자동 편입 + 응답 `proposed_types` 로 재가 유도 —
  정보 손실 0(THESIS T1·T3). 따라서 어휘 크기에 **시스템이 강제하는 상한이
  없다**.
- 물리 라우팅: 레거시 4노드타입은 전용 테이블로, **그 밖의 모든 노드 타입은
  `Entity{entity_id, type, title, owner_id, theme_id, tags}` 캐리어**로, 그
  사이 엣지는 `TYPED{type}` 로 exact-match MERGE 된다. `_PHYSICAL_*_BINDINGS`
  는 검증 게이트가 아니라 라우팅 표.
- 의도적 비표현: **레거시 노드 간 신규 엣지 타입**은 rel 테이블 조합 폭발을
  피하려 물리 미표현 — warnings 로 통지(스킵과 다른 사유). 다양성 확장은 전부
  Entity/TYPED 캐리어와 속성(`role`/`type`) 차원으로 우회시킨 설계다.
  (v0.2: episode→concept 만은 ABOUT 1급 관계로 승격 — F4, episodic↔semantic
  다리의 제품 가치가 조합 비용을 상회한다고 판정.)
- UI 진입점: Atlas 상세 레일의 고아 노드 "잇기 제안 → Bind"(D32)도 fragment
  MERGE 단일 쓰기 표면(R4) 경유 — `relates_to` 엣지 생성(`atlas.ts` L226–245).

### 4.4 조회 측 — TRAIL 가드 순회 + 축 합류 (v0.2)

`vinc_graph`(MCP)와 Atlas L2 는 동일 플랜(`subgraph_ids_plan`)을 공유한다(D16).
가변길이 순회는 depth 1–4 로 클램프하고 **`TRAIL`(엣지 무재사용)** 가드를
쓴다 — 기본 WALK 는 3노드 사이클에서 `*1..8` 시 510 경로로 폭증하던 것을
TRAIL 이 6 경로로 절단한 실측이 근거(§6-2 M3).

v0.2 — Concept 단일 축(F2) 해소: `graph()` 는 축 플랜 3종(`bridge_rows_plan`·
`episode_rows_plan`·`record_rows_plan`, 비-OPTIONAL 매치 = 부착 있는 항목만)을
합류시켜 L1/L2 에 부착 노드·엣지를 렌더한다. L2 에선 매개자가 keep 집합에
편입돼 **'보이지 않는 접착제'**(브릿지 매개 2홉 개념이 엣지 없이 떠 있던 문제)
가 사라지고, degree 는 전 엣지 타입을 양 끝에 카운트(F3 — 브릿지 팔만 가진
개념이 더는 '고아'로 잇기 제안을 받지 않는다). Entity 는 도메인 무소속 폐쇄
성분이라 전용 시드 순회(`entity_subgraph_ids_plan`, r.type 을 rel 로 투영)로만
가시화된다. 무라벨 순회에 ABOUT/ATTACHES_TO 가 홉으로 편입되며 depth-2 에서
공동 부착 형제가 이웃이 되는 의미 변화는 의도된 것(부착도 구조).

## 5. 다양성의 구조 — 왜 3단으로 나뉘는가

연결 다양성은 아래 3단으로 계층화되어 있고, 각 단의 성장 차수가 다르다:

| 단 | 메커니즘 | 다양성 | 성장 차수 |
|---|---|---|---|
| ① 고정 스키마 | 관계 테이블 9종 (FROM/TO 라벨 고정, v0.2 ABOUT 포함) | 9 트리플 | **O(1)** — 불변 |
| ② 속성 파라미터화 | `CONNECTS.role` = 도메인 파생 · `ATTACHES_TO.type` 자유 | D + t | **O(D + t)** — 선형 |
| ③ 테마 어휘 개방층 | `Entity(type)` × `TYPED(type)` × `Entity(type)` | n²·e | **O(n²e)** — 2차, 상한 없음 |

(D = 도메인 수, t = Record 부착 타입 어휘, n = 테마 노드 타입 어휘,
e = 테마 엣지 타입 어휘)

이 계층화는 Kùzu 의 "rel 테이블당 FROM/TO 고정" 제약(§3.1)과 "정보 손실
0"(§4.3) 요구를 동시에 만족시키는 해다 — **타입을 테이블이 아니라 속성으로
승격**해, 물리 스키마는 O(1)로 닫고 의미 공간은 무제한 개방했다.

## 6. 연결 다양성 정량 계산

### 6.1 의미적 연결 형식(typed triple)의 총수

"누가 – 무엇으로 – 누구에게"의 구별 가능한 형식 수:

```
T = 4 (Concept↔Concept 관계 4종)
  + 1 (Bridge→Domain CROSSES)
  + 1 (Episode→Concept ABOUT — v0.2 신설)
  + D (Bridge→Concept CONNECTS — role = 도메인 파생 D종)
  + t (Record→Concept ATTACHES_TO — 부착 타입 어휘 t종)
  + n²·e (Entity→Entity TYPED — 노드 타입 n² 순서쌍 × 엣지 타입 e종)
```

**수치 예** — 도메인 D=5, 테마 어휘 노드 n=10 · 엣지 e=8, 부착 타입 t=8:

```
T = 4 + 1 + 1 + 5 + 8 + (10² × 8) = 819종
```

고정 스키마만의 9종 대비 **≈91배**. 지배항은 n²e — 어휘 20% 만 저작해도
개방층이 전체의 97.7%(800/819)를 차지한다. **v0.2 이후 이 수치는 잠재치가
아니라 라이브 상한이다** — v0.1 시점엔 개방층 휴면으로 라이브 T ≈ 9종에
불과했다(§4.3 정정 참조).

### 6.2 어휘 확장의 한계 증분 (복리 구조)

어휘 1개 추가가 늘리는 형식 수는 상수가 아니라 **기존 어휘 크기에 비례**한다:

```
노드 타입 +1 (n → n+1):  ΔT = (2n+1)·e     예) n=10, e=8 → +168종
엣지 타입 +1 (e → e+1):  ΔT = n²           예) n=10        → +100종
```

즉 어휘 투자 대비 다양성이 2차 함수로 증가하는 복리 구조이고, 어휘 편입이
자동(proposed)이므로 이론적 상한이 없다.

### 6.3 인스턴스 수준 — 걸 수 있는 엣지 슬롯

MERGE 매칭 키 기준(중복 불가 단위)으로, 그래프 규모별 상이한 엣지 슬롯 수.
가정: Concept N_c=100 · Bridge N_b=20 · Domain D=5 · Record N_r=30 ·
Entity N_e=50, 어휘 §6.1 과 동일:

| 경로 | 슬롯 공식 | 계산 | 슬롯 수 |
|---|---|---|---|
| Concept↔Concept (4종) | 4·N_c(N_c−1) | 4×100×99 | 39,600 |
| Bridge→Domain | N_b·D | 20×5 | 100 |
| Bridge→Concept (role D종 — role 이 매칭 키) | N_b·N_c·D | 20×100×5 | 10,000 |
| Episode→Concept (ABOUT, v0.2 — N_ep=30 가정) | N_ep·N_c | 30×100 | 3,000 |
| Record→Concept (type t종) | N_r·N_c·t | 30×100×8 | 24,000 |
| Entity↔Entity (type e종 — type 이 매칭 키) | e·N_e(N_e−1) | 8×50×49 | 19,600 |
| **합계** | | | **≈ 96,300** |

수백 노드 규모에서 이미 **9만 단위의 구별 가능한 연결 슬롯**이 존재하며
지배항은 O(N²·타입수)다. (RELATED_TO 를 대칭으로 접으면 C-C 항은
3·N_c(N_c−1) + N_c(N_c−1)/2 = 34,650 으로 소폭 준다 — 물리 저장은 방향
포함이므로 표는 방향 기준.)

### 6.4 조회·렌더 면의 조합 다양성

같은 데이터를 보는 방법의 수(열린 층 프리퍼런스 × 레벨 축):

```
렌더 구성 = 레이아웃 5 × 뷰(2D/3D) 2 × 크기 2 × 라벨 2 × 화살촉 2
          × 고아표시 2 × 산개 6단  = 960 구성
레벨 축   = L0 1 + L1 D + L2 (N × depth 4) + Compare C(D,2) + Grid 1 + Matrix 1
          (D=5, N=200 이면 = 1 + 5 + 800 + 10 + 2 = 818 뷰 대상)
```

Grid 는 카드별 레이아웃 오버라이드(D34)가 얹히므로 도메인 9개 상한에서
5⁹ 카드 조합이 추가로 존재한다(실용 다양성이라기보다 탐색 자유도).

### 6.5 설계상의 의도적 비대칭 — 의미 개방 ↔ 지각 고정

핵심 관찰: **데이터(의미) 다양성은 O(n²e)로 개방하되, 시각 어휘는 O(1)로
닫았다.**

| 채널 | 다양성 | 근거 |
|---|---|---|
| 의미적 연결 형식 | 819종+ (상한 없음) | §6.1 |
| 노드 시각 상태 | 32 (형태 4 × 게이트웨이 2 × 초점 2 × pin 2) | §1.4, 닫힌 층 D31·D35 |
| 엣지 시각 상태 | 10 (농도 5단 × 화살촉 2) | §3.2, D33 |

TYPED 의 `type` 문자열은 **캐리어명이 아니라 사용자 타입으로 엣지 `type` 에
투영**되고(v0.2 — entity 시드 뷰), CONNECTS `role`/ATTACHES_TO `type` 은 엣지
`role` 필드로 응답에 실리며 상세 레일에 부착 카운트가 표기된다. 그러나 **잉크
채널**(형태·농도)은 여전히 O(1) 고정 — 사용자 저작 타입이 아무리 늘어도 Atlas
는 모노크롬 4형태·5농도로만 발화한다(Entity 는 육각기둥 폴스루). 판독 가능성
(모노크롬 규율, [[vinc.d.atlas-visual-language]] §1)을 위한 의도적 선택이나,
개방층 어휘가 커질수록 "형식은 다른데 그림이 같은" 엣지가 늘어나는 표현
병목은 남는다. 후속 논의 후보: 잉크 채널의 타입 분화를 DEC 후보로 올릴지
여부 — 본 문서는 research-input 이므로 제안까지만 기록한다.

### 6.6 알려진 잔여 한계 (v0.2 — 결함 아닌 등재 항목)

- ~~엣지 톰스톤 부재~~ → **해소(2026-07-19 S4 확장)**: `_EdgeTombstone` +
  `delete_edge` 표면(`vinc_delete_edge`) + 신규 엣지의 끝점 revstamp(추가·재생성
  전파) — 엣지 삭제·추가 모두 증분 동기화된다([[vinc.c.dataplane]] §6).
- **레거시 `''` Bridge 과도기 노출**: 백필 불가(신뢰 원천 부재)로 과도기 술어
  유지 — 종전 전량 노출 대비 단조 개선이며, 로컬 백필(bridges_ingest 재실행)
  보편화 후 `_bridge_owner_pred` 의 OR 절 1줄 제거로 엄격 전환.
- **vinc_delete 의 Entity 미도달**: 삭제 표면의 접두사 표에 개방층 라우팅
  미편입(후속 과제).
- **호스티드 테마 어휘 스토어-전역**: `.themes.json` 은 owner 스코프가 없어
  개방 타입 *이름* 이 테넌트 간 공유된다(이름만·저민감, /themes 는 로컬 전용).
- **Bridge L1 소속 판정은 선언 필드 신뢰**: fragment 로 만든(source/target_domain
  없는) 브릿지는 L1 렌즈에서 빠질 수 있다 — L2 에선 보임(v1 수용).

## 7. 판정 요약

1. **노드의 구**: Concept = 프로스티드 유리 구(D35·D40), Domain = 토성형
   링구, 크기 = √연결수(상한 12), 상태 = 투명도·포인트색·pin 승격의 유한
   32 상태 — 전부 닫힌 층.
2. **전체의 구**: 도메인 = 피보나치 구면 성단(shell 기본, 허브 핵·골든 앵글
   산개), 성단들이 단위원 궤도 배열 — 결정론 순수 함수(D17), 클라이언트
   시뮬레이션 0.
3. **연결 형식**: 물리 9 트리플(v0.2 ABOUT 포함)로 닫힌 스키마 + 속성
   (`role`/`type`) 승격으로 연 의미 공간. 렌더는 헤어라인·잉크 농도 5단·방향
   화살촉 3종.
4. **연결 구조**: 도메인 내 frontmatter 명시 + RELATED_TO 추론(v0.2 보수) /
   크로스도메인 Bridge 1급 매개(소유 스코프 편입) / 사용자 저작 Entity·TYPED
   개방층(라이브)의 3층 — Episode 는 ABOUT 으로 semantic 층에 접속.
5. **다양성 정량**: T = 19 + n²e — 어휘 10·8 저작 시 819종(고정 대비 91배),
   한계 증분 (2n+1)e 및 n² 의 복리 성장, 인스턴스 슬롯은 수백 노드에서 9.6만+.
   **v0.2 부로 전량 라이브** — 의미 다양성은 상한 없이 개방, 시각 잉크 채널은
   32·10 상태로 의도적 고정(비잉크 채널에는 타입·role 노출).

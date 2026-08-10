<!-- vinc-export
doc_id: vinc.d.ux-research
title: Vinc UI/UX 재구조화 연구
origin: Vinc-docs/vinc/D/vinc.d.ux-research.md@5090690
chunks: 7
verified: true
content_hash: 7e82f6695f3a2236bfddffa7a4d3a59cce9802ff0f3d7fcd1993fea85b59d50c
updated_at: 2026-08-01T16:20:39.731Z
-->

---
doc_id: vinc.d.ux-research
layer: D
domain: ux-research
title: Vinc UI/UX 재구조화 연구 — 표면 감사·Atlas 그래프 설계·영어 카피 시스템
status: draft
authority: research-input
version: v0.1
owner: vinc
last_updated: 2026-07-10
inherits_from: [vinc.d.index, vinc.a.terms]
tags: [vinc, D, ux, research, atlas, copywriting, dec-candidate]
---

# Vinc UI/UX 재구조화 연구

> **지위: 연구 입력(research-input).** [[vinc.d.index]] v0.1(8화면)의 v0.2 개정 근거를
> 제공한다. 본 문서의 제안은 §5 DEC 후보표를 통과해야 정본이 된다 — 여기 내용은
> 화면설계 SSoT 를 즉시 대체하지 않는다. 어휘·금지 규칙은 [[vinc.a.terms]] 준수
> (재기재 금지). P3(UI) 착수 전 선행 연구이며, 기능 공백(§3.2 그래프 순회 등)과의
> 접점은 §4.4 에 명시한다.

## 1. 문제 정의 — 왜 P3 착수 전에 재구조화가 필요한가

발주자 관찰 3건(2026-07-10):
1. **불필요한 기능·설정** — 현행 8화면에는 목적 대비 과잉인 표면이 있다(§2 감사).
2. **그래프가 보기 불편** — 시각적 그래프에서 통찰을 얻기 어렵다(§3 연구).
3. **도메인별·도메인 결합의 유동 탐색 불가** — 데이터셋 구획 사이를 오가며 결합
   지점을 따라가는 동선이 없다(§4 Atlas 설계).
4. **영어 UI + 카피라이팅** — 전 표면 영어화하되, 기술 식별자의 원문 노출이 아니라
   마케팅 수준의 카피가 필요하다(§6 카피 시스템).

외부 연구가 이 관찰을 뒷받침한다. 마크다운 그래프 뷰의 대표 사례(Obsidian)에 대한
공통 비판은 Vinc 화면 6 의 현행 설계에 그대로 적용된다: *"연결의 위상 지도일 뿐
작업의 운영 뷰가 아니다 — 우선순위·상태·지금 필요한 것을 보여주지 않는다"*,
*"약 200 노트를 넘으면 탐색보다 구경에 가깝다"*, *"노드 타입도 엣지 타입도 없어
데이터베이스 의미의 지식 그래프가 아니다"*. Vinc 는 이미 **타입 있는 노드
(Episode/Concept/Bridge/Domain)와 타입 있는 엣지, cross_domain 플래그**를 데이터
플레인에 보유하므로, 이 비판을 피할 자산이 있는데 화면 설계가 그것을 쓰지 않고
있다는 것이 본 연구의 출발점이다.

## 2. 현행 표면 감사 — 무엇이 과잉인가

[[vinc.d.index]] §2 의 8화면을 "제거 시 잃는 것" 기준으로 판정한다.

| 화면 | 판정 | 근거 |
|---|---|---|
| 1 로그인 | **강등 → 설정 옵트인** | 단일 사용자·오프라인·로컬 앱의 첫 화면이 계정 생성이다. 미결 D7(무암호 단일 프로필)이 기본값이 되면 이 화면은 "잠금을 켠 사용자"에게만 필요한 경로다. 첫 실행 마찰(설치→즉시 가치)의 최대 비용 지점 |
| 2 온보딩 | **흡수 → 첫 실행 빈 상태** | 공통 규약이 이미 "Empty 카피는 실제 커맨드 동반"을 강제한다([[vinc.d.index]] §3). 별도 온보딩 화면은 이 규약과 기능 중복 — 데이터 디렉토리·임베딩 준비는 Intake 첫 화면의 빈 상태 + 진행 표시로 충분 |
| 3 적재 | 유지 | 진입 표면. [[vinc.d.ingest]] 템플릿 유효 |
| 4 레코드 탐색 | **통합 후보** | 탐색이 4(목록)·5(분포)·7(검색) 3개 화면으로 파편화 — 검색 결과에서 그래프로, 분포에서 특정 도메인 서브그래프로 건너가는 동선이 트리에 없다. 관찰 3(유동 탐색 불가)의 구조적 원인 |
| 5 데이터셋 분포 | **Atlas L0 로 흡수** | 도메인 분포는 별도 차트 화면이 아니라 그래프의 최상위 줌 레벨(§4 L0)이 자연 서식지다. `/domains` 집계는 L0 의 데이터 소스가 된다 |
| 6 그래프 마이닝 | **재설계 (본 연구 핵심)** | 현행 [[vinc.d.graph]]는 "좌 필터폼·중앙 캔버스·우 상세" 단일 평면 뷰 — 줌 레벨·도메인 렌즈·결합 동선이 없다. §3–4 |
| 7 검색 | 유지 + 그래프 핸드오프 | 목록형 결과는 고유 가치. 단 모든 결과 행에 "Show in Atlas" 액션을 계약으로 추가 |
| 8 팩 내보내기 | 유지 | 산출물 표면. 고유 |

**제안 트리 (5표면 + 설정) — DEC 후보 D15:**
```
Vinc UI
├─ Intake    (구 3+2)  — 적재 + 첫 실행 셋업(빈 상태가 온보딩을 겸함)
├─ Records   (구 4)    — 레코드 목록·열람 (행 액션: Show in Atlas)
├─ Atlas     (구 6+5)  — 그래프 탐색 3-레벨 줌 (L0 도메인 지도 = 구 분포 화면)
├─ Search    (구 7)    — 하이브리드 검색 (행 액션: Show in Atlas)
├─ Packs     (구 8)    — 팩 내보내기
└─ Settings (팝오버)   — 잠금(구 1, 옵트인)·MCP 관찰 패널·데이터 디렉토리
```
8→5 로 줄지만 기능 상실은 0 — 로그인·온보딩·분포는 사라지는 게 아니라 더 싼
위치로 이동한다. [[vinc.d.index]] §5 "8화면 모델 불변" 선언과 충돌하므로 반드시
DEC 로 결정한다(§5).

## 3. 그래프 탐색 UX — 외부 연구 요약

적용 원칙 4건(출처: 정보시각화 표준 문헌 + 그래프 UX 실무 자료, 2026-07 조사):

1. **Overview first, zoom and filter, details on demand** (Shneiderman mantra).
   전체 노드를 한 평면에 쏟는 것이 아니라, 요약된 개관에서 시작해 줌·필터로
   좁히고 상세는 요구 시점에 연다. 대규모 네트워크 시각화 연구(GraphMaps,
   커뮤니티 기반 탐색)의 공통 결론: **커뮤니티/클러스터 수준의 개관이 지역
   관심(region of interest)으로 안내해야 한다.**
2. **가독 한계는 실재한다** — 수백 노드를 넘는 순간 무차별 렌더는 "탐색이 아니라
   구경"이 된다(Obsidian 비판 §1). 해법은 렌더 수 늘리기가 아니라 **집계 수준을
   바꾸는 시맨틱 줌**이다.
3. **타입을 쓰는 그래프만 작업 도구가 된다** — 노드/엣지 타입, 상태, 파셋 필터가
   없으면 위상 지도에 그친다. Vinc 데이터 플레인은 타입을 이미 갖고 있다(§1).
4. **Sigma.js/WebGL 특성** — 대규모 렌더는 강점이나, ForceAtlas2 는 고비용·
   비결정적. 상호작용 문법(hover 이웃 강조, 카메라 줌)은 기본 제공되므로 UI 의
   과제는 렌더가 아니라 **레이아웃의 사전 계산과 줌 레벨 설계**다(§4.4).

## 4. Atlas — 화면 6 재설계 제안

관찰 2·3("보기 불편", "도메인별·결합의 유동 탐색")의 직접 해법. 이름 Atlas 는
"지도책 — 축척이 다른 여러 장의 지도" 은유로, 마이닝 서사와 정합한다.

### 4.1 3-레벨 시맨틱 줌

| 레벨 | 무엇이 노드인가 | 무엇이 엣지인가 | 답하는 질문 |
|---|---|---|---|
| **L0 Domain Atlas** | 도메인(슈퍼노드, 크기=레코드 수) | 도메인 간 결합(굵기=Bridge·cross_domain 엣지 수) | "내 코퍼스는 어떤 구획으로 나뉘고 어디가 이어져 있나" |
| **L1 Domain Lens** | 선택 도메인의 Concept/Episode | 도메인 내 관계 + **가장자리 게이트웨이**(타 도메인으로 나가는 결합 노드를 렌즈 경계에 유령 표시 — 클릭 시 그 도메인 렌즈로 전환) | "이 구획 안은 어떻게 생겼고 어디로 새어 나가나" |
| **L2 Node Focus** | 선택 노드의 이웃(깊이 1–2 ego) | 해당 노드 인접 엣지 | "이 레코드/개념은 무엇과 묶이나" |

- 수천 노드 코퍼스도 L0 는 항상 도메인 수(한 자릿수~수십)로 요약된다 — 가독
  한계(§3-2)를 구조로 회피.
- **결합의 유동 탐색**: L1 게이트웨이가 핵심 발명이다. 도메인 A 렌즈 안에서 B 와
  묶인 노드는 렌즈 가장자리에 B 방향으로 정렬되어 반투명 표기되고, 클릭 한 번으로
  B 렌즈로 "통과"한다. 화면 전환 없이 도메인 사이를 걸어 다니는 경험.
- L0↔L1↔L2 는 줌 제스처(스크롤/핀치)와 더블클릭으로 연속 전환. 브레드크럼
  (`Atlas › physics › fourier-transform`)으로 위치 상실 방지.

### 4.2 상호작용 문법

| 제스처 | 동작 |
|---|---|
| hover | 이웃 유지 + 비이웃 페이드(Sigma reducer). 라벨은 hover 대상만 승격 |
| click | 우측 상세 레일 열기(레코드 본문 발췌·태그·연결 목록) |
| double-click | 그 노드로 refocus (L2 재중심) |
| 스크롤 줌 임계 통과 | L0↔L1↔L2 시맨틱 전환 (연속 배율 아님 — 명시적 3단) |
| 게이트웨이 클릭 | 인접 도메인 렌즈로 전환(§4.1) |
| `←` / breadcrumb | 탐색 히스토리 후퇴 |

### 4.3 필터의 재설계 — "설정처럼 보이는 폼" 제거

현행 좌측 "필터·질의 패널(도메인·태그·깊이)"([[vinc.d.graph]] §6.1)은 폼 UI 다 —
관찰 1(불필요한 설정 인상)의 그래프판. 대체:
- **도메인 필터 → L0 자체가 필터다.** 드롭다운 대신 도메인 노드를 클릭해 렌즈로
  들어간다.
- **태그 → 인터랙티브 레전드 칩.** 캔버스 하단 칩 토글(active = 표면 승격 + 텍스트
  무게, 모노크롬 규약 유지).
- **깊이 → L2 에서만 의미 있으므로 L2 상세 레일 안의 1–2 스테퍼로 국소화.**
- 남는 좌측 패널은 삭제 — 캔버스가 전폭을 얻는다. 상세는 우측 레일(click 시에만).

### 4.4 기술 계약 델타 (P3 전 사이드카 준비)

| 항목 | 내용 | 접점 |
|---|---|---|
| 레이아웃 사전 계산 | ForceAtlas2 는 비결정·고비용(§3-4) → 적재 완료 시 사이드카 잡 큐가 좌표를 계산·저장, `/graph` 응답에 `x,y` 포함. UI 는 소비만(유동성·재현성 확보) | 기구현 잡 큐 재사용 |
| L0 집계 | `/domains` 를 L0 데이터 소스로 확장(도메인별 카운트 + 도메인 간 결합 카운트 행렬) | `/domains`·`/graph` |
| L1/L2 순회 | seed+depth 서브그래프 질의 — **현재 미구현 공백**과 동일 항목. Atlas 가 이 공백의 1차 소비자이므로 P3 전 필수 선행 | `vinc_graph` 공백(WEG-VINC-GAP §3.2) · [[vinc.mcp]] |
| 모노크롬 결합 표기 | cross_domain 엣지는 색이 아닌 **dashed stroke + 양끝 도메인 아이콘 페어**로 변별(디자인 게이트 유지) | [[vinc.d.index]] §3 · DESIGN.md |
| LOD 라벨 | 줌 배율 임계 아래에서 라벨 숨김(허브 노드만 예외) | GraphView 컴포넌트 |

## 5. DEC 후보표 — **전건 승인 (발주자, 2026-07-10)**

| # | 결정 | 옵션 | 권고 | 결정 |
|---|---|---|---|---|
| D15 | 표면 트리 8화면 → 5표면+설정 (§2) | (a) 유지 (b) 5표면 채택 (c) 부분(1·2만 강등) | (b) | **(b) 채택** |
| D16 | Atlas 3-레벨 시맨틱 줌 (§4.1) | (a) 현행 단일 평면 (b) 3-레벨 | (b) | **(b) 채택** |
| D17 | 레이아웃 사전 계산(사이드카) (§4.4) | (a) 클라이언트 실시간 (b) 사이드카 잡 | (b) | **(b) 채택** |
| D7(기존 미결) | 무암호 단일 프로필 기본 | (a) 로그인 화면 유지 (b) 무암호 기본+옵트인 잠금 | (b) | **(b) 채택** |
| D18 | 영어 카피 시스템·보이스 가이드 (§6) | (a) 식별자 직노출 (b) 카피 덱 채택 | (b) | **(b) 채택** |

정본 등재: [[vinc.b.architecture#§2]]. 개정 반영: [[vinc.d.index]] v0.2 · [[vinc.d.graph]] v0.2 ·
[[vinc.d.ingest]] v0.2 + 신규 [[vinc.d.records]]·[[vinc.d.search]]·[[vinc.d.packs]]·[[vinc.d.settings]].

## 6. 영어 카피 시스템 — 식별자 노출이 아닌 카피라이팅

### 6.1 보이스 (상수) — "Precise, warm, craftsmanlike"

Ink & Paper 디자인 철학(밀도 있는 잉크, 장식 배제)의 언어판:
- **Verb-first.** 모든 CTA 는 동사로 시작하고 결과를 약속한다 — "Ingest sources",
  "Open in Atlas", "Export pack". 명사형 라벨("Ingestion", "Exportation") 금지.
- **Contractions.** "can't", "you'll" — 자연스러운 어조. 뻣뻣한 무축약 금지.
- **한 문장 원칙.** 빈 상태·오류는 [무엇이 일어났나] + [다음 동작] 정확히 2절.
- **서사 금지(F2 정합).** learn / remember / memory / study / brain / "second
  brain" 계열 영어 서사 일체 금지. 정본 동사군: **mine, map, trace, surface,
  bind, connect, explore, ingest, export.** (`recall`·`decide` 는 코드 식별자로만
  — UI 카피에 노출 금지, [[vinc.a.terms]] §2)
- **주권 카피.** 오프라인 정체성은 기능이 아니라 브랜드다 — 반복 사용 가능한
  신뢰 문구: *"Runs entirely on this machine. Nothing leaves."*

### 6.2 톤 (상태별 가변)

| 상태 | 톤 | 규칙 |
|---|---|---|
| 빈 상태 | 초대 | 결핍("No data")이 아니라 다음 동작으로 초대. 항상 CTA 동반 |
| 진행 | 담백 | 수치 우선("Ingesting 214 of 380"), 의인화 금지("Working hard…" 금지) |
| 성공 | 간결 + 다음 동선 | 결과 요약 + 자연스러운 다음 화면 링크 |
| 오류 | 침착·해법 우선 | 비난 금지, 원인(알면)+복구 동작. 기술 코드는 접힌 상세로 |

### 6.3 표면 네이밍 (영어 정본 제안)

| 표면 | 영어명 | 탈락안과 사유 |
|---|---|---|
| 적재 | **Intake** | "Ingest" 는 동사(CTA)로 예약 — 표면명은 명사. "Import" 는 1회성 뉘앙스 |
| 레코드 | **Records** | "Recall" 금지([[vinc.a.terms]] §2) |
| 그래프 | **Atlas** | "Graph" 는 기술 식별자 직노출 — 은유 승격(§4). "Map" 은 지리 오해 |
| 검색 | **Search** | 관례 유지가 정답인 유일한 표면 |
| 내보내기 | **Packs** | 산출물(명사)이 표면 정체성. "Export" 는 CTA 로 예약 |
| 설정 | **Settings** | 관례 |

### 6.4 마이크로카피 덱 (표면 × 4-State, 발췌 정본)

| 표면·상태 | 카피 (영어 정본) |
|---|---|
| Intake · idle(첫 실행) | **"Bring your corpus in."** — Drop files or folders here, or `Choose sources`. Vinc runs entirely on this machine — nothing leaves. |
| Intake · loading | "Ingesting {n} of {m} — {domain_count} domains so far." |
| Intake · success | "{n} records across {m} domains. → `Open in Atlas`" |
| Intake · error | "Couldn't read {source}. Check the file and `Try again` — everything already ingested is safe." |
| Records · idle | "No records yet. `Ingest sources` to fill this view." |
| Records · 행 액션 | `Show in Atlas` · `Pin` · `Tag` |
| Atlas · idle | "Nothing to map yet. Ingest a source or run a search — the atlas draws itself from your records." |
| Atlas · loading | "Laying out {domain_count} domains…" |
| Atlas · success(L0 툴팁) | "{domain}: {n} records · {k} ties to other domains. Click to open the lens." |
| Atlas · L1 게이트웨이 툴팁 | "Crosses into {domain} — click to follow." |
| Atlas · error | "The atlas couldn't load. `Retry` — your records are untouched." |
| Search · idle | "Search across everything you've ingested — by meaning or by keyword." |
| Search · empty result | "No matches for '{q}'. Try broader terms, or browse the Atlas instead." |
| Packs · idle | "Bundle records into a pack you can carry anywhere. `Create a pack`" |
| Packs · success | "Pack ready — {n} records, {size}. `Reveal in folder`" |
| Settings · 잠금 옵트인 | "Lock Vinc with a passphrase. Optional — your data stays local either way." |
| MCP 관찰 패널 | "Tool calls from connected clients appear here, as they happen." |

### 6.5 태그라인 후보 (마케팅 표면 — 창 제목·사이트·스토어)

1. **"Mine what matters."** — 동사 정체성, 3어.
2. **"Every thread, one desk."** — vinculum(결속) 계보 + 로컬 주권.
3. **"Your corpus, mapped."** — Atlas 와 직결.

권고: 1 을 주 태그라인, 3 을 Atlas 기능 카피로. (최종 선정은 D18 승인 시.)

## 7. 실행 순서 (P3 진입 게이트)

1. 본 연구 DEC 5건(§5) 발주자 결정 →
2. [[vinc.d.index]] v0.2 개정(5표면 트리) + [[vinc.d.graph]] v0.2(Atlas 3-레벨) +
   나머지 표면 설계서 seed →
3. 사이드카 계약 델타(§4.4 — 레이아웃 잡·L0 집계·seed+depth 순회)를 코드 레포에
   구현. **seed+depth 는 MCP `vinc_graph` 공백과 동일 작업이므로 1회 구현으로
   UI·MCP 양쪽을 해소** →
4. 카피 덱(§6.4)을 표면 설계서에 실텍스트로 이관(카피는 화면이 소유 —
   [[vinc.d.index]] §3) → P3 코드 착수.

## 참조 (외부 연구, 2026-07-10 조사)

- Cambridge Intelligence, "Graph visualization UX" — 개관·줌·필터·상세 원칙
- GraphMaps (arXiv:1506.06745) — 대규모 그래프의 지도식 탐색
- LargeNetVis (arXiv:2208.04358) — 커뮤니티 분류 기반 탐색 안내
- Code Culture, "Obsidian's Graph View Is Beautiful and Almost Completely
  Useless" — 무타입 그래프 뷰의 작업 불가 비판(본 연구의 반면교사)
- Sigma.js 공식 문서 — WebGL 렌더·hover 이웃 강조·graphology 커뮤니티 검출
- Smashing Magazine·Apollo GraphQL UX Writing Style Guide — verb-first·톤 가변·
  빈 상태/오류 카피 원칙

## 부록 Z. 미분류 원문 사실
(없음)

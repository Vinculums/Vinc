<!-- vinc-export
doc_id: vinc.d.index
title: Vinc 화면설계 — 전체 표면 트리 (5표면 + Settings)
origin: Vinc-docs/vinc/D/vinc.d.index.md@5090690
chunks: 2
verified: true
content_hash: 83c19eb05e9a60196771bd530045a0df6401c7f8b6fa38ac94cf8230763a6481
updated_at: 2026-08-01T16:20:35.518Z
-->

---
doc_id: vinc.d.index
layer: D
domain: index
title: Vinc 화면설계 — 전체 표면 트리 (5표면 + Settings) + 공통 규약
status: draft
authority: SSoT
version: v0.2
owner: vinc
last_updated: 2026-07-10
inherits_from: [vinc.a.terms]
tags: [vinc, D, screen, tree, 4-state, atlas]
---

# Vinc 화면설계 — 전체 표면 트리 (5표면 + Settings)

> **D 계층 인덱스.** Vinc 데스크톱 앱(Tauri 셸)의 모든 UI 표면을 GNB→표면 트리로 정렬한
> 마스터다. 표면별 화면설계서는 §4 인덱스 참조. 용어·금지 규칙은 [[vinc.a.terms]] § 참조
> (재기재 금지). 색·치수는 직기재 금지 — 디자인 토큰/컴포넌트만 참조(디자인 게이트 D-R).
>
> **v0.2 (2026-07-10)**: DEC D15 승인으로 8화면(D8)을 **5표면 + Settings** 로 개정.
> 근거·감사는 [[vinc.d.ux-research]] §2. 카피 언어는 **영어 정본**(D18 — [[vinc.d.ux-research]] §6).

## 1. 개요
Vinc 는 **단일 데스크톱 셸(Tauri)** 하나의 표면을 갖고, 그 아래 **5개 표면**이 트리로
매달린다. 로그인은 D7(무암호 단일 프로필 기본)에 따라 Settings 의 옵트인 잠금으로,
온보딩은 Intake 첫 실행 빈 상태로, 데이터셋 분포는 Atlas 의 L0 줌 레벨로 흡수됐다
(구 8화면 → 5표면, 기능 상실 0 — [[vinc.d.ux-research]] §2). 각 표면은 **4-State** 로 선언한다.

### 아키텍처 계약 (제어/데이터 분리)
- **제어 = Tauri IPC** — 창·세션·사이드카 수명주기·로컬 파일 다이얼로그 등 셸 제어.
- **데이터 = localhost HTTP** — 사이드카(`api/server.py`, `127.0.0.1` + 세션 토큰) 호출.
  UI 는 그래프/질의 로직을 담지 않고 사이드카 HTTP 만 호출한다.
- **그래프 렌더러 = 3d-force-graph(three.js 계열)** — Atlas 전용, D21 전면 3D 채택
  (Compare 렌즈만 Sigma 2D). 레이아웃은 사이드카 사전 계산(D17).

## 2. 전체 표면 트리 (GNB → 표면)
```
Vinc UI  ── Tauri 셸 ── Python 사이드카(localhost HTTP + 토큰)
│
├─ 표면 1  Intake    — 적재 + 첫 실행 셋업(빈 상태가 온보딩 겸함, /ingest/{engine})
├─ 표면 2  Records   — 레코드 목록·열람(/recall) · 행 액션 "Show in Atlas"
├─ 표면 3  Atlas     — 그래프 탐색 3-레벨 줌(/graph·/domains·/decide, 3d-force-graph)
├─ 표면 4  Search    — 의미·키워드 하이브리드 검색(/search) · 행 액션 "Show in Atlas"
├─ 표면 5  Packs     — 산출물 팩 내보내기(/activate)
└─ Settings (팝오버) — 옵트인 잠금(D7)·MCP 관찰 패널(D11)·데이터 디렉토리
```
전역 셸 요소(사이드바 내비, 세션 상태, Settings 팝오버)는 각 표면 위 공통 크롬으로 둔다.

## 3. 표면 공통 규약
| 항목 | 규약 |
|---|---|
| 화면 헤딩 | `# 표면 {N}. {영어명}` (N = 1–5 연속) |
| 4-State | 모든 데이터 표면은 `idle` / `loading` / `success` / `error` 4상태를 명시 |
| 카피 언어 | **영어 정본**(D18). 보이스 = verb-first·축약형·한 문장 원칙·서사 금지(F2). 정본 동사군 mine/map/trace/surface/bind — [[vinc.d.ux-research]] §6.1 |
| Empty/Error 카피 | 실제 동작 동반(예: "No records yet. `Ingest sources` to fill this view.") |
| 상태 표현 | 색이 아닌 **표기 아이콘 + 텍스트 무게**로 변별(모노크롬 팔레트). error = 굵은 잉크 + alert |
| 색·치수 | 직기재 금지 — 디자인 토큰(`--*`)·컴포넌트(`Button`·`ListRow`…)만 § 참조 |
| 마이크로카피 | 실제 노출 문구는 표면 설계서가 소유(실텍스트 기재). 과금·CP 계측·구명칭 금지([[vinc.a.terms#§4]][[vinc.a.terms#§5]][[vinc.a.terms#§6]]) |

## 4. 표면 설계서 인덱스
- [[vinc.d.ingest]] — 표면 1 Intake (v0.2 — 온보딩 흡수)
- [[vinc.d.records]] — 표면 2 Records
- [[vinc.d.graph]] — 표면 3 Atlas (v0.2 — 3-레벨 시맨틱 줌, D16)
  - [[vinc.d.graph-views]] — Atlas 확장 관점 흐름·타임라인·합계 (v0.3, D43 채택 2026-07-24)
- [[vinc.d.search]] — 표면 4 Search
- [[vinc.d.packs]] — 표면 5 Packs
- [[vinc.d.settings]] — Settings 팝오버 (잠금 옵트인·MCP 관찰 패널)

## 5. 결정 기록·미결 트래커
- **승인(2026-07-10)**: D7 무암호 기본 / D15 5표면 트리 / D16 Atlas 3-레벨 /
  D17 레이아웃 사전 계산 / D18 영어 카피 시스템 — 정본 등재는 [[vinc.b.architecture#§2]],
  연구 근거는 [[vinc.d.ux-research]].
- D1 셸 = Tauri(기본) / D6 learner 표면 기본 off — **확정**([[vinc.b.architecture#§2]] D6;
  앱은 Tauri 로 출시됨). 구 "설계 §13 결정 대기" 서술은 폐기.
- **D11 MCP 서버 표면 — 채택.** 두 번째 프론트 도어(헤드리스). 도구 계약·정합 규약은 [[vinc.mcp]].
  Settings 하위 "MCP 연결 상태·도구 호출 로그" 관찰 패널([[vinc.d.settings]]).
- ~~P3 코드 착수 전 사이드카 계약 델타(레이아웃 잡·L0 집계·seed+depth 순회) 선행~~ →
  **완료.** P3 종료 + seed+depth 순회 구현됨([[vinc.mcp#§7]]·[[vinc.c.dataplane#§2]]).
  근거 [[vinc.d.ux-research]] §4.4·§7.

---

## 부록 Z. 미분류 원문 사실
(없음)

<!-- vinc-export
doc_id: vinc.d.graph
title: 표면 3. Atlas
origin: Vinc-docs/vinc/D/vinc.d.graph.md@5090690
chunks: 3
verified: true
content_hash: b2deba85c755d7000a80cd87a4caa90cd9c39aca05079c1673c90ec47f3ef21e
updated_at: 2026-08-01T16:20:35.065Z
-->

---
doc_id: vinc.d.graph
layer: D
domain: graph
title: Vinc 표면 3 — Atlas (3-레벨 시맨틱 줌, 전면 3D · 3d-force-graph)
status: draft
authority: SSoT
version: v0.3
owner: vinc
last_updated: 2026-07-16
inherits_from: [vinc.d.index]
tags: [vinc, D, screen, atlas, graph, force-graph, three-js]
---

# 표면 3. Atlas

> 그래프 탐색 표면 — 도메인 지도에서 시작해 렌즈·노드로 파고드는 3-레벨 시맨틱 줌
> (DEC D16). 렌더러 = **3d-force-graph(three.js 계열, MIT)** — **D21 개정으로 2.5D 권고를
> 폐기하고 전면 3D 채택**. Compare 좌우 렌즈만 **Sigma 2D 잔존**(`atlas.ts`).
> 레이아웃 = 사이드카 사전 계산 좌표 소비(D17).
> 데이터는 사이드카 `/graph`·`/domains`·`/decide`(localhost HTTP + 토큰), 제어는 Tauri IPC.
> 색·치수는 디자인 토큰만 참조([[vinc.d.index#§3]]). 설계 근거는 [[vinc.d.ux-research]] §3–4.
>
> **v0.3(2026-07-16) 개정:** 렌더러(D21)·노드 프리미티브(D35)·hover 문법(D32·D33)·4-State
> 카피(07-12 SaaS 톤)가 개정 이전 상태로 남아 있어 코드 실측(`app/src/surfaces/atlas3d.ts`·
> `copy.ts`) 기준으로 정정. 구현권위 = 코드.

## 1. 3-레벨 시맨틱 줌

| 레벨 | 노드 | 엣지 | 데이터 소스 |
|---|---|---|---|
| **L0 Domain Atlas** | 도메인 슈퍼노드(크기=레코드 수) | 도메인 간 결합(농도=cross_domain 엣지 수, D33) | `/domains` L0 집계(카운트+결합 행렬) |
| **L1 Domain Lens** | 선택 도메인의 노드 | 도메인 내 관계 + **가장자리 게이트웨이**(타 도메인 결합 노드를 경계에 반투명 표기 — 클릭 시 그 도메인 렌즈로 전환) | `/graph?domain=` |
| **L2 Node Focus** | 선택 노드의 이웃(깊이 1–2 ego) | 인접 엣지 | `/graph?seed=&depth=` (seed+depth 순회) |

- L0↔L1↔L2 는 줌 임계 통과·더블클릭으로 전환(연속 배율 아님 — 명시적 3단).
- 브레드크럼 `Atlas › {domain} › {node}` + `←` 히스토리 후퇴로 위치 상실 방지.
- 결합(cross_domain) 엣지는 색이 아닌 **dashed stroke + 양끝 도메인 아이콘 페어**로 변별.

## 2. 레이아웃

- 중앙: **3D 캔버스 전폭**(`atlas3d.ts` — `ForceGraph3D`). 노드 프리미티브는 **D35**:
  **Concept = 구** · **Domain = 구 + 적도 링**(토성형) · **Episode/Record = 작은 정육각기둥**
  (전 모서리 등길이) · **Bridge = 정팔면체**. 재질은 **D40**(프로스티드 Physical — 노드
  불투명도 하한 0.72 / 게이트웨이 0.35 + 환경맵 반사, 모노크롬 유지).
  엣지는 **D33** 헤어라인(튜브 폐기) + 곡률 0.18, **농도 = 가중치**.
  **좌측 필터 폼 없음** — L0 자체가 도메인 필터다([[vinc.d.ux-research]] §4.3).
- 다크 기본에서는 **D39** 반전 팔레트(어두운 배경 + 밝은 잉크 마크, fog·엣지·라벨·초점 동반 반전).
- 하단: 태그 레전드 칩(토글 = 표면 승격 + 텍스트 무게, 솔리드 색 금지).
- 우측: 상세 레일 — click 시에만 열림(레코드 발췌·태그·연결 목록·`Pin`/`Tag` 액션).
  L2 깊이 스테퍼(1–2)는 상세 레일 안에 국소화.
- 노드 상태 3단계는 점 표기(`dot-filled`/`dot-half`/`dot-empty`)로 변별(색 아님).
- 강조는 `--ring`(포인트) 아웃라인 1개만. 면 전체 톤 바 금지.
- View 패널(열린 층, D31): 2D↔3D 토글 · 노드 크기 스케일 · 라벨 밀도 · 화살촉 · 산개 ·
  레이아웃 프리셋(D34) — 사용자 소유(`localStorage`).

## 3. 상호작용 문법

| 제스처 | 동작 |
|---|---|
| hover | 이웃 유지 + **비이웃 이징 페이드 + 엣지 후퇴**(D32). 라벨은 hover 대상만 승격 |
| click | 우측 상세 레일 열기 |
| double-click | 해당 노드로 refocus(L2 재중심) |
| 줌 임계 통과 | L0↔L1↔L2 시맨틱 전환 |
| 게이트웨이 클릭 | 인접 도메인 렌즈로 전환 |
| `←` / breadcrumb | 탐색 히스토리 후퇴 |
| LOD | 줌 배율 임계 아래 라벨 숨김(허브 노드 예외) |

## 4. 4-State (카피 = D18 보이스 · D42 KO 덱)

> **문자열 정본은 코드** — `Vinc/app/src/copy.ts` `copy.atlas`. 전수 목록은 [[vinc.a.ui-copy#§6]].
> 07-12 SaaS 톤 개선(record→document · domain→topic) 반영분이다.

| 상태 | 표현 |
|---|---|
| idle | EmptyGuide: "No knowledge map yet. Add files or run a search — the map builds itself from your documents." CTA = `Import files` |
| loading | Skeleton 캔버스 + "Arranging {d} topics…" (스피너 비텍스트 `--info`) |
| success | L0 렌더. 툴팁 "{dom}: {n} documents · {k} links to other topics. Click to see more." / 게이트웨이 툴팁 "Crosses into {dom}. Click to follow." |
| error | ErrorBanner(`--danger` 굵은 잉크 + `alert-triangle`): "The map couldn't load. Retry anytime. Your documents are untouched." + `Retry` |

## 5. 참조
- 데이터 계약: 사이드카 `/graph`(seed+depth·좌표 포함)·`/domains`(L0 집계)·`/decide`.
  그래프 로직·레이아웃 계산은 UI 에 두지 않음(D17).
- 구현: `app/src/surfaces/atlas3d.ts`(`ForceGraph3D` 래퍼 — L0/L1/L2 정본) ·
  `atlas.ts`(Compare 좌우 2D 렌즈만 Sigma) · `atlas-prefs.ts`(View 패널 지속).
- 컴포넌트: `EmptyGuide`·`Skeleton`·`ErrorBanner`·`ListRow`.
- 연구 근거·DEC: [[vinc.d.ux-research]] §3–5, [[vinc.b.architecture#§2]],
  시각 언어 정본 [[vinc.d.atlas-visual-language]].

## 부록 Z. 미분류 원문 사실
(없음)

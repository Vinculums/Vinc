<!-- vinc-export
doc_id: vinc.d.records
title: 표면 2. Records
origin: Vinc-docs/vinc/D/vinc.d.records.md@5090690
chunks: 1
verified: true
content_hash: 7215514f746d923c00daa7be1c2f51568949b8e5d8f4d60466680ba39d124b75
updated_at: 2026-08-01T16:20:38.334Z
-->

---
doc_id: vinc.d.records
layer: D
domain: records
title: Vinc 표면 2 — Records (레코드 목록·열람)
status: draft
authority: SSoT
version: v0.2
owner: vinc
last_updated: 2026-07-16
inherits_from: [vinc.d.index]
tags: [vinc, D, screen, records]
---

# 표면 2. Records

> 레코드 목록·열람 표면. 데이터는 사이드카 `/recall`(preference 랭킹). 모든 행은
> Atlas 핸드오프 액션을 갖는다(탐색 파편화 해소 — D15). 색·치수는 디자인 토큰만
> 참조([[vinc.d.index#§3]]).

## 1. 레이아웃
- 좌: 도메인·태그 칩 필터(폼 아님 — 레전드 칩 토글). 정렬 = preference | recent.
- 본문: `ListRow` 목록(제목·도메인·태그·갱신일). 행 hover 액션: `Show in Atlas` · `Pin` · `Tag`.
- 우: 선택 레코드 상세(본문 발췌·연결 목록) — click 시에만.

## 2. 4-State (카피 = D18 보이스 · D42 KO 덱)

> **문자열 정본은 코드** — `copy.ts` `copy.records`. 전수 목록 [[vinc.a.ui-copy#§5]].
> 07-12 SaaS 톤 개선(record→document · domain→topic) 반영분.

| 상태 | 표현 |
|---|---|
| idle | EmptyGuide: "No documents yet." CTA = `Import files` |
| loading | Skeleton 목록 |
| success | 목록 + 카운트 헤더 "{n} documents · {m} topics" |
| error | ErrorBanner: "Documents couldn't load. Retry anytime. Nothing was changed." |

행 액션: `Show in Atlas` · `Pin` · `Tag`. 필터 플레이스홀더 "Search documents…"(`ui.recordsUi.filter`).

## 3. 참조
- 데이터 계약: `/recall`·`/pin`·`/tag`·`/touch`.
- 컴포넌트: `ListRow`·`Badge`·`EmptyGuide`·`Skeleton`·`ErrorBanner`.

## 부록 Z. 미분류 원문 사실
(없음)

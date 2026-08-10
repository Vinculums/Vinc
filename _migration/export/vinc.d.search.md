<!-- vinc-export
doc_id: vinc.d.search
title: 표면 4. Search
origin: Vinc-docs/vinc/D/vinc.d.search.md@5090690
chunks: 1
verified: true
content_hash: 7417d5ff2e92e81c057b35e39d3d42ca0887a89f196de2a1a86365bd418a9721
updated_at: 2026-08-01T16:20:39.238Z
-->

---
doc_id: vinc.d.search
layer: D
domain: search
title: Vinc 표면 4 — Search (하이브리드 검색)
status: draft
authority: SSoT
version: v0.2
owner: vinc
last_updated: 2026-07-16
inherits_from: [vinc.d.index]
tags: [vinc, D, screen, search]
---

# 표면 4. Search

> 의미(벡터)+키워드(BM25) 하이브리드 검색 표면. 데이터는 사이드카 `/search`(RRF).
> 모든 결과 행은 Atlas 핸드오프 액션을 갖는다(D15). 색·치수는 디자인 토큰만
> 참조([[vinc.d.index#§3]]).

## 1. 레이아웃
- 상단: 검색 입력(단일 필드 — 모드 토글 없음, 하이브리드가 기본). 도메인 칩 필터.
- 본문: 결과 `ListRow`(제목·스니펫·도메인·score 표기 `bar`). 행 액션: `Show in Atlas` · `Open`.

## 2. 4-State (카피 = D18 보이스 · D42 KO 덱)

> **문자열 정본은 코드** — `copy.ts` `copy.search`. 전수 목록 [[vinc.a.ui-copy#§7]].
> 07-12 SaaS 톤 개선 반영분.

| 상태 | 표현 |
|---|---|
| idle | "Search all your documents, by meaning or by keyword." |
| loading | Skeleton 결과 목록 + "Searching…" |
| success(0건) | "No results for '{q}'. Try a different term, or open the Atlas instead." + `Open Atlas` |
| success | 결과 목록 + "{n} results" |
| error | ErrorBanner: "Search couldn't run. Retry anytime. The index is intact." |

## 3. 참조
- 데이터 계약: `/search`(query, domain?, limit).
- 컴포넌트: `ListRow`·`EmptyGuide`·`Skeleton`·`ErrorBanner`.

## 부록 Z. 미분류 원문 사실
(없음)

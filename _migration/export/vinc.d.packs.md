<!-- vinc-export
doc_id: vinc.d.packs
title: 표면 5. Packs
origin: Vinc-docs/vinc/D/vinc.d.packs.md@5090690
chunks: 1
verified: true
content_hash: 3469f75424cac91fc2a92b0f24219ce6c7d4f4a2823d33c847e1b3102a23306b
updated_at: 2026-08-01T16:20:38.040Z
-->

---
doc_id: vinc.d.packs
layer: D
domain: packs
title: Vinc 표면 5 — Packs (팩 내보내기)
status: draft
authority: SSoT
version: v0.2
owner: vinc
last_updated: 2026-07-16
inherits_from: [vinc.d.index]
tags: [vinc, D, screen, packs, export]
---

# 표면 5. Packs

> 산출물 팩 생성·내보내기·**타인과 주고받기(D19)** 표면. 데이터는 사이드카
> `/activate`(컨텍스트 팩)·`/share`(공유 팩)·`/ingest/pack`(들여오기).
> learner 섹션은 D6(기본 off)를 따른다. 색·치수는 디자인 토큰만 참조([[vinc.d.index#§3]]).

## 1. 레이아웃
- 좌: 팩 구성 — 스코프 칩(도메인·태그) + **영역 스코프**(Atlas 에서 보던 seed+depth
  영역을 그대로 패키징, [[vinc.c.sharepack]] §1-2) + 형식(md|json|mcp-manifest|share).
- 본문: 기존 팩 목록(`ListRow` — 이름·레코드 수·크기·생성일). primary CTA = `Create a pack`.
- 하단: `Import a pack` — 받은 share pack(JSON)을 내 owner 로 들여온다(멱등).

## 2. 4-State (카피 = D18 보이스 · D42 KO 덱)

> **문자열 정본은 코드** — `copy.ts` `copy.packs`. 전수 목록 [[vinc.a.ui-copy#§8]].
> 07-12 SaaS 톤 개선(record→document) 반영분.

| 상태 | 표현 |
|---|---|
| idle | EmptyGuide: "Bundle your knowledge into a portable pack, or share it safely with someone else." CTA = `Create a pack` · `Share a pack` · `Import a pack` |
| loading | Progress: "Packing {n} documents…" / "Importing…" |
| success | "Pack ready: {n} documents, {size}." + `Reveal in folder` / 공유: "Pack ready: {c} concepts, {e} connections." / 들여오기: "Imported: {created} new, {matched} already here." |
| error | ErrorBanner: "The pack didn't finish. Try again. Your documents are untouched." |

공유 고지(`shareNote`): "The pack carries document content only. Your pins and tags are never shared."
— preference 신호 구조적 미포함(D19).

## 3. 참조
- 데이터 계약: `/activate`(owner, domain?, format) · `/share`(owner, domain?/tags?/seed+depth) ·
  `/ingest/pack`(pack, owner). 공유 팩 계약 정본: [[vinc.c.sharepack]] (DEC D19).
- 컴포넌트: `ListRow`·`Progress`·`EmptyGuide`·`ErrorBanner`·`Button`.

## 부록 Z. 미분류 원문 사실
(없음)

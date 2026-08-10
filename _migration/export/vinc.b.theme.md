<!-- vinc-export
doc_id: vinc.b.theme
title: 주제·타입 어휘 모델
origin: Vinc-docs/vinc/B/vinc.b.theme.md@5090690
chunks: 4
verified: true
content_hash: 17bc640916075082f6c05c0c81f76a1884fa704c1bafb1cda4d0de8fddc43af1
updated_at: 2026-08-01T16:20:21.699Z
-->

---
doc_id: vinc.b.theme
layer: B
domain: theme
title: 주제·타입 어휘 모델 (사용자 저작 타입 — D11-R2)
status: draft
authority: SSoT
version: v0.2
owner: vinc
last_updated: 2026-07-29
inherits_from: [vinc.b.architecture, vinc.a.terms]
tags: [vinc, theme, type, vocabulary, d11-r2, thesis]
---

# 주제·타입 어휘 모델

> **위상:** THESIS(T2 주제 미정 · T3 타입도 저작물 · T9 부담 분담)의 구현 측 정본.
> 노드/엣지 `type`을 **전역 고정 enum**에서 **주제(theme)별 사용자 저작 어휘**로
> 바꾼 D11-R2 를 코드/스키마 계약으로 착지시킨다. 화면 표면은 후속(다음 세션).

## 0. 한 문단 — 무엇이 왜 바뀌나

전역 고정 타입 enum 은 "남이 정한 타입 체계 강요"라 T3 를 위반한다. R2 는 타입을
**주제별 사용자 저작·소유 어휘**로 바꾸고, 백지 부담(T9)은 시스템이 **시작 어휘를
초안으로 제안**해 흡수한다. 시스템은 타입을 **제안(proposed)** 만 하고, 사용자가
**재가(authored)** 한다 — 몰래 확정하지 않는다(T1).

## 1. 모델 — Theme · Type

```
Theme { theme_id, title, guiding_questions[], vocabulary:{node,edge} }
Type  { name, kind:node|edge, scope:theme|shared,
        status:authored|proposed, provenance:user|system_draft|ingest_proposed }
```

- **저장 소재지 — 그래프 밖 메타.** 어휘는 `<data_dir>.themes.json`(DB 파일 형제 · `store/theme_store.py`)에
  산다. **그래프 테이블이 아니다** — 어휘는 해석의 *틀*이지 *내용*(노드·엣지)이 아니라서,
  어휘 편집이 그래프 데이터를 건드리지 않도록 분리한다(IMPL §3).
- **상태 전이(재가 흐름):** `proposed ──재가──▶ authored` · `proposed ──기각──▶ 제거` ·
  `proposed ──개명──▶ authored(new)`. `proposed`는 존재·MERGE 참여하되 확정 구조가 아니다.
- **guiding_questions 는 선택(T9).** 필수화하지 않는다(백지 부담 회피) — 시스템이 초안
  질문을 제안하고 사용자가 채우거나 건너뛴다. 도구는 값을 받기만 한다.

## 2. 어휘(frame) vs 물리 바인딩(content) 분리

`type`은 두 가지를 뒤섞기 쉽다: (a) *어떤 타입명이 존재하는가*(어휘) + (b) *그 타입이
어느 물리 테이블에 묶이는가*(그래프 바인딩). 이 둘을 가른다.

| 관심사 | 소재지 | 성격 |
|---|---|---|
| (a) 어휘 | `store/theme_store.py` → `themes.json` | 주제별·사용자 소유. 검증 게이트 아님 |
| (b) 물리 바인딩 | `api/fragment.py` `_NODE_MAP` (라이브) | 레거시 라우팅 표. 엔트리 없으면 캐리어로 |

- **레거시 노드/엣지 타입**(concept/domain/bridge/episode · relates_to/prerequisite_of/
  derives_from/connects_via)은 실 엔진·`api/query.py` 가 읽고 쓰는 **전용 테이블**을 유지한다.
  - **소재지 정정(2026-07-29):** 이 표가 가리키던 `engines/fragment_ingest.py`
    `_PHYSICAL_*_BINDINGS` 는 **호출부가 없다**(계보 참조용 — `api/server.py:300-309`
    주석이 명시). 라이브 경로는 `/ingest`·`/ingest/fragment` → `api/fragment.merge_fragment`
    이고, 그 `_NODE_MAP` 은 **5종**이다 — 위 4종에 **`record`**(→`Record`)가 더해졌고
    Record→Concept 는 `ATTACHES_TO{type}` 로 수용된다. 개방층(Entity/TYPED) 포팅도
    이 라이브 경로에서 산다(발주자 결정 2026-07-19). 아래 §3·§4 의 계약은 이 경로 기준.
- **그 밖의 사용자 저작 타입**은 범용 캐리어 `Entity{entity_id,type,title,owner_id,theme_id,
  tags}` + `TYPED{type}(Entity→Entity)`로 간다([[vinc.c.dataplane#§2]]). `theme_id` 컬럼이
  **노드 소유 = 주제 귀속**(THEME-DEEP §6)을 스키마에 각인한다 — `domain`은 승계하지 않는다.
- **"투기적 열 금지"와 무충돌:** 캐리어 열은 고정 컬럼(값만 사용자 저작)이고, 어휘는 그래프
  밖이다([[vinc.b.architecture#§5]] 명확화).
- **예외의 경계(DEC — [[vinc.b.architecture#§2]]):** content 캐리어 승인은 **`Entity`/`TYPED`
  두 테이블에 국한**된다. 새 사용자 저작 타입은 열 증설이 아니라 `type` **값**으로 흡수한다 —
  이 두 테이블에도 투기적 열을 늘리지 않고, 다른 테이블의 투기적 열을 정당화하지도 않는다.

## 3. ingest 계약 (D11-R2)

- `vinc_ingest(fragment, theme_id)` — `theme_id` 는 **선택**이며 **lazy 확정**된다.
  해석 순서 = 명시(`fragment.theme_id` — 미지 테마는 멱등 create) → 활성 테마 →
  개방 타입 최초 조우 시 `default` 테마 lazy 생성(`api/fragment.py` `_ThemeFold.resolve`).
  - **정정(2026-07-29):** 원안의 "**theme_id 필수**(없으면 ingest 전체 reject)"는 폐기됐다.
    그 문구는 휴면 경로 `engines/fragment_ingest.py::ingest_fragment` 독스트링에만
    잔존하며(호출부 0), 라이브 경로는 어휘 부재를 이유로 적재를 거절하지 않는다 —
    T9(백지 부담 흡수)와 정합. `themes=None`(테스트·sync/share 호출부)이면 어휘 편입은
    전부 무동작이고 `type` 문자열은 행에 그대로 남는다(손실 0).
- `type`을 **주제 어휘에서 조회**. 어휘 안 → 그대로 사용. 어휘 밖 → `status:proposed,
  provenance:ingest_proposed`로 **어휘에 편입(스킵 아님)** + 응답 `proposed_types`에 담음.
- **MERGE는 exact-match only 불변**: 노드 `id` / 엣지 `(from,type,to)` 정확 매칭. 유사도
  병합 영구 금지(THESIS T1). 바뀐 것은 `type` **해석 소재지**(전역 상수 → 주제 어휘)뿐.
- 응답: `{ merged:{nodes:{created,matched}, edges:{created}}, skipped:0, proposed_types:[…], warnings:[…] }`.

## 4. 재가 표면 — HTTP 전용 (MCP 미노출)

그래프 쓰기는 `vinc_ingest`, **어휘 쓰기는 사이드카 HTTP 라우트**로 분리 — 두 write 가
서로 다른 저장소(그래프 → 임베디드 스토어 · 어휘 → `themes.json`)를 건드려 감사 경계가
또렷하다([[vinc.mcp#§4]]).

- 주제: `GET /themes` · `POST /themes`(생성) · `POST /themes/activate`.
- 타입: `GET /types`(읽기) · `POST /types/ratify|reject|rename`(쓰기 — **사용자 승인
  전제**). 프로토콜 강제 여부는 열린 항목([[D11-R2 §10]]).
- **표면 정정(2026-07-29):** 원안이 적은 `vinc_themes_list/create/activate` ·
  `vinc_types_list/ratify/reject/rename` **7종 MCP 도구는 구현되지 않았다**
  (`vinc_mcp/tools.py` `TOOLS` grep 0건 — 현행 18종은 읽기 12·쓰기 6이고 어휘 도구는
  그중에 없다). 어휘 확정은 **사람의 행위**라 호스트가 자율로 부를 자리에 두지 않은
  것으로, 계약의 취지(사용자 승인 전제)는 그대로 살아 있고 소재지만 HTTP 다.
  헤드리스 재가는 사이드카 라우트를 직접 호출한다.

## 5. 시드·마이그레이션·팩

- **시드(T9):** 새 주제 생성 시 구 전역 enum 을 `system_draft/proposed` 초안으로 시드
  (강등하되 삭제 안 함). 구 enum 은 여기서만 산다.
- **마이그레이션:** `store/migrate_themes.py` — `legacy` 주제 시드 + 그래프 실재 타입만
  `authored` 재가. **themes.json 만 기록, 그래프 미변경**([[Vinclums/Vinc docs/MIGRATION.md]]).
- **팩:** `authored` 어휘만 이전(`proposed`는 잠정이라 미탑재 — THESIS §6). `activate(...,
  theme_id, themes)` 가 필터.

## 6. 크로스도메인 브릿지 (T8)

주제 간 브릿지 엣지 타입은 `scope:shared`로 자동 제안(`proposed`) + 사용자 재가 — 결합마다
백지에서 타입을 짜지 않되(T9) 확정권은 사용자에게(T1). 상세 정책은 후속 DEC.

## 7. THESIS 정합

| 항목 | 매핑 |
|---|---|
| T1 확정=재가 | proposed → authored 재가로만 확정, 시스템은 제안만 |
| T2 주제 미정 | 어휘는 주제별, 전역 taxonomy 없음 |
| T3 타입=저작물 | 어휘가 주제의 지문, 사용자 소유 |
| T4 소유·이전 | 어휘는 주제에 귀속, 팩과 함께 이전(authored만) |
| T7 오프라인 | themes.json 로컬 JSON, 외부 호출 0 |
| T9 부담 분담 | 시작 어휘 초안 시드, 재가는 사용자 |

## 부록 Z. 미분류 원문 사실
(없음)

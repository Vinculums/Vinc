# 계층 아키텍처 설정 — PREFIX vinc

PREFIX: vinc
ACTIVE_PREFIX: vinc

```yaml
PREFIXES:
  - id: vinc
    label: Vinc — 독립 설치형 데이터 마이닝 데스크톱 앱
ACTIVE_PREFIX: vinc
```

## 로컬 소스 경로 (RE REFERENCE-DOCS.md 계약)

| 계층 | 로컬 경로 | 비고 |
|---|---|---|
| vinc-A | `vinc/A/` | 용어·표기·금지 어휘 SSoT |
| vinc-B | `vinc/B/` | 불변 원칙 Delta (SSoT 루트) |
| vinc-C | `vinc/C/` (+ 선례상 일부 `vinc/`-root) | 제품 Delta ([[weg.c.memory-standalone]] 상속, 재기재 금지) + 웹/계정 as-built(`vinc.c.web-vincs-app`, 앱과 물리 분리) |
| vinc-D | `vinc/D/` | 화면설계서 (5표면 + Settings, DEC D15) |

## doc_id 규칙

형식: `vinc.{a|b|c|d}.{domain}` (RE 점 표기 계약). 본문 `## N.` 섹션 헤딩.
정본 ID: `VINC-{LAYER}-{SEQ}` (대문자) / doc_id layer 는 소문자 — 혼용 금지.

## 상위 정본

- 설계 SSoT: `weg-9000-docs/…/weg.c.memory-standalone.md` (v0.4) — vinc-C 가 상속.
- 흡수 절차: `weg-9000-docs/WORK-ORDERS/WO-MEM-SA-001.md` — §3 반입 금지 = 하드 룰.
- 표기 기준: RE `NOTATION.md`(프리픽스 무관) 상속 + `vinc.a.terms` Delta.

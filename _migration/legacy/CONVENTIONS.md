# CONVENTIONS — Vinc 도메인 바인딩 (revamp 인스턴스)

> revamp 엔진은 prefix 무관 템플릿이다. 본 파일이 Vinc-docs 인스턴스의 바인딩이며,
> `vinc.profile.yml` 과 짝을 이룬다. `stage1_rules` 와 `validate_stage1.py` 가
> 이 값을 읽어 동작을 분기한다.

## §1 핵심 바인딩

| 항목 | 값 | 설명 |
|---|---|---|
| **prefix** | `vinc` | doc_id 접두사. `vinc.profile.yml` supported_prefixes 에 등재 |
| **domain** | `vinc` | 제품/도메인 이름 |
| **structure_mode** | **(a) A/B/C 3계층 SSoT** (+ D 화면설계서) | RE REFERENCE-DOCS.md 계약 준수 |
| **publish_safety** | `lenient` (off) | self-documentation 테넌트 — Confluence 미발행 |
| **layout** | `vinc/{A,B,C,D}/{doc_id}.md` (RE 레이아웃) | revamp by-resource 대신 --file 모드로 검증(weg 동형) |
| **profile** | `vinc.profile.yml` | 도메인 프로파일 오버라이드 지점 |

## §2 원천 바인딩 (정본 계층)

| 원천 | 위치 | 권위 |
|---|---|---|
| 설계 SSoT | `weg-9000-docs/…/weg.c.memory-standalone.md` (v0.4) | vinc-C 가 `inherits_from` 상속 |
| 흡수 절차 | `weg-9000-docs/WORK-ORDERS/WO-MEM-SA-001.md` | §3 반입 금지 = 하드 룰 |
| 구현 1차 | `Vinculums/Vinc` (라이브 코드) | `(구현권위: Vinc/{path})` |
| 추출 원본 | `weg-9000-memory` (읽기 전용) | [참고] 코드 계보만 |

## §3 검증 실행

```bash
# STAGE 1 결정론 게이트 (단일 문서 — RE 레이아웃)
python scripts/validate_stage1.py \
    --file /home/user/Vinc-docs/vinc/A/vinc.a.terms.md \
    --profile /home/user/Vinc-docs/vinc.profile.yml \
    --doc vinc.a.terms
```

- exit code: `0`=PASS · `1`=FIX-only · `2`=BLOCK · `3`=내부 오류.
- 도메인 금지 어휘(F1/F2/F7)는 여기서가 아니라 `Vinc/scripts/gate_forbidden.py`
  가 강제한다(vinc.a.terms §7·§9 — 금지어 정의 문서 자신의 오탐 회피).

> 레거시 흡수(revamp `ingest_legacy`)는 Vinc 에 미해당 — 신규 구축이므로 §3 생략.

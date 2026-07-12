# Vinc

**독립 설치형 데이터 마이닝 데스크톱 앱.** 원문 코퍼스를 적재(ingest) →
그래프·벡터로 구조화 → 탐색·검색·패턴 추출 → 팩으로 내보내는 오프라인 우선
도구다. 외부 인프라·API 키·서버 없이 설치 직후 동작한다.

> 제품명 Vinc — 라틴어 *vinculum*(결속·연결 고리). 데이터 마이닝 도구이며
> "기억/학습" 서사를 사용하지 않는다.

## 정본 (SSoT)

| | |
|---|---|
| 설계 SSoT | `weg-9000-docs/CONTEXT/reference-docs/weg/C/weg.c.memory-standalone.md` (v0.3) |
| 흡수 절차 | `weg-9000-docs/WORK-ORDERS/WO-MEM-SA-001.md` (§3 반입 금지 = 하드 룰) |
| 용어·금지 어휘 | `Vinclums/Vinc-docs vinc/A/vinc.a.terms.md` |
| 실행 계획 | `Vinclums/Vinc-docs PLAN.md` |

## 아키텍처 (전략 B — 임베디드 그래프 스토어)

```
Vinc 데스크톱 앱 (Tauri 셸) → localhost HTTP → Python 사이드카(api/ + engines/)
  → GraphStore 인터페이스 → 임베디드 스토어(LadybugDB) + 로컬 임베딩(다국어 mpnet 768d)
```

- 스토어 백엔드 교체 가능(`store/backend.py` 의 `GraphStore` 추상화 뒤).
- 라이선스 전 구성 MIT/Apache 계열 (GPLv3 미포함).

## 게이트

```bash
python scripts/gate_forbidden.py          # 반입 금지 F1–F7 (WO §3 하드 룰) — 0건 요구
python verify.py                          # 플랜 순수성(무 DB) — M2 코드 이식 후
python e2e_live.py --backend embedded     # 백엔드 e2e — M3 이후
```

## 상태

부트스트랩(M1). 코드 이식(M2~)은 실행 계획 PLAN.md 마일스톤을 따른다.

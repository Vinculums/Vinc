# Vinc UI 인벤토리·감사 (design P0)

| | |
|---|---|
| 지위 | design 엔진 P0 산출물(준비·감사) — [[weg.c.design]] METHODOLOGY P0 |
| 대상 | 콘솔 화면 5 "나의 기억"(weg.d.console) **구조 골격** + viz GraphView |
| 원칙 | **구조만 승계, 카피·서사 미승계**(WO-MEM-SA-001 A5 · §3 F2 · 설계 §3-4) |
| 금지어 | 승계 산출물은 `vinc.a.terms` §5(F2) 통과 필수 — 감사 단계에서 카피 선제거 |
| 상태 | draft (토큰·컴포넌트 스펙은 M4/P1) |

## 1. 흡수 매트릭스 (화면 5 → Vinc 마이닝 뷰)

설계 §3-4 흡수 매트릭스의 design 관점 구체화. **O=구조 승계 / X=금지.**

| 화면 5 자산 | 승계 | Vinc 대응 | 비고 |
|---|---|---|---|
| 탭 패턴(2탭) | **O** | 그래프 마이닝 ↔ 팩 내보내기 | `Tabs` 컴포넌트. 탭 상태값 영어 정본 |
| 도메인 필터 칩(오픈셋·하드코딩 금지) | **O** | 데이터셋 구획 칩 | `GET /domains` 프로브 동형. `domain` = 데이터셋 구획(vinc.a.terms §3) |
| 4-State 상태 머신(idle→loading→success→error 강등) | **O** | 전 뷰 공통 | graceful degradation 계약 유지 |
| 팩 생성 플로우(Activate) | **O** | 팩 내보내기 뷰 | 생성 후 버튼 disabled(중복 방지) |
| `EmptyGuide` 몰리큘(가르치는 empty state) | **O (구조)** | 빈 데이터셋 온보딩 | **카피 재작성**(§3) |
| `MomentCard` 몰리큘(완료 모먼트, 절제) | **O (구조)** | 팩 내보내기 성공 | 카드 1장·point 토큰만·애니메이션 없음·`role="status"`. **카피 재작성** |
| viz **GraphView** 임베드 | **O** | 그래프 마이닝 뷰 렌더러 | 렌더러 재사용(WO A7) |
| DS 어휘 14종(Tabs·Table·Badge·Card·Stat·Separator·Tooltip·Collapsible·Progress·Skeleton·ListRow·Button·EmptyState·ErrorBanner) | **O** | 전 뷰 | 카드 남용 대신 이 어휘 우선 |
| 헤더 "프리뷰" 배지 | O(조건부) | 미연동 표면 한정 | Vinc 는 실연동이므로 대부분 제거 |
| **"나의 기억"·"My Memory" 뷰 명칭** | **X** | "레코드 탐색"·"그래프 마이닝" | §3 F2 |
| **"학습을 자산으로"·"배운 것"·"개인 학습기억" 서사** | **X** | 마이닝 서사 | §3 F2 |
| **"학습법(lessons)" 집계·MomentCard 본문** | **X(기본)** | learner 미승계(D6) | 엔진 코드 존치·기본 비활성 |
| CP `services/memory` 과금 연동(usage_record) | **X** | 없음 | F1 하드 제외 |

## 2. 5 마이닝 뷰 인벤토리 (설계 §3-1)

| 뷰 | 화면 5 원형 | 주요 DS 컴포넌트(초안) | 상태 표면 |
|---|---|---|---|
| 적재(ingest) | (신규 — 화면 5 무) | `Button` · `Progress`(잡 진행률) · `ListRow`(inbox) | 잡 queued→running→done→failed(설계 §3-1-1) |
| 레코드 탐색(recall) | My Memory 탭 일부 | `Table` · `ListRow` · `Badge`(pin/tag) | 4-State |
| 데이터셋 분포(domains) | 도메인 필터 칩 | 오픈셋 `Badge` 칩 · `Stat` | 0건 → `EmptyGuide` |
| 그래프 마이닝(graph/decide) | My Memory 그래프 카드 | **GraphView**(임베드) · `Card` | 4-State + 강등 |
| 검색(search) | (신규) | 입력 + `Table`(하이브리드 결과) · `Skeleton` | 4-State |
| + 팩 내보내기(activate) | Activate 탭 | `Button` · `MomentCard`(성공) | 생성 후 disabled |
| + 백업(backup) | (신규 — 설계 §3-1-1) | `Button` · `Progress` | 잡 경유 |
| + 온보딩/로그인 | (신규 — 설계 §3-3) | 폼 · **비암호화 고지 배너** | 프로필 구획 |

## 3. 카피 재작성 규칙 (F2 선제거)

승계하는 `EmptyGuide`·`MomentCard` 구조는 유지하되 문자열은 마이닝 서사로 교체.
**UI 카피 언어는 영어 기본·중점(설계 v0.4 ⓐ)** — 아래 표는 화면 5(한국어 원본)의
학습 프레이밍 카피를 제거·대체하는 *방향*을 보인 것이며(원본 대조용 한국어 병기),
확정 카피는 M5 화면설계서(pm 파이프라인)에서 **영어로** 저작한다. 본 감사는
**금지 카피 목록과 대체 방향**만 고정한다:

| 화면 5 카피(금지) | 대체 방향(마이닝) |
|---|---|
| "아직 학습된 도메인이 없어요" | "아직 적재된 데이터셋이 없어요" |
| "세션을 진행하면 배운 것이 에피소드로 적재돼요" | "inbox 에 원문을 넣으면 레코드로 구조화돼요" |
| "내 학습이 이식 준비됐어요" | "팩 내보내기가 준비됐어요" |
| "에피소드 N건 + 학습법 M건" | "레코드 N건" (learner 기본 비활성 — D6) |

> 최종 문자열은 `vinc.a.terms` §5(F2) grep + design `--surface` 게이트를 통과해야
> 한다. 본 감사는 P0 근거이며, 시각 스펙(색·간격·형태)은 토큰 확정(M4) 후
> `ref_gate`(토큰/컴포넌트 참조만, 직기재 금지)로 강제한다.

## 4. 다음 단계 (design 로드맵)

| Phase | 작업 | 마일스톤 |
|---|---|---|
| P1 | `design-tokens.json`(DTCG) + `dtcg_validate`·`a11y_audit` PASS | M4 |
| P3 | `gen_tokens.py` → `tokens.css`(라이트 우선 + `[data-theme=dark]`, `--check` 멱등) | M4 |
| P4 | `scaffold_design_md.py` → `DESIGN.md` | M4 |
| — | 화면설계서 `ref_gate` · Tauri `app/src` `--surface`(D-S01/D-S02) | M5 |

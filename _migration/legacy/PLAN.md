# Vinc 실행 계획 (PLAN)

> 마일스톤 정본. `Vinc/README.md` 가 인용하는 실행 계획이다. 설계 로드맵(§11 P0–P4)·
> WO(WO-MEM-SA-001 §4 S0–S4)와 1:1 대응하며, 부트스트랩 단계는 M1–M3 로 표기한다.

## 단계
| 단계 | 내용 | 완료 게이트 |
|---|---|---|
| **M1** 부트스트랩 | 레포 골격 + 반입 금지 게이트(`scripts/gate_forbidden.py`) | 게이트 PASS(F1–F7 0건) |
| **M2–M3** 코드 이식 | 엔진·api 이식, `GraphStore` 추상화 준비 | 무 DB 테스트 PASS |
| **P0** 스토어 추상화 | `GraphStore` + 백엔드 이관, 7 엔진·api 치환 | 회귀 0 확인 후 P1 |
| **P1** 임베디드 백엔드 | 임베디드 어댑터 + 로컬 임베딩 기본 | e2e(임베디드) PASS |
| **P2** 사이드카 | `api/server.py` + 토큰 인증 + inbox 적재 | CLI ↔ HTTP 동등성 |
| **P3** UI (= M5) | Tauri 셸 + 5표면 + Settings(4-State, DEC D15) + Atlas 3D 그래프(D21) + 팩 내보내기 | 오프라인 왕복 시나리오 + 반입 금지 grep 0건 |
| **P4** 패키징 | 3 플랫폼 설치본·서명·업데이트 | 클린 VM 설치 → 오프라인 왕복 PASS |

## M5(P3) 파운데이션 — 본 세션 산출
디자인 시스템 파운데이션 우선: 정본 토큰(`DESIGN/design-tokens.json`+`tokens.css`) · Notation
아이콘(`DESIGN/assets/icons/`) · 화면설계 시드(`vinc/D/vinc.d.*`) · 용어 정본(`vinc/A/vinc.a.terms.md`).
UI 코드 구현(Tauri 셸·표면·Atlas 3D 뷰)은 P0–P2 선행 완료 후 착수 — **P0–P4 전부 완료**
(현행 릴리스 v0.6.7 라이브 · 배포 = GitHub Releases + vincs.io). 이 표의 M1–P4 는 완료된
초기 마일스톤 기록이며, 그 이후의 현행 현황은 `NEXT-STEPS.md` + [[vinc.c.p5-remote-rollout]] §5,
릴리스별 변경은 `Vinc/CHANGELOG.md` 가 정본이다.

## 아키텍처 계약
- 제어 = Tauri IPC, 데이터 = localhost HTTP(사이드카 `127.0.0.1` + 세션 토큰).
- 그래프 렌더러 = **3d-force-graph(three.js 계열, MIT)** — D21 개정으로 지도 관점 3D 채택.
  Sigma 2D 는 Compare 좌우 렌즈에 더해 **2D 관점 자체**가 쓴다(D46 — 구 2D 는 3D 씬을
  z=0 으로 누른 투영이었고, 이제 독립 렌더러다)([[vinc.d.graph]]).
- 전 과정 오프라인 — 외부 인증·필수 외부 키·과금·학습 프레이밍 반입 금지([[vinc.a.terms]]).

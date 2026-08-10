<!-- vinc-export
doc_id: vinc.next-session
title: Vinc 다음-세션 핸드오프
origin: Vinc-docs/vinc/NEXT-SESSION.md@5090690
chunks: 4
verified: true
content_hash: b4b96e331ca4474b5f30424acec9bbe9eea0546c958fc78eaeccc8665f012c3b
updated_at: 2026-08-01T16:20:14.863Z
-->

---
doc_id: vinc.next-session
layer: C
domain: handoff
title: Vinc 다음-세션 핸드오프 (P0–P2 + MCP Push MVP 완료 → P3 UI 착수)
status: draft
authority: SSoT
version: v0.3
owner: vinc
last_updated: 2026-07-06
inherits_from: [vinc.arch, vinc.mcp, vinc.c.dataplane]
tags: [vinc, handoff, next-session, roadmap]
---

# Vinc 다음-세션 핸드오프

> ⚠ **SUPERSEDED (2026-07-16 기준).** 이 핸드오프는 **2026-07-06 시점**(P0–P2 + MCP Push MVP 완료
> → P3 착수)의 스냅샷이다. 이후 P3~P5 를 거쳐 **원격 서버 라이브·MCP 커넥터 등록·v0.1.6 릴리스·
> vincs.app 웹/계정 프로덕션 라이브**까지 진행됐다. **현행 현황 정본:** `NEXT-STEPS.md`(후속 계획)
> + [[vinc.c.p5-remote-rollout]] §5 장부(서버) + [[vinc.c.web-vincs-app]](웹). 아래는 07-06 당시
> 상태로 보존한다.

> 맥락 재구성 없이 다음 세션이 착수할 수 있도록 **현재 상태·다음 작업·진입점**을 정리한다.
> **중요 변화(v0.3):** 순위 1 **MCP Push MVP 완료**(`vinc_mcp/` + `engines/fragment_ingest.py` +
> 인라인 `POST /ingest`, 아래 §1). D11/스켈레톤 원안과 실제 스키마의 델타는
> [[vinc.mcp.skeleton#§10]] 에 정리됨. 이제 P3(UI) 가 다음 순위.
> **이전(v0.2):** 데이터플레인 코드 M1–M4(=**P0·P1·P2**)가 `Vinclums/Vinc` main 에 머지됨.

## 1. 현재 상태 (완료·머지됨)
### 코드 (`Vinclums/Vinc` main)
- **P0** 스토어 추상화: `store/backend.py` `GraphStore`/`open_store` — 7 엔진 드라이버 일원화.
- **P1** 임베디드 백엔드: kuzu(0.11.3) + 스키마 선행 DDL + 5 엔진 + `pack_ingest` + **하이브리드 검색**
  (벡터 HNSW + BM25 RRF) + 방언 정합.
- **P2** 사이드카: `api/server.py`(stdlib HTTP, `127.0.0.1` + 세션 토큰) + `api/jobs.py`(단일 워커 큐=쓰기
  직렬화) + `api/auth.py`(argon2id 로컬 로그인) + `api/backup.py`. CLI 11 서브커맨드 1:1 라우트.
- `verify.py` **177 green**(신규 38 — fragment_ingest 14·server /ingest 3·vinc_mcp tools/audit/
  lifecycle/server 21) · 반입 금지 게이트 v0.4 PASS. **잔여 #10**: 실 임베딩 모델 로드 e2e +
  스냅샷 복원 왕복 = 드라이버 설치·네트워크 머신 실측(현 환경 스킵 19–21건, mcp SDK 유무에 따라 다름).
- **MCP Push MVP 완료**: `vinc_mcp/`(`server.py` FastMCP stdio · `tools.py` 5 tool · `bridge.py` ·
  `lifecycle.py` attach/spawn+lockfile refcount(실 서브프로세스 왕복 검증됨) · `audit.py` §6 로그) +
  `engines/fragment_ingest.py`(POST /ingest MERGE, 실 스키마 대응) + `api/server.py` 인라인
  `POST /ingest`. `pyproject.toml` extra `vinc[mcp]`(PyPI `mcp`). 델타·미지원 범위는
  [[vinc.mcp.skeleton#§10]] — `record` 타입·자유 `props{}`·`inherits_from`·`vinc_graph`
  seed+depth 순회는 **미구현**(실제 임베디드 스키마에 대응 컬럼/플랜 없음, 후속).

### 문서·디자인 (`Vinclums/Vinc-docs` main)
- RE 계약 A/B/C/D: `vinc.a.terms`(v0.4 금지어 SSoT)·`vinc.b.architecture`(불변 원칙)·`vinc.c.dataplane`
  (P1 as-built)·`vinc.d.index`(8화면)+`graph`+`ingest`. `master-id-map.yml`·`CONVENTIONS`·`vinc.profile.yml`.
- 디자인 시스템: `DESIGN/`(Ink&Paper 토큰·tokens.css·43 Notation 아이콘 v2·gallery·profile). 게이트 PASS.
- **MCP**: `MCP-INTERFACE`(D11 계약, v0.2)·`MCP-SKELETON`(Push MVP 골격+§10 as-built 델타, v0.2)·`ARCHITECTURE`(다이어그램).

### Figma (WEG's team, file `WUXLeDDNBqih2KTZOz9wCX`)
Foundations(색 변수) · Screens(8화면, 아이콘 임베드) · Icons(43 컴포넌트 `ic/*`).

## 2. 다음 작업 (우선순위 · 진입점 · 이제 블로커 없음)
| 순위 | 작업 | 진입점 / 산출 | 상태 |
|---|---|---|---|
| 1 | ~~MCP Push MVP~~ | `vinc_mcp/` + `engines/fragment_ingest.py` + 인라인 `/ingest` | **완료**(위 §1) |
| 2 | **P3 UI (Tauri 셸 + 8화면)** | `app/` 신규 — Tauri 셸 + 8화면(`[[vinc.d.index]]` + Figma) + **Sigma.js GraphView** + 로컬 로그인. 제어=IPC·데이터=사이드카 HTTP([[vinc.arch]]) | **착수 가능** — 지금 1순위 |
| 3 | **Figma 관찰 패널** — "MCP 연결·도구 호출 로그"(설정 하위) | `[[vinc.mcp.skeleton]]` §6 로그 스키마 시각화(`mcp-audit.jsonl` 실제 기록 확인됨). Figma Screens 페이지 프레임 추가 | 로그 계약 확정·실제 기록 검증됨 |
| 4 | **MCP 후속 갭 메우기** — `vinc_graph` seed+depth 순회 · `record` 타입/자유 `props{}` 스키마 검토 | `api/query.py` 에 순회 플랜 추가([[vinc.mcp.skeleton#§10-2]]); record/props 는 실사용처 확인 후 스키마 확장 여부 재검토([[vinc.mcp.skeleton#§10-3]]) | 신규 — 실사용 검증 후 착수 |
| 5 | **#10 실측** — 실 임베딩 모델 e2e + 스냅샷 복원 왕복 + kuzu 다중 Connection MVCC | 드라이버 설치·네트워크 머신 | 환경 대기 |
| 6 | **Pull**(과거 대화 이력 배치 마이닝) | 별도 DEC — 난제는 이력 출처(내보낸 로그→`inbox/` 또는 memory MCP) | Push MVP 이후(완료) — 착수 가능 |

**P3 게이트(설계 §11 / WO S3)**: 오프라인 수동 왕복(계정→로그인→적재→탐색→pin→팩 내보내기) +
반입 금지 grep 0건 + 디자인 표면 게이트(D-S01/D-S02).

## 3. 정본 문서 맵
| 주제 | 문서 |
|---|---|
| 용어·금지 어휘(v0.4) | `vinc/A/vinc.a.terms.md` |
| 불변 원칙 | `vinc/B/vinc.b.architecture.md` |
| P1 as-built 데이터플레인 | `vinc/C/vinc.c.dataplane.md` |
| 아키텍처·유저플로우 | `vinc/ARCHITECTURE.md` |
| MCP 계약(D11) / 스켈레톤 | `vinc/MCP-INTERFACE.md` / `vinc/MCP-SKELETON.md` |
| 8화면 화면설계 | `vinc/D/vinc.d.index.md` (+graph·ingest) |
| 디자인 시스템 | `DESIGN/DESIGN.md`·`design-tokens.json`·`assets/icons/README.md` |
| 실행 계획 | `PLAN.md` |
| 상위 설계 SSoT(참조만) | `weg-9000-docs/.../weg.c.memory-standalone.md`, `WORK-ORDERS/WO-MEM-SA-001.md` |

## 4. 재현 게이트
```sh
# 코드 무결성(무 DB)
cd Vinc && python verify.py
# 반입 금지 (코드·문서 0건)
python Vinc/scripts/gate_forbidden.py --root Vinc
python Vinc/scripts/gate_forbidden.py --root Vinc-docs
# 디자인 토큰·D-layer
python weg-9000-design/scripts/gen_tokens.py --profile Vinc-docs/DESIGN/vinc.profile.yml \
  --src Vinc-docs/DESIGN/design-tokens.json --out Vinc-docs/DESIGN/tokens.css --check
python weg-9000-design/scripts/validate_design.py --tokens Vinc-docs/DESIGN/design-tokens.json \
  --profile Vinc-docs/DESIGN/vinc.profile.yml
```
`vinc_mcp/server.py` 테스트(2건)는 mcp SDK 미설치 시 skip 된다 — 실행하려면
`pip install -e ".[mcp]"`(또는 `pip install mcp`) 후 `python verify.py` 재실행.

## 5. 범위·경계
- 관리 대상: **`Vinclums/Vinc` · `Vinclums/Vinc-docs`** 둘뿐. `weg-9000-docs`(설계 SSoT)는 참조만·미변경.
- Vinc ≠ weg-9000 — 반입 금지 F1/F2/F7 유지, 상위 설계는 링크(`[[weg.c.memory-standalone]]`)로만 참조.

## 부록 Z. 미분류 원문 사실
(없음)

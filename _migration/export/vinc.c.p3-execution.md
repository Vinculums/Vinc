<!-- vinc-export
doc_id: vinc.c.p3-execution
title: P3 실행 계획 — 코드 고도화 → Figma 역동기화
origin: Vinc-docs/vinc/C/vinc.c.p3-execution.md@5090690
chunks: 5
verified: true
content_hash: 4bedb92cbd30050ce3cf2ea92fa07f30005dc7c18bccb07e6c7e5c8bae5c71a3
updated_at: 2026-08-01T16:20:27.101Z
-->

---
doc_id: vinc.c.p3-execution
layer: C
domain: p3-execution
title: P3 실행 계획 — 코드 고도화 → Figma 역동기화 (Vinc 단독 트랙)
status: draft
authority: SSoT
version: v0.1
owner: vinc
last_updated: 2026-07-10
inherits_from: [vinc.d.atlas-visual-language, vinc.d.desktop-shell, vinc.c.sharepack]
tags: [vinc, C, plan, p3, execution, figma]
---

# P3 실행 계획 — 코드 고도화 → Figma 역동기화

> **범위 결정(발주자, 2026-07-10): weg-9000 측 작업은 범위 밖 — Vinc 만 작업한다.**
> weg-9000 로드맵(LEARNING-SYSTEM-ANALYSIS §3 — validate_learning.py·거버넌스 복구
> 등)은 원천 레포 문서로 남되 본 트랙에서 실행하지 않는다. Vinc 가 소비하는 원천
> 데이터 품질 결손은 ingest 입장 검증(§E3)으로 Vinc 경계에서 방어한다.
>
> 순서 원칙: **코드가 먼저, Figma 는 마지막에 역동기화**(코드가 이미 v0.2 정본을
> 앞서 구현했으므로, Figma 는 새 시안이 아니라 as-built 미러를 만든다).
> 각 Phase 는 게이트(빌드·verify·gate_forbidden·Playwright 실측) PASS 후에만 다음.

## 완료 기록 (2026-07-10)

**전 Phase 완료.** A(3D Atlas — D21 개정 포함) · B(lasso+Share/Import 시트, /share ids) ·
C(Multiples/Matrix) · D(다크·라이브 검색·샘플 팩 D30 — Tauri 필요분은 P4 분리 유지) ·
E(promote-candidates·inbox 잡·layer/doc 푸시다운 — domain/tag 는 Chunk 열 부재로 P4
스키마 델타 이관) · F(Figma `v0.2 — As-built (code mirror)` 페이지: Records/Atlas 3D/
Components 벡터 프레임 + 1차 시안 4프레임 deprecated/absorbed/superseded 계보 마킹.
비트맵 미러는 프록시의 figma.com 자산 업로드 차단으로 벡터 as-built 로 대체).
검증: verify 175 pass · 양 게이트 PASS · build PASS · Playwright/curl 라이브 실측.

**P3.1 후속(같은 날, D31)**: 발주자 질의("평면으로도, 노드 구성 커스텀 가능한가")
→ D31 등재([[vinc.d.atlas-visual-language]] §1.5 — 열린 층/닫힌 층 2층 정책) →
앱 구현(2D↔3D 토글·크기·라벨·화살촉·산개, 인캔버스 View 패널, localStorage).
사이드카 계약 변경 없음.

**P3.2 후속(같은 날, D32)**: 발주자 3원칙(MOC 허브·고아 최소화·hover 집중) →
D32 등재(§1.6) → 구현: `GET /suggest-ties`(태그·도메인 근사, 테스트 3) +
Unlinked 필터 + Bind(fragment MERGE 재사용) + pin 허브 서열(레이아웃·크기,
Concept pinned 열은 P4 델타) + hover rAF 이징·엣지 paper 후퇴 +
`#atlas/trace/<id>` 딥링크. weg-9000 실데이터로 고아 생성→제안→Bind→재제안
제외 왕복 실측(API+UI).

**P3.3 후속(같은 날, D33)**: 발주자 지적("연결선이 뚱뚱한 반투명 튜브 — 더
부드럽고 명확하게") → 원인: linkWidth 지정 시 3d-force-graph 가 원통 튜브
메시로 렌더(§1.2 의 "헤어라인" 사양 위반이었음) → 엣지 헤어라인+곡률 0.18+
농도=가중치, 노드 재질 플랫 셰이딩 Standard+반구광. 마크 픽스처
(`/dev/marks.html`)와 실데이터 L0/L1 로 전후 실측.
보강(발주자 문답 — 발선 위치): 포트/꼭지점 발선 기각·중심 발선 유지(§1.2
근거 기록), 화살촉 표면 오프셋(도착 반경 동적 relPos) + 상세 레일
`→ out · ← in` 분리 표기 구현·실측.

**P3.4 후속(같은 날, D34)**: 발주자 이미지 2장(도메인 분할 그리드 + 배치 형식
분류표) → 가능 범위 판정 후 전수 구현: ① `/graph?layout=` 프리셋 5종(순수
함수 — radial 은 껍질 반경 노드 수 적응, organic 은 80분위 정규화로 뭉침 2회
보정, 테스트 6) ② View 패널 Layout 행 ③ Multiples 카드별 `↻` 오버라이드
④ 그리드 미니 3D — 단일 WebGL 렌더러 setScissor 뷰포트 분할(컨텍스트 상한
회피) + 레이캐스트 brushing. 기각: Flow Chart·Scaling Circles·Area Grouping
(의미론 부재)·Arc(2D 전용 후순위). weg-9000 실데이터 4도메인 실측.

**P3.5 후속(같은 날, D35)**: 발주자 지시("육각기둥이 시각적으로 비대") →
프리미티브 재배정: Concept=구 · Domain=구+적도 링(토성형) · Episode/Record=
작은 정육각기둥(변=높이, 전 모서리 등길이) · Bridge=정팔면체 유지. 2D 접기
회전 규칙·미니 그리드 동반 갱신, 픽스처·실데이터 L0/L1 실측.

**P3.6 후속(같은 날, D36·D37)**: ① D36 — 원격 MCP 프로파일 문서화
([[vinc.d.remote-profile]]: 클라우드 VM 사양(2vCPU/4GB/서울)·Caddy TLS·
홈페이지 정적 동거·런북·구현 순서 — 코드 델타는 후속 트랙). ② D37 —
Liquid Glass 층 분리 수용([[vinc.d.glass-language]]): 부유 크롬(팔레트·시트·
View 패널·상세 레일·팝오버)만 글래스(backdrop-filter, 불투명도 하한 .58,
reduced-transparency/미지원 폴백 = 기존 카드), 콘텐츠 평면·레일은 Ink & Paper
유지. 앱 시안 구현·실측(팔레트·View 패널) — 발주자 검토 대기.

**P3.7 후속(같은 날, D38)**: 발주자 지시 — 기본 테마 다크 전환(신규 사용자
무설정 = dark), 사용자가 몰라도 되는 정보 정리: 5표면 헤더의 엔드포인트 메타
삭제·자연어화, Intake 의 CLI/P4/sidecar 문구 자연어화, Packs 포맷 칩 제거·
스코프 라벨 개명(start from/reach), Settings 엔진 주소·시크릿 Advanced 접힘
(+Workspace 개명). 신규 사용자 흐름 실측(무 localStorage → 다크 + 자연어 카피).

**P3.8 후속(같은 날, D39·D40)**: ① D39 — 다크 Atlas 반전 팔레트(발주자:
"배경은 어둡고 그래프가 밝게"): themePalette() 도입 — 다크는 밝은 잉크 마크·
투명 캔버스·fog/엣지/라벨/초점 동반 반전·감광 조명, 라이트 불변(글래스 문서의
"다크 종이 캔버스 예외" 폐지). ② D40 — 글래스 노드(발주자: "질감을 글래스
형식으로"): MeshPhysical 프로스티드(0.72/게이트웨이 0.35, clearcoat) +
RoomEnvironment 반사원(메인·미니 공용). 다크/라이트 실측. 색상 토큰 트랙:
다크 CTA C안 확정(딥 잉크블루+밝은 텍스트, 링·네이티브 컨트롤은 라이튼 분리),
네이티브 컨트롤 accent-color 토큰화, 화면 카피 em-dash 전폐(32곳).

**P3.9 후속(같은 날, D41·D42)**: ① D41 — macOS 컨트롤 문법(패널 세그먼티드
컨트롤 + 버튼 폴리시). ② D42 — 표면 언어 en/ko: 이중 배포 기각·단일 배포,
copy.ts 덱 이중화(접근 시점 해석 Proxy — 호출부 무변경) + 산재 하드코딩 표면
문자열 61건 덱 이관, Settings Language 세그(reload 재렌더), 무설정 = OS 로케일
추종. 한국어 덱 저작(F2 게이트 통과 — 채굴·기록·잇기 어휘), 한/영 전환 왕복
실측.

**P3.10 (2026-07-11, D43)**: ① 기본 데이터 홈 `~/.vinc` — `store/config.py`
`resolve_data_dir()`(우선순위 `--data` > `MEMORY_DATA_DIR` > `~/.vinc/db`, 기본
홈일 때만 `db/packs/inbox/backups` 생성). 라이브러리 계약(`load_config` 기본
None)은 불변 — 경로 해석은 CLI 진입점에서만(verify·테스트 순수성).
`tests/test_paths.py` 3종. ② P4 릴리스 파이프라인 — `packaging/run_sidecar.py`
+`sidecar.spec`(지연 임포트 엔진 5종·kuzu hiddenimports, cryptography/OpenSSL
제외로 pyo3 훅 충돌 회피), `tauri.conf.json` `externalBin`+`.vincpack`
fileAssociations(D29 이행), `.github/workflows/release.yml`(태그 `v*`, 3-OS
매트릭스: 사이드카 동결 → `binaries/vinc-sidecar-<target-triple>` 배치 →
tauri-action 드래프트 릴리스). 사이드카 바이너리 스모크(--help·serve+/health)
실측. 서명·공증·클린 VM 게이트는 후속.

## 현재 위치 (2026-07-10 실측 — **스냅샷, 상단 §완료 기록이 최신**)

> ⚠ 아래는 **Phase A–F 착수 전** 시점 기록이다. 본 문서 상단 "완료 기록"이 **전 Phase 완료**를
> 선언하므로, 아래 "잔여 백로그"는 **이미 해소됐다**(2026-07-16 확인). 시점 기록으로만 보존.

완료: 사이드카 P0–P2·MCP Push MVP·기능 공백 4종 해소(seed+depth/Record/props/
INHERITS_FROM)·/atlas·/coupling·/share+import(D19)·앱 5표면 스캐폴드·Compare 뷰·
검색 연산자/≡≈·아이콘 레일 셸(D26)·Notation 아이콘 배선·커맨드 팔레트.
~~잔여 백로그: Atlas 마크 사양 절반(§A)·Share 시트+lasso(§B)·Multiples/Matrix(§C)·
설치형 기능 중 웹셸 가능분(§D)·사이드카 보강(§E)·Figma 역동기화(§F).~~ → 전 Phase 완료.

## Phase A — Atlas 마크 사양 완성 (atlas-visual-language §1 잔여)

| # | 작업 | 산출 |
|---|---|---|
| A0 | **3D 렌더러 전환(D21 개정)**: Atlas L0/L1/L2 를 Sigma → three.js 계열 3d-force-graph(MIT) 로 교체. Compare 좌우 렌즈는 Sigma 2D 잔존 | atlas.ts |
| A1 | **엣지 3D**: 방향 화살촉(prerequisite/derives — 네이티브 arrow), 크로스도메인 = 세선+ink_mute(파선 튜브는 후속), 굵기=가중치 | atlas.ts |
| A2 | **노드 3D 프리미티브 = 타입**: 육각기둥(Concept)·구(Episode/Record)·정팔면체(Bridge)·대형 육각기둥+링(L0 Domain), 게이트웨이 반투명 | atlas.ts |
| A3 | **모노크롬 3D 규율**: 무광 잉크 Lambert 3단·배경/fog=paper·무채 조명·포인트색=초점 전용·클릭 fly-to·orbit 감쇠 | atlas.ts |
| A4 | **사이드카 z 좌표**: layout_positions 를 3D(클러스터 중심 xy원 + 구면 fibonacci 산개)로 확장 — 결정론 유지 | api/query.py + 테스트 |
| 게이트 | build + Playwright — 형태/선형태가 실렌더에서 변별되는지 스크린샷 실측 | |

## Phase B — Share 시트 + lasso (atlas-visual-language §2·§5)

| # | 작업 | 산출 |
|---|---|---|
| B1 | **lasso 영역 선택**: Shift+드래그 사각/자유 선택 → 노드 집합 + 플로팅 액션(`Trace`·`Tag`·`Share this slice`) | atlas.ts |
| B2 | **Share 시트**(3동선 수렴: lasso/우클릭 seed/Packs 폼): 스코프 요약 + 포함 노드 목록(개별 제외 체크) + 미포함 고지 카피 + 미니 미리보기 그래프 + `Save pack file` | `app/src/sheets/share.ts` |
| B3 | **Import 미리보기 시트**: 매니페스트(작성일·스코프·counts) + 겹침 예고("{x} already exist — updated in place") + 결과 diff + `View in Atlas`(수입 노드 1회 점등, D23) | `app/src/sheets/import.ts` |
| 계약 델타 | `/share` 에 `ids=[…]`(명시 노드 집합) 스코프 추가 — lasso 선택을 그대로 패키징 | api/share.py + 테스트 |
| 게이트 | 실데이터 왕복: lasso → 시트(1개 제외) → 저장 → 별도 owner 재수입 → 점등 확인 | |

## Phase C — 멀티뷰 (atlas-visual-language §3 잔여)

| # | 작업 | 산출 |
|---|---|---|
| C1 | **Multiples 그리드**: 도메인당 미니 캔버스(사이드카 좌표 재사용), hover 시 크로스도메인 상대 동시 점등(brushing&linking), 카드 클릭=L1 | atlas.ts `grid` 모드 |
| C2 | **Coupling Matrix**: 도메인×도메인 히트맵(잉크 농도 3단), 셀 클릭=Compare. 도메인 6+ 에서 기본 노출 | atlas.ts `matrix` 모드 |
| 게이트 | weg-9000 실데이터(4도메인)로 Multiples 실측 + Matrix 는 합성 6도메인 픽스처 | |

## Phase D — 설치형 기능(웹셸에서 가능한 것만; Tauri 빌드 필요분은 P4)

| # | 작업 | 비고 |
|---|---|---|
| D1 | **다크 모드**: `[data-theme=dark]` 토큰 이미 존재 — OS 추종 + Settings 토글 | desktop-shell §3-9 |
| D2 | **검색 라이브**: 250ms 디바운스 + 최근 검색(로컬) | §4.1 |
| D3 | **첫 실행 고지 + `Try a sample pack`**: 동봉 샘플 `.vincpack` 즉석 수입(owner=`sample:demo`) + 1클릭 제거 | D30 — share 인프라 재사용 |
| P4 분리 | 창 상태·업데이터·파일 연결(.vincpack)·네이티브 메뉴·트레이 — **Tauri 빌드 환경 필요, 본 트랙 범위 밖으로 명시** | desktop-shell §2–3 |

## Phase E — 사이드카 보강

| # | 작업 | 비고 |
|---|---|---|
| E1 | **`/promote-candidates`**(조회 전용): access_count·pin·tag 임계 초과 후보 결정론 집계 — 확정(쓰기)은 기존 `/pin` 경유(단일 쓰기 표면 원칙 유지, DEC 불요) | MCP §7.2 ③·갭 권고 4 |
| E2 | **Pull inbox**: `inbox/` 디렉토리 스캔 잡(잡 큐 재사용) — md→recall/concepts 경로, 시안 5:2 의 "인박스 감시" 탭 실체화 | MCP-INTERFACE §7 |
| E3 | **검색 filters 푸시다운**: `domain:`/`tag:` 를 클라이언트 필터가 아닌 사이드카 pre-filter 로 | §4.4 |
| 게이트 | verify 신규 테스트 + 실데이터 실측 | |

## Phase F — Figma 역동기화 (코드 완료 후, 마지막)

| # | 작업 | 비고 |
|---|---|---|
| F1 | **v0.2 페이지 신설**: 5표면 as-built 프레임(레일·영어 카피·헤더 문법) — 코드 스크린샷 기준, `use_figma` 코드→디자인 푸시 활용 | 새 시안이 아닌 미러 |
| F2 | **컴포넌트 라이브러리**: Button/ListRow(상태점)/chip/badge/palette-card/Share 시트 — 코드 정본 기준 | DESIGN.md §5 실체화 |
| F3 | **1차 시안 정리**: Login 프레임 `deprecated (D7)` 마킹, 분포 프레임 `absorbed → Atlas L0 (D15)` 마킹 — 삭제 아닌 계보 보존 | |
| 게이트 | 프레임 ↔ 실앱 스크린샷 육안 대조 + 토큰 변수 바인딩 확인 | |

## 순서·의존

```
A(마크 사양) ─┬─→ B(Share 시트: 시각 언어 위에서) ─→ C(멀티뷰) ─┐
E(사이드카)  ─┘   (E1·E3 는 A 와 병행 가능)                    ├─→ F(Figma 미러)
D(설치형 웹분) ── 독립, 아무 때나 끼워넣기 ─────────────────────┘
```

전 Phase 공통 게이트: `gate_forbidden`(코드+docs) · `verify.py` · `npm run build` ·
Playwright 실측 · 커밋 단위 = Phase 당 1~2.

## 부록 Z. 미분류 원문 사실
(없음)

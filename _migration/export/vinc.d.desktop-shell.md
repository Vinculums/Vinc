<!-- vinc-export
doc_id: vinc.d.desktop-shell
title: 설치형 셸 연구 — Figma 재개 전 판정
origin: Vinc-docs/vinc/D/vinc.d.desktop-shell.md@5090690
chunks: 6
verified: true
content_hash: 7d80a41ce3ff26dc0bbd30f2b45237f29019614e06351ccb3669b6089018334e
updated_at: 2026-08-01T16:20:31.559Z
-->

---
doc_id: vinc.d.desktop-shell
layer: D
domain: desktop-shell
title: 설치형 셸 연구 — 내비게이션(LNB 판정)·데스크톱 구조·필수 기능·첫 실행 (D26–D30 · D38 · D41–D44)
status: draft
authority: research-input
version: v0.1
owner: vinc
last_updated: 2026-07-16
inherits_from: [vinc.d.index, vinc.d.ux-research, vinc.d.atlas-visual-language]
tags: [vinc, D, shell, navigation, rail, tauri, desktop, first-run, dec-candidate]
---

# 설치형 셸 연구 — Figma 재개 전 판정

> 발주자 질문(2026-07-10): "LNB 를 쓸지부터, 설치형에 알맞은 구조, 설치형에 필수로
> 들어가는 기능, 그리고 로그인부터 1차 시안인 현 디자인의 재검토." 본 문서는
> 딥 리서치(5각도 검색 → 소스 정독 → 주장별 3표 적대 검증)의 판정 정본이다.
>
> **검증 등급 표기**: ✔=적대 검증 통과(3표), ◐=공식 문서 출처이나 검증 투표가
> 환경 한도로 미완(반박 0건) — 구현 착수 전 1차 문서 재확인 권장.

## 1. 내비게이션 판정 — LNB(전폭 사이드바) 기각, **접이식 아이콘 레일** 채택 (DEC D26)

> ⚠ **이 절은 2026-07-31 DEC D69 로 뒤집혔다**([[vinc.d.atlas-visual-language]] §6).
> 실제 이행 궤적은 D26(전폭 LNB 기각 → 아이콘 레일) → D56(레일마저 폐지 → 상단바
> 단일화) → **D69(문서 트리 사이드바로 재도입)** 이다. 아래 근거 4축은 **여전히
> 유효한 관찰**이되, 전제가 달라졌다: D26 은 좌측 크롬을 "표면 5개로 가는 내비"로만
> 봤다. D69 의 LNB 가 싣는 것은 표면 목록이 아니라 **도메인 → 문서 트리**, 즉
> Obsidian 의 리본이 아니라 **파일 탐색기 쪽**이다(같은 앱의 다른 패널). Material 의
> "3~7 목적지" 산정도 표면 수에만 적용되지 문서 수백 건의 계층 목록에는 해당하지
> 않는다. 유지되는 규율: 기본 숨김 금지(HIG) · 플로팅 **내비** 패널 기각(D70 의
> 노드 종속 맥락 카드는 내비가 아니라 별개) · 캔버스 퍼스트(LNB 는 오버레이가
> 아니라 그리드 열이라 캔버스를 덮지 않는다).

근거 4축이 같은 방향을 가리킨다:

1. ✔ **Apple HIG**: 사이드바는 최상위 내비게이션이지만 "상당한 수직·수평 공간을
   요구"하며, "숨길 수 있게 하되 **기본 숨김은 금지**(발견성)"가 공식 지침이다.
2. ✔ **Material**: 내비게이션 레일은 **3–7개 목적지** 전용 컴포넌트이고 데스크톱
   해상도에서 **단독 내비로 충분**하다 — Vinc 의 5표면은 정중앙에 든다.
3. ✔ **Figma UI3 의 실패 학습**: 캔버스 극대화가 재설계의 명시 동기였고 하단
   슬림 툴바로 크롬을 줄였다. 그러나 **플로팅 내비/속성 패널은 베타 출시 후
   철회**했다 — 캔버스를 오히려 좁히고, 내용이 패널 뒤로 비껴 보이고, 정렬
   도구(눈금자)를 무력화했기 때문. → **고정(접이식) 패널이 상용 검증된 안전지대,
   플로팅 오버레이 내비는 기각**.
4. ✔ **Obsidian**: 그래프가 주 표면인 성숙 데스크톱 앱의 실제 답 — 슬림 아이콘
   리본(레일)에서 그래프를 소환하고, 그래프 설정은 **캔버스 코너 톱니의 인캔버스
   오버레이**로 둔다(앱 설정으로 보내지 않음). 전역/로컬 2스코프 분리도 Atlas
   L0/L1·L2 와 동형 — 우리 구조가 관례와 일치함을 교차 확인.

**판정 D26**:
- 전폭 LNB(1차 시안) → **아이콘 레일 56px 기본**(Notation 아이콘 5개 + Settings),
  hover/토글(`[`)로 라벨 확장(200px), **기본 숨김 금지**(HIG).
- Atlas 표면은 **캔버스 퍼스트**: 레일 외 좌측 크롬 0(이미 [[vinc.d.atlas-visual-language]]
  §4.3 필터 폼 제거), 그래프 컨트롤은 인캔버스(Obsidian 패턴), 뷰 액션은 하단
  슬림 바(Figma UI3 패턴).
- **플로팅 내비 패널 기각**(Figma 철회 근거 승계). 상단 탭바 기각(수직 공간 소모 +
  캔버스 상단 침범). 커맨드 팔레트는 **보조 수단**(§3 필수 기능)이지 내비 대체가
  아니다 — 발견성은 레일이 담당.

**창 크롬**: ✔ Tauri v2 는 완전 커스텀 타이틀바를 3경로(conf/JS/Rust)로 지원 —
Ink & Paper 크롬 통합 가능. ◐ 단 macOS `decorations:false` 는 창 이동·정렬 등
시스템 동작을 잃으므로 **macOS 는 `titleBarStyle: overlay`(트래픽 라이트 유지 +
콘텐츠 확장) 하이브리드, Windows 는 커스텀 타이틀바** 권고 (DEC D27).

## 2. 설치형 데스크톱 앱의 구조 관례 (웹앱과 다른 것)

| 항목 | 판정 | 근거 |
|---|---|---|
| 네이티브 메뉴 | 채택 — File(Ingest sources·Import a pack·Back up)/View(표면 5)/Help. ◐ Tauri 메뉴는 창·트레이 부착 가능, macOS 글로벌 메뉴바/Win 인앱 배치는 OS 관례 자동 | v2.tauri.app/learn/window-menu |
| 시스템 트레이 | **보류** — 상주형 앱 아님(잡 큐는 창 수명). 긴 ingest 진행 알림은 OS 알림으로 충분 | ◐ tauri 2.0 플러그인화 |
| 창 상태 복원 | 채택(필수) — ◐ 공식 `window-state` 플러그인이 저장·복원 전자동 | v2.tauri.app/plugin/window-state |
| 멀티 윈도우 | **기각** — 단일 창 유지. 스토어 writer 가 단일이고(R4), Compare 뷰가 분할 수요를 내부에서 해소 | 설계 §11 |
| 파일 연결 | 채택 — ◐ Tauri 번들러가 파일 연결 지원 → **`.vincpack` 확장자 등록**: 받은 팩 더블클릭 = 앱 기동 + Import 시트 직행 (DEC D29). 공유 UX([[vinc.c.sharepack]])의 OS 통합 완성 | v2.tauri.app/blog/tauri-20 |
| 드래그앤드롭 | 채택(이미 Intake 규약) — 앱 전역 드롭: md/폴더→Intake, `.vincpack`→Import 시트 | |
| OS 알림 | 채택(최소) — 백그라운드 잡(ingest·backup) 완료 시 1회. 마케팅성 알림 금지 | ◐ 플러그인 |

## 3. 설치형 필수 기능 체크리스트 (Tauri 구현 관례 포함)

| # | 기능 | 상태 | 규정 |
|---|---|---|---|
| 1 | 자동 업데이트 | **신설 (DEC D28)** | ◐ 공식 updater 플러그인 — **정적 JSON 매니페스트**로 서버 없이 가능. **오프라인 우선 정합**: 유일한 선택적 네트워크 접점이므로 Settings 4번째 항목으로 명시(기본 on + 첫 실행 고지 + 끄기 제공, 실패 무음), 수동 `Check for updates` 는 Help 메뉴. 과금·계정 무관(F1/F5 유지) |
| 2 | 창 크기·위치 기억 | 신설(필수) | ◐ window-state 플러그인 — 전자동 |
| 3 | 설정 로컬 저장 | 보강 | ◐ 공식 Store 플러그인(로컬 파일 KV) — 현 스캐폴드 localStorage 를 셸에서 대체 |
| 4 | 커맨드 팔레트 | 신설 | `Cmd/Ctrl+K` — 표면 이동·최근 레코드·액션(Ingest/Share/Back up). 내비 보조(§1) |
| 5 | 단축키 체계 | 보강 | 기존 `f`/`1–3`/`Esc`([[vinc.d.atlas-visual-language]] §2) + `1–5` 표면 직행, `[` 레일 토글, `?` 치트시트 오버레이 |
| 6 | 백업/복원 | 기존 | `/backup`(스냅샷, CHECKPOINT 일관) — Settings·File 메뉴 노출 |
| 7 | 데이터 디렉토리 관리 | 기존 | Settings Data 항목 + `Reveal in folder` |
| 8 | 크래시·문제 리포트 | 신설 | **외부 전송 금지(F5)** — 로컬 로그 파일 + Help `Reveal logs`. 원격 크래시 리포터 기각 |
| 9 | 다크 모드 | 기존 설계 | tokens `[data-theme="dark"]` — OS 테마 추종 + Settings 수동 토글 |
| 10 | 접근성 | 기존 규정 | WCAG 2.2 AA 토큰·`:focus-visible` 링·전 기능 키보드 도달(레일 tab 순회) |
| 11 | 첫 실행 온보딩 | 기존(D15) | §4 |
| 12 | 언어 | 기존(D18) | 영어 정본. i18n 프레임 없음(단일 언어 출시) |

## 4. 첫 실행 경험 — 설치→가치 최단 경로

로그인 우선 구조는 이미 폐기(D7). 재검토 판정:

1. **첫 화면 = Intake 빈 상태**("Bring your corpus in." + 주권 카피) — 별도 온보딩
   화면 없음(D15 유지). 임베딩 모델 준비는 드롭 존 하단 진행 표시로.
2. **샘플 팩 동봉 (DEC D30, 신설)**: 빈 상태에 보조 동작 `Try a sample pack` —
   설치본에 동봉된 작은 `.vincpack` 을 즉석 수입해 **자기 데이터 없이 60초 안에
   Atlas 를 체험**. share pack 인프라(D19) 재사용이라 구현 비용이 낮고, "설치 직후
   가치"의 최단 경로다. 샘플은 `Remove sample data` 1클릭 제거(owner=`sample:demo`
   스코프 삭제).
3. 첫 실행 고지 1장(모달 아님, 빈 상태 안): 데이터 위치 + 업데이트 체크 고지(D28)
   — 동의 강요 화면 금지.

## 5. 1차 시안 재검토 목록 → Figma 재개 우선순위

| 순위 | 작업 | 근거 |
|---|---|---|
| 1 | **셸 프레임 교체**: 전폭 LNB → 아이콘 레일(56px/확장 200px) + 타이틀바 통합(mac overlay/Win 커스텀) | §1 D26·D27 |
| 2 | **로그인 프레임 삭제** → 첫 실행 Intake 프레임(드롭 존 + `Try a sample pack`) + 잠금 해제 다이얼로그(옵트인 시에만) | D7·§4 |
| 3 | **Atlas 캔버스 퍼스트 프레임**: 인캔버스 컨트롤·하단 슬림 뷰 바·우측 상세 레일(고정·접이식 — 플로팅 금지) | §1 |
| 4 | **커맨드 팔레트 컴포넌트**(Cmd+K) — 모노크롬 목록·표기 아이콘 | §3-4 |
| 5 | **Settings 확장**: Update(D28)·단축키 치트시트 항목 추가(3→5 항목 상한 유지) | §3 |
| 6 | **`.vincpack` 열기 플로우**: OS 더블클릭 → Import 시트([[vinc.d.atlas-visual-language]] §5.2) | D29 |
| 7 | 네이티브 메뉴 정의서(File/View/Help 트리) | §2 |

## 6. DEC 후보표

| # | 결정 | 권고 |
|---|---|---|
| D26 | 내비 = 접이식 아이콘 레일(기본 노출) + 캔버스 퍼스트 Atlas, 플로팅 내비 기각 | ~~채택~~ → **D56 으로 폐지, D69 로 재도입**(§1 머리 경고 참조) |
| D27 | 창 크롬 = macOS `titleBarStyle: overlay` / Windows 커스텀 타이틀바 하이브리드 | 채택(◐ macOS 제약 1차 문서 재확인 후) |
| D28 | 자동 업데이트 = updater 플러그인 + 정적 매니페스트, 기본 on·첫 실행 고지·끄기 제공 | 채택 |
| D29 | `.vincpack` 파일 연결 등록 → Import 시트 직행 | 채택 |
| D30 | 샘플 팩 동봉 첫 실행(`Try a sample pack`, 1클릭 제거) | 채택 |
| D38 | **기본 테마 = 다크** + **개발자 정보 은닉**: 헤더의 엔드포인트 메타(`· /recall` 등)·CLI/내부 용어(P4·Tauri·sidecar·owner scope) 표면 카피에서 제거 또는 자연어화, Settings 의 엔진 주소·연결 시크릿은 `Advanced` 접힘으로 강등(Workspace 로 개명). **보강**: 화면 카피에 em-dash(—) 금지 — 문장 분리(마침표)·수치 나열은 콜론·라벨 병기는 중점(·)으로 대체(D18 보이스 가이드 구두점 규칙). 발주자 지시 2026-07-10 | 채택 |
| D41 | **macOS 컨트롤 문법** — 패널 선택지(View 패널·Settings 테마)는 세그먼티드 컨트롤(홈 파인 트랙 + 떠오른 썸 — 색 아닌 높이가 상태, 5지선다는 줄바꿈), 버튼은 미세 그라데이션+상단 하이라이트+눌림 변위. 발주자 지시 2026-07-10 | 채택 |
| D42 | **표면 언어 en/ko(단일 배포)** — 이중 배포 기각(릴리스 매트릭스 배가·재설치 전환). 카피 덱 이중화(copy.ts DECKS, 접근 시점 해석 Proxy) + Settings `Language` 세그 + 무설정 = OS 로케일 추종(ko-*→한국어). **한국어 덱은 번역이 아닌 저작**: 간결한 -세요/-합니다체, F2 금지 어휘를 채굴·지도·기록·잇기 계열로 대체(게이트가 자동 검증), 표면 이름(Intake 등)·레이아웃 프리셋명은 고유명사로 미번역. 발주자 승인 2026-07-10 | 채택 |
| D43 | **기본 데이터 홈 = `~/.vinc` + P4 릴리스 파이프라인** — ① 데이터 홈: `.claude`/`.obsidian` 관례를 따라 무설정 기동 시 `~/.vinc/{db,packs,inbox,backups}` 를 생성·사용. 우선순위 `--data`(CLI) > `MEMORY_DATA_DIR`(env) > `~/.vinc/db`. 라이브러리 계약(`load_config`)의 기본값은 None 유지 — 경로 해석은 CLI 진입점(`resolve_data_dir`)에서만 수행해 verify·테스트 순수성 보존. ② 릴리스: 사이드카를 PyInstaller 단일 바이너리로 동결(`packaging/sidecar.spec` — 지연 임포트 엔진 5종+kuzu hiddenimports, cryptography/OpenSSL 명시 제외)하고 Tauri `externalBin`(`binaries/vinc-sidecar-<target-triple>`)으로 번들, `.vincpack` fileAssociations 등록(D29 이행), GitHub Actions `release.yml`(태그 `v*`, ubuntu-22.04/windows/macos 3-OS 매트릭스 → 드래프트 릴리스). 서명·공증은 후속. 발주자 승인 2026-07-11 | 채택 |
| D44 | **코드서명·리포 공개 방향($0 경로)** — D43 이 후속으로 남긴 서명·공증의 결론. 미서명 배포의 첫 실행 경고(Windows SmartScreen · macOS Gatekeeper) 대응으로 **`Vinclums/Vinc` public 전환**(히스토리 시크릿 감사 클린 — 키·`.env` 커밋 0건) → **Windows = SignPath Foundation 무료 OSS 서명**, **macOS = 미서명 유지 + 다운로드 페이지 첫 실행 우회 안내(라이브)**. 공개 전환으로 CI 무료화(비공개 mac 러너 10배 가중 비용 회피). Apple Developer($99/yr)는 macOS 서명을 원할 때만. 상세 전개는 `plans/PLAN-code-signing-notarization.md`. 발주자 결정 2026-07-13 | 채택 |

## 참조 (딥 리서치 검증 소스, 2026-07-10)

- Apple HIG — Sidebars (developer.apple.com) ✔
- Material Design — Navigation rail (m2.material.io) ✔
- Figma Blog — "Our approach to designing UI3" · "Behind our redesign: UI3" ✔
- Obsidian Help — Graph view ✔
- Tauri v2 — Window customization ✔ / Window menu·window-state·Store·Updater
  플러그인·2.0 릴리스 노트 ◐(반박 0, 검증 투표 환경 한도로 미완)

## 부록 Z. 미분류 원문 사실
(없음)

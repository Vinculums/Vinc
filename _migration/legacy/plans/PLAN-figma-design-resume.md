# Figma 디자인 빌드 재개 계획서

> **목표**: Figma 파일 `WUXLeDDNBqih2KTZOz9wCX` 에서 중단된 디자인 빌드를 재개해, 라이브 마케팅 사이트(`Vinc/site/*`)와 D 계층 화면설계 SSoT(`Vinc-docs/vinc/D/*`)를 정본 삼아 **Home 완성 → Marketing 프레임 → App 표면(5+Settings) → Auth 화면 → light/mobile 변형** 순으로 채운다.
> **현재 상태**: **차단됨(BLOCKED)** — Figma 커넥터 인가가 선행 조건이며 발주자가 claude.ai 에서 인가하기 전에는 착수 불가. 파일 골격(Foundations·Icons·색 변수 컬렉션 Light/Dark 18종·Home HERO·Marketing/App 케이스별 프레임 스켈레톤)은 준비되어 있고, 구 'Screens' 페이지는 삭제됨.

---

## 전제·의존 (preconditions / dependencies)

| # | 전제 | 상태 | 착수 차단 여부 |
|---|---|---|---|
| P1 | **Figma 커넥터 인가** — 이 세션은 비대화형이라 OAuth 진행 불가. `plugin:design:figma`(및 Figma MCP 툴 `mcp__…__use_figma` 등)가 미인가. 발주자가 claude.ai 커넥터 설정에서 인가해야 함 | ❌ 미인가 | **전면 차단** |
| P2 | 대상 파일 접근권 — `WUXLeDDNBqih2KTZOz9wCX` 편집 권한이 인가 계정에 있어야 함 | 확인 필요(O) | 차단 |
| P3 | 정본 소스 = 라이브 사이트 HTML(`D:/Projects/AIProjects/vinc-project/Vinc/site/{index,download/index,release/index,guide/index,login/index,account/index}.html`) + D 계층 스펙(`Vinc-docs/vinc/D/vinc.d.{ingest,records,graph,search,packs,settings,desktop-shell,atlas-visual-language,index}.md`) | ✅ 확보(정독 완료) | — |
| P4 | 폰트 — Space Grotesk(제목)·Inter/Noto(본문). Figma 팀 라이브러리/로컬에 Space Grotesk·Inter 설치 필요. **Space Grotesk 는 'SemiBold' 웨이트가 없음 → 제목은 'Bold' 사용**(사이트의 heading 680/700 무게 매핑) | 확인 필요(O) | 부분 차단(제목 프레임) |
| P5 | 변수 정합 — 마케팅은 기존 Light/Dark 18종 컬렉션 사용. **앱 창(window) 팔레트(`--w-*`)는 항상 다크(D38)** 이며 별도 값 → Foundations 단계에서 "Window(항상 다크)" 컬렉션/모드 신설 필요(하드코딩 hex 금지) | 신설 필요(C) | Phase 3 선행 |

**Figma MCP 사용 제약(전 단계 공통 준수)**
- **figma-use 스킬 경유**로만 생성/편집(`use_figma`). 읽기는 `get_metadata`·`get_screenshot`·`get_variable_defs`·`get_design_context`.
- **페이지 단위 읽기**: 한 번에 한 페이지만 읽어 컨텍스트 폭주 방지(파일 5페이지 = Foundations/Icons/v0.2 As-built/Marketing/App).
- **좌표는 수치(numeric)로 명시** — 오토레이아웃 추정 금지, x/y·w/h 를 명시 배치.
- 색·치수는 **변수 바인딩만**(직접 hex 금지 — 디자인 게이트 D-R, `vinc.d.index#§3`).
- 카피는 **em-dash(—) 금지**(D38): 문장 분리는 마침표, 나열은 콜론, 병기는 중점(·).

---

## 단계 (phased tasks)

> owner: **C**=Claude(Figma MCP 빌드) · **O**=발주자(인가·결정·리뷰). 각 태스크는 완료선(done-criterion) 명시.

### Phase 0 — 전제 해소 + Foundations·컴포넌트 마감

| # | owner | 태스크 | 완료선 |
|---|---|---|---|
| 0.1 | **O** | claude.ai 에서 Figma 커넥터 인가 + 대상 파일 편집권 확인(P1·P2) | `whoami`/`get_metadata` 가 파일 `WUXLeDDNBqih2KTZOz9wCX` 를 반환 |
| 0.2 | **O** | Space Grotesk·Inter·Noto 폰트 가용 확인(P4) | Foundations 텍스트 스타일이 폰트 미설치 경고 없이 렌더 |
| 0.3 | **C** | Foundations 감사 — Light/Dark 18종 변수(background·foreground·card·muted·muted-foreground·border·ring·primary·primary-foreground·accent 등)가 `site` CSS 값과 일치하는지 `get_variable_defs` 로 대조 | 18종 전부 HSL 값 일치(예 dark `--ring:218 31% 66%`), 불일치 0 |
| 0.4 | **C** | **Window(항상 다크) 변수 컬렉션 신설**(P5) — `--w-bg #16130F·--w-surface #211E18·--w-muted #262218·--w-fog #2C2820·--w-border #39332B·--w-text #EAE5DD·--w-text-soft #C7C0B2·--w-mutedfg #A39A8A·--w-point #8FA3C4·--w-pointfill #3E4C63` | 컬렉션 등재, App/목업 프레임이 이 변수만 바인딩 |
| 0.5 | **C** | 텍스트 스타일 — 제목(Space Grotesk **Bold**)·본문/UI(Inter/Noto) 스케일을 `site` 클램프에 근사(h1 clamp 2.4–4.1rem 등) | Display/H1/H2/H3/Body/Caption/kbd 스타일 등재 |
| 0.6 | **C** | Icons 페이지 정합 — 사이트 심볼 세트를 컴포넌트로 완비: `vlogo·i-ingest·i-recall·i-graph·i-pack·i-search·i-settings·i-domains·i-decide·i-stack·i-lock·i-download·i-globe·i-sun·i-moon·i-check·i-sparkle·i-info·i-desktop·i-mail·i-upload·i-cursor·i-folder·flag-us·flag-kr` | 25개 아이콘 컴포넌트, `currentColor` 상당의 색 변수 스와핑 가능 |
| 0.7 | **C** | 공유 컴포넌트 라이브러리 — 마케팅: `Button(primary/quiet/small)`·`Header/nav`·`Footer`·`langmodal`·`segtoggle(테마)`. 앱: `IconRail(56px, D26)`·`TitleBar(D27)`·`ListRow`·`EmptyGuide`·`Progress`·`Badge`·`Skeleton`·`ErrorBanner`·`SegmentedControl(D41)`·`DetailRail`·`CommandPalette(Cmd+K)` | 각 컴포넌트 = variant 세트, App 표면이 인스턴스로 조립 가능 |

**착수 차단**: 0.1 미완이면 Phase 0 이후 전면 정지.

### Phase 1 — Home 완성 (Marketing 페이지)

> 정본 = `Vinc/site/index.html`. HERO 는 완료 상태. 남은 6블록을 라이브와 시각 일치(폰트 제외)로 채운다.

| # | owner | 태스크 | 완료선 |
|---|---|---|---|
| 1.1 | **C** | **앱 목업 window** — 타이틀바(신호등 3 + `Search by meaning ⌘K` pill) + 아이콘 레일 6개(Intake·Records·Atlas active·Search·Packs / gap / Settings) + Atlas 2D 캔버스(노드·엣지·legend 3칩·detail 카드). Window 변수 바인딩(0.4), 항상 다크 | 프레임이 `site` 목업(`.window`)과 노드/레일/legend 구성 일치 |
| 1.2 | **C** | **3단계 흐름**(`#how`) — 카드 3(01 Effortless import / 02 Explore the atlas / 03 Share as packs), 번호 카운터·아이콘·카피 | 3카드 배치, 카피 = 영어 정본 |
| 1.3 | **C** | **기능 그리드**(`#features`) — 6카드(search·graph·domains·pack·decide·stack 아이콘 + 제목·본문) | 6카드 2–3열 그리드 |
| 1.4 | **C** | **프라이버시**(`#vows`) — 3열(No sign-up / Completely offline / Free of lock-in) + 상단 방사 글로우 배경 | 3열 배치, `--ring` 글로우 표현 |
| 1.5 | **C** | **최종 CTA + 푸터** — 로고·`Now, connect what you know.`·Download 버튼 / 푸터 4링크(Download·User guide·Release notes·© MIT) | 프레임 완결, 링크 라벨 일치 |
| 1.6 | **C** | 배경 레이어 표현 — 오로라 4블롭 + 하프톤 도트 필드 + 그레인은 Figma 정적 근사(이미지 fill 금지·CSP 무관하나 정적 프레임이므로 단색+글로우 레이어로 대체) | 배경 톤이 라이브와 근사, 외부 asset 0 |
| 1.7 | **O** | Home 리뷰 승인 | 발주자 OK → Phase 2 |

### Phase 2 — Download · Release · Guide 프레임

| # | owner | 태스크 | 완료선 |
|---|---|---|---|
| 2.1 | **C** | **Download** (`site/download/index.html`) — 히어로(로고·`Download Vinc`·버전 `0.1.0 · pre-release`·감지 primary 버튼·detect note) + 준비중 notice 배너(info) + 플랫폼 3카드(macOS Apple Silicon .dmg / Windows .exe / Linux AppImage·.deb, "Your device" 태그) + 안심 문구 | 3카드 + notice + assure 배치, GitHub Releases 링크 텍스트 반영 |
| 2.2 | **C** | **Release** (`site/release/index.html`) — 히어로 + v0.1.0 릴리스 카드(날짜 2026-07-13·Pre-release 배지·4항목 리스트·서명 경고 note·Download/GitHub CTA) + 로드맵 타임라인 5스텝(Done×3·In progress×1·Planned×1) | 릴리스 카드 + 로드맵 상태 노드(done/now/plan) 일치 |
| 2.3 | **C** | **Guide** (`site/guide/index.html`) — 인트로 + TOC 6칩(1 Intake·2 Records·3 Atlas·4 Search·5 Packs·+ Connect to Claude). 6챕터는 라이브의 **애니메이션 장면**이므로 Figma 는 각 장면의 **대표 정지 프레임**으로 빌드(장면별 최종/성공 상태 1컷) | 6챕터 대표 프레임 + 단축키(keys) 섹션 + 최종 CTA |
| 2.4 | **C** | Guide `+ MCP(Connect to Claude)` 장면 — 터미널 카드 + 커넥터 카드(도구 칩 5)로 MCP 연결 표현. 앱 무로그인·오프라인 불변(하드 인바리언트) 카피 유지 | MCP 장면 정지 프레임, 잘못된 "앱 로그인" 함의 없음 |
| 2.5 | **O** | Marketing 3종 리뷰 승인 | 발주자 OK → Phase 3 |

### Phase 3 — App 표면 (5 표면 + Settings)

> 정본 = D 계층 스펙 + `desktop-shell`(D26 레일·D27 타이틀바·D38 다크·D41 세그). 모든 App 프레임은 **Window 변수(0.4)·항상 다크**. 셸 크롬(레일·타이틀바)은 0.7 컴포넌트 인스턴스. 각 표면 4-State 중 **핵심 상태 우선**, error/loading 은 스트레치.

| # | owner | 태스크(표면 · 상태) | 완료선 |
|---|---|---|---|
| 3.1 | **C** | **Intake — Empty(idle 첫 실행)** — EmptyGuide `Bring your corpus in.` + 드롭존 + `Choose sources` CTA + 주권 카피 + `Try a sample pack`(D30) | `vinc.d.ingest §2` idle 카피·CTA 일치 |
| 3.2 | **C** | **Intake — Ingesting(loading)** — Progress 트랙 + `Ingesting {n} of {m} — {domain} domains so far.`(카피는 D38 구두점으로 정규화) | 진행 상태 프레임 |
| 3.3 | **C** | **Intake — Success** — `{n} records across {m} domains.` + `Open in Atlas` 동선 | 성공 프레임 + 다음 동선 |
| 3.4 | **C** | **Records** — Success(ListRow 목록 + 카운트 헤더 `{n} records · {m} domains` + 좌 도메인/태그 칩 필터 + 우 상세 레일 click 시) · Empty(`No records yet. Ingest sources…`) | `vinc.d.records` 레이아웃·행 액션(Show in Atlas·Pin·Tag) 반영 |
| 3.5 | **C** | **Atlas — Graph(L0 success)** — 캔버스 전폭 + 하단 태그 legend 칩 + 브레드크럼 `Atlas › {domain}` + 인캔버스 View 컨트롤. 그래프는 **정적 2D 투영**(결정 D2 참조) — 육각(Concept)/원(Record)/이중원(Domain) 마크·모노크롬 잉크 | `vinc.d.graph §1–2` L0·크롬 반영 |
| 3.6 | **C** | **Atlas — Detail** — 노드 click 시 우측 상세 레일(발췌·태그·연결 `→ n out · ← n in`·Pin/Tag·L2 깊이 스테퍼) | `vinc.d.graph §2`·`atlas-visual-language §1.2` 방향 표기 |
| 3.7 | **C** | **Atlas — Empty(idle)** — `Nothing to map yet. Ingest a source or run a search…` + `Ingest sources` CTA | idle 카피 일치 |
| 3.8 | **C** | **Search** — idle(`Search across everything you've ingested…`) · Success(결과 ListRow + 매치 표기 `≡`/`≈`/`≡≈` + `{n} matches` + 도메인 그루핑) · 0건(`No matches for '{q}'…` + `Open Atlas`) | `vinc.d.search §2`·`atlas-visual-language §4.2` 매치 표기 반영 |
| 3.9 | **C** | **Packs** — idle(`Create a pack` · `Import a pack` 스코프 칩+형식) · Success(`Pack ready — {n} records, {size}` / `Imported — {created} new…`) + **Share 시트**(스코프 요약·미포함 고지·미니 그래프 미리보기·`Save pack file`) | `vinc.d.packs`·`atlas-visual-language §5.1` Share 시트 반영 |
| 3.10 | **C** | **Settings(팝오버)** — 3항목 고정(Lock·MCP·Data). D38 반영: Data=`Workspace`, 엔진주소/시크릿은 `Advanced` 접힘. MCP 관찰 패널(연결 상태 + 도구 호출 로그) | `vinc.d.settings §1` 3항목·카피 일치, 4항목 이상 없음 |
| 3.11 | **O** | App 표면 리뷰 승인(특히 D2 Atlas 표현·D3 정본 관계 결정 반영 확인) | 발주자 OK → Phase 4 |
| 3.12 | **C** | (스트레치) 각 표면 loading(Skeleton)·error(ErrorBanner `--danger`+alert-triangle) 상태 + Command palette(Cmd+K) 프레임 | 4-State 완비 |

### Phase 4 — Auth 화면 (웹 계정 · 앱과 분리)

> 정본 = `site/login/index.html`·`site/account/index.html` + `vinc.d.remote-profile`. **하드 인바리언트**: 웹 계정은 앱과 완전 분리 — 모든 Auth 프레임에 "데스크톱 앱은 계정 불필요·오프라인" 분리 카피 필수, 앱이 phone-home 하는 함의 금지.

| # | owner | 태스크 | 완료선 |
|---|---|---|---|
| 4.1 | **C** | **Login — input** — authcard(로고·`Sign in to Vinc`·이메일 필드·`Send magic link`) + 분리 노트(`The desktop app still needs no account — this is for the website only.`) | 프레임 + 분리 카피 명시 |
| 4.2 | **C** | **Login — sent** — sentmark(메일 아이콘) + `Check your inbox` + 15분 만료 안내 | 전송 후 상태 프레임 |
| 4.3 | **C** | **Account** — 프로필 카드(아바타·이메일·Free plan 배지·Sign out) + Vinc Pro(비활성 `Notify me`) + 삭제 카드(danger, `Your local app data is never touched.`) | `site/account` 4카드 구성 일치 |
| 4.4 | **C** | **Newsletter signup** — Account 의 `Release news` 토글(on) 카드 + 분리 카피. 독립 구독 진입점이 필요하면 별도 mini-form 프레임 | 뉴스레터 토글/폼 + unsubscribe 문구 |
| 4.5 | **O** | Auth 리뷰 승인 | 발주자 OK → Phase 5 |

### Phase 5 — light/mobile 변형 + v0.2 As-built 정리 + 핸드오프

| # | owner | 태스크 | 완료선 |
|---|---|---|---|
| 5.1 | **C** | **Light 변형** — 마케팅·Auth 프레임을 변수 모드 스와핑(Light)으로 복제. **App 표면은 항상 다크(D38)라 light 변형 제외** | 마케팅/Auth 각 프레임 Light 컷 존재 |
| 5.2 | **C** | **Mobile 변형** — 마케팅 4종 + Login/Account 를 375px 폭으로(헤더 links 숨김·그리드 1열). App 은 대표 표면(Intake·Atlas)만 좁은 폭(레일 collapse) 참고 컷 | 반응형 변형 프레임, 가로 스크롤 없음 |
| 5.3 | **C** | **v0.2 As-built 페이지 정리** — App 페이지가 5표면 parity 도달 후 `Archive · v0.2` 로 개명·잠금(즉시 삭제 금지) | 페이지 개명/잠금, 최신 정본과 혼동 없음 |
| 5.4 | **C** | 핸드오프 정리 — 프레임 명명 규칙(`App/Intake — Empty` 등)·컴포넌트 설명·변수 문서화 | 네이밍 일관, dev mode 열람 가능 |
| 5.5 | **O** | 최종 리뷰 + `Archive · v0.2` 삭제 승인 | 발주자 OK → 트랙 종료 |

---

## 결정 필요 (decisions the owner must make)

| # | 결정 | 선택지 | 권장 기본값 |
|---|---|---|---|
| D1 | **Figma 커넥터 인가 시점** (P1 — 전면 차단) | 지금 인가 / 보류 | **지금 인가** — 미인가 시 전 트랙 정지. 인가 후에만 첫 세션 착수 |
| D2 | **Atlas 그래프의 Figma 표현** — 실제 Atlas 는 3D(three.js·글래스 구/육각기둥·D21·D35·D39·D40)이며 Figma·WebGL 재현 불가 | (a) 정적 2D 투영(마케팅 목업 스타일·모노크롬 마크) (b) 유사 3D 정지 렌더 붙임 | **(a) 정적 2D 투영** — Figma 는 레이아웃·크롬·상태의 정본, 3D 리얼리즘은 코드/three.js 담당. 프레임에 "실 렌더러 3D" 주석 |
| D3 | **App 프레임의 정본 관계** — v0.1.0 앱 UI 는 이미 코드로 존재(`app/index.html`) | (a) 전방 설계 스펙(5표면 D 계층에 앞서 맞춤) (b) as-built 문서화 | **(a) 전방 설계 스펙** — D 계층 SSoT(5표면) 기준. as-built 는 `v0.2` 아카이브가 보관 |
| D4 | **Figma 카피 언어** — D18 영어 정본 vs D42 en/ko 이중 | (a) 영어 단일(정본) (b) 변수 모드로 ko 병행 | **(a) 영어 단일 먼저** — ko 덱은 `copy.ts`/사이트 DECK 가 정본. 필요 시 Phase 5+ 에서 ko 모드 추가 |
| D5 | **마케팅 프레임 정합 수준** — 사이트가 이미 LIVE | (a) 라이브 HTML 1:1 미러(폰트 제외) (b) 디자인 전방 재해석 | **(a) 1:1 미러** — 라이브가 마케팅 시각 정본. 디자인이 코드와 어긋나면 안 됨. 폰트만 실폰트로 승격 |
| D6 | **4-State 범위** — 표면당 error/loading 포함 여부 | 핵심 상태만 / 4상태 완비 | **핵심 상태(task 지정) 우선**, error/loading·Command palette 는 3.12 스트레치 |
| D7 | **Light/Mobile parity 범위** | 전 프레임 완전 parity / 선택적 | **Light=마케팅·Auth 전부(App 제외)**, **Mobile=마케팅+Login/Account 우선** — 비용 대비 커버리지 |

---

## 리스크·함정

- **R1 · 인가 없이는 0 진행**: 이 트랙은 Figma MCP 없이는 아무 산출물도 못 낸다. D1 미결 시 계획 전체가 대기. → 발주자 인가를 최우선 단일 게이트로.
- **R2 · Space Grotesk 'SemiBold' 부재**: 사이트 heading 은 font-weight 640/680/700 을 쓰는데 Space Grotesk 는 SemiBold 웨이트가 없다. **Bold 로만 매핑**(중간 무게는 Bold+letter-spacing 조정), SemiBold 지정 시 렌더 깨짐/폴백.
- **R3 · Atlas 3D ↔ 2D 간극**: `atlas-visual-language`(D39 다크 반전·D40 글래스 구)는 3D 전제. Figma 정지 2D 로 만들면 실제 앱과 시각 괴리가 생김 → D2 를 프레임 주석으로 명문화하고, 마크=타입 규율(육각=Concept·원=Record·이중원=Domain)·모노크롬 잉크만 2D 로 이식.
- **R4 · 앱 창은 Light/Dark 모드 밖(D38)**: App·목업·Guide 장면은 **항상 다크 `--w-*`**. 마케팅 Light/Dark 컬렉션을 잘못 바인딩하면 라이트 테마에서 앱 창까지 밝아지는 오류. Window 컬렉션(0.4) 분리 필수.
- **R5 · 하드코딩 hex 유혹**: `--w-*` 값이 hex(#16130F 등)라 변수 없이 직입력하기 쉬움 → 디자인 게이트 D-R 위반. 반드시 Window 변수로.
- **R6 · em-dash 카피 오염(D38)**: 스펙 원문(예 `Ingesting {n} of {m} — {domain}`)에 em-dash 가 남아 있다. 화면 카피 이식 시 마침표·콜론·중점으로 정규화.
- **R7 · 커넥터 표면의 인바리언트 오독**: Auth·Guide MCP 장면에서 "앱이 로그인/원격 접속" 함의가 새면 하드 인바리언트(앱 무로그인·오프라인·서버가 로컬 볼트 미접근) 위반. 분리 카피 필수 검수(4.x·2.4).
- **R8 · 페이지 단위 읽기 미준수 → 컨텍스트 폭주**: 5페이지를 한 번에 읽으면 토큰 초과. 페이지별 `get_metadata`/`get_screenshot` 로 국소화.
- **R9 · 좌표 추정 드리프트**: 오토레이아웃에 의존하면 라이브와 간격 어긋남. 수치 좌표 명시로 1:1 정합 유지(D5).
- **R10 · v0.2 As-built 조기 삭제**: parity 전에 지우면 as-built 이력 유실. 개명·잠금 후 최종 승인에서만 삭제(5.3·5.5).

---

## 개략 공수 (rough effort per phase)

> 단위 = Claude/Figma-MCP 작업 세션(발주자 인가·리뷰 대기 제외). MCP 빌드는 토큰·왕복이 무거워 프레임당 실측 후 조정.

| Phase | 범위 | 개략 공수 |
|---|---|---|
| 0 | 전제 해소 + Foundations 감사 + Window 컬렉션 + 아이콘/텍스트/공유 컴포넌트 | 1–1.5 세션 (+ O 인가·폰트 확인 선행) |
| 1 | Home 6블록 완성 | 1–1.5 세션 |
| 2 | Download·Release·Guide(6장면 대표 프레임) | 2–2.5 세션 (Guide 가 최대 비중) |
| 3 | App 5표면 + Settings 핵심 상태(Intake 3·Records·Atlas 3·Search·Packs·Settings) | 3–4 세션 (Atlas·Packs Share 시트가 최대) |
| 4 | Auth 4프레임(Login input/sent·Account·Newsletter) | 0.5–1 세션 |
| 5 | Light·Mobile 변형 + v0.2 정리 + 핸드오프 | 1.5–2 세션 |
| **합계** | | **약 9–12.5 세션** (핵심 상태 기준. 4-State 완비·Command palette·L1/L2 Atlas 등 스트레치 시 +2–3 세션) |

**임계 경로**: D1 인가(O) → Phase 0 → Phase 1 → …(각 Phase 말 O 리뷰가 다음 Phase 게이트). Phase 3 이 최장 구간이며 D2·D3 결정이 3.5–3.9 착수를 선행 게이트한다.

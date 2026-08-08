# 앱 시안 — Deep Signal (v1, 2026-07-19)

데스크탑 앱의 새 디자인 방향 시안. 발주자 레퍼런스(박물관 디스플레이 — 다크 스크린 중앙의
글로우 데이터뷰) 기반. **Figma `App (실제 화면)` 페이지에 전면 구성 완료**, 팔레트/노드 스타일은
`App (Design system)` 페이지 `Foundations — App 시안 (Deep Signal)` 프레임.

## 콘셉트

- **다크 관측실(observatory)**: 최심부 다크 배경 + 희미한 블루프린트 그리드 위에
  지식 그래프가 **성좌(constellation)**처럼 떠 있다. 깊이는 글로우 강도로 표현.
- **컬러 LNB**: 좌측 72px 아이콘 레일 — 배경보다 살짝 밝은 인디고 면(`bg-2`),
  활성 아이콘은 인디고 칩(`accent-deep`) + 페리윙클 글로우.
- **옆에 붙는 패널**: 노드/항목 클릭 시 우측에 **프로스티드 글라스 패널**(반투명 `bg-1` 82% +
  BACKGROUND_BLUR 24, 좌측 글로우 엣지 라인 2px)이 붙고, 포커스 노드에서 점선 커넥터가 이어진다.
- **로고 연속성**: 액센트 = 로고 tip 인디고의 라이튼 계열(페리윙클). 앱과 로고가 같은 혈통.

## 팔레트 (draft — Phase 3에서 tenants/desktop 토큰으로 정식화)

| 토큰(안) | HEX | 용도 |
|---|---|---|
| bg-0 | `#0C0D13` | 앱 배경(최심부) |
| bg-1 | `#12141D` | 서피스(필·카드·패널 베이스) |
| bg-2 | `#191C2A` | LNB·부상 표면 |
| line | `#262A3D` | 헤어라인 |
| text | `#E8E7EF` | 본문(쿨 아이보리) |
| text-soft | `#A7A9BF` | 보조 |
| text-mute | `#6E7189` | 캡션·메타·비활성 아이콘 |
| accent | `#8F96D9` | 페리윙클 인디고 — 글로우·엣지·포인트 |
| accent-strong | `#B9BEF0` | 글로우 코어·활성 아이콘·CTA 텍스트 |
| accent-deep | `#3A4070` | 액티브 면·CTA 배경·세그 썸 |

글로우 = `accent` 색 DROP_SHADOW(offset 0, radius 6–30, 강도는 계층별).

> ⛔ **글로우 규칙 (발주자 확정 2026-07-19): 글로우는 그래프 노드 전용.**
> 칩·버튼·카드·입력필드·패널 엣지 등 모든 UI 크롬은 플랫(색 DROP_SHADOW 금지).
> 검정 depth 그림자(패널 부상용)는 글로우가 아니므로 허용.

## 그래프 노드 계층 (Foundations에 샘플)

| 계층 | 크기 | 채움 | 글로우 |
|---|---|---|---|
| focus | 26 | accent-strong | radius 30 · 0.9 |
| near | 16 | accent | radius 16 · 0.5 |
| mid | 13 | accent-deep + accent 링 | radius 10 · 0.35 |
| far | 9 | bg-2 + mute 링 | radius 6 · 0.18 |

엣지: accent 1px, 포커스 인접 0.5 · 일반 0.28 · 원거리 0.14 투명도, 살짝 커브.

## 구성된 화면 (App (실제 화면) 페이지)

| 프레임 | 내용 |
|---|---|
| Atlas · Graph (42:15) | 대표 화면 — 그리드+성좌 그래프+레전드+줌+⌘K+2D/3D 세그 |
| Atlas · Node detail (42:17) | **핵심 인터랙션** — 노드 클릭 → 우측 프로스티드 패널(개념 정보·strong ties 가중치 바·액션) + 점선 커넥터 |
| Intake · Empty (42:3) | 글로우 대시드 드롭존 + 로고 마크 + 샘플 CTA |
| Records · List (42:10) | 필터 칩 + 리스트 행(●◐○ 상태 마크 유지) + hover 행(accent 8% 틴트) |
| Search · Results (42:22) | 글로우 검색 필드(연산자 하이라이트) + 도메인 그룹 결과(≡ 정확/≈ 의미 마크) |
| Packs · Idle (42:27) | 팩 카드(좌측 액센트 라인·글로우 활성 카드) + 고스트 새 팩 |
| Settings · Default (42:32) | 좌 메뉴 + 폼 행 + 테마 세그 + Synced 뱃지 + 토글 |

빈 상태 변형 프레임(Intake Ingesting/Success, Records·Atlas·Search Empty, Packs Share ready)은
skeleton 유지 — 방향 확정 후 확장.

## 구조 패턴 — 발주자 선택 확정 (2026-07-19, 전부 B안)

3×3 탐색 보드(`App (Design system)` 페이지 `Patterns — 재구성 탐색`)에서 선택, **전 화면 반영 완료**:

| 축 | 확정안 | 스펙 |
|---|---|---|
| **LNB** | **B · 확장 사이드바 232px** | 로고+워드마크 / 아이콘+라벨 내비 5행(활성 = ACD 45% 행 배경 + 굵기. **좌측 색띠 없음** — 개정 2026-08-02) / DOMAINS 트리(도트+이름+카운트, 깊이 = 들여쓰기 + 안내선 + 잉크 2단 + 도트 차등) / 하단 Settings + "Synced · vincs.app" 푸터. 캔버스는 x=232부터 |
| **Settings** | **B · 우측 슬라이드오버** | 작업 화면 위 딤(black 42%, 사이드바 제외) + 우측 380px 패널(blur 24 + 검정 depth 그림자). 섹션 칩 탭 + 폼 행. 플로팅 카드와 같은 글라스 문법 |
| **패널** | **B · 플로팅 글라스 카드** | 노드 클릭 → 노드 옆 300px 글라스 카드(blur 20, 좌측 2px ACS 바) + 점선 커넥터 + 노드 주위 점선 선택 링. compact 정보(개념·ties 3개·Focus lens·자세히→). "자세히"로 풀 패널 확장 |

주의: **인스턴스 `resize()`는 내부 미스케일** — Logo/Mark 글리프·벡터에 SCALE 제약 설정으로 해결(재발 방지).

> **개정 2026-08-02 — 좌측 색띠 폐기(LNB 한정).** 위 표의 "좌측 2px ACS 바"를 내린다.
> 사유는 발주자 지적으로, 2026-07-31 문서 사이트에 먼저 내려진 것을 앱에 확대 적용한
> 것이다: "각 줄 옆에 색 막대가 서면 장식이 내용보다 먼저 읽힌다." 앱에서는 홈 행이
> Atlas 표면에서 항상 활성이라 주제를 고르는 순간 **띠가 두 개 동시에 서는** 결함도
> 있었다. 활성 표시는 홈·주제·문서 모두 **채워진 알약 + 굵기**로 통일한다.
> 플로팅 패널(위 표 3행)의 "좌측 2px ACS 바"는 **그대로 둔다** — 그쪽은 목록이 아니라
> 단일 카드라 지적의 논지(줄마다 막대가 선다)가 적용되지 않는다.

## 공통 컴포넌트 라이브러리 (2026-07-19, `App (Design system)` 페이지)

발주자 지시로 **DS 페이지(컴포넌트·변수) ↔ 실 화면(인스턴스)** 이원화 완료. 실 화면은 컴포넌트 인스턴스만 사용.

**Figma 변수**: 컬렉션 `App · Deep Signal (draft)` — 팔레트 10색, 스코프·WEB 코드신택스(`var(--app-*)`) 지정. 컴포넌트 fill/stroke/text 전부 변수 바인딩(하드코딩 0).

| 컴포넌트 | 변형/프로퍼티 | 사용처 |
|---|---|---|
| `App / NavItem` | State=Default/Active · label TEXT · icon INSTANCE_SWAP | Sidebar 내비 행 |
| `App / DomainRow` | State=Default/Active · name/count TEXT | Sidebar 도메인 트리 |
| `App / Sidebar` | NavItem×5 + DomainRow×3 조합 · 화면별 중첩 변형 오버라이드로 활성 탭 지정 | **13개 화면 전부 인스턴스** |
| `App / Button` | Kind=Primary/Ghost · label TEXT | CTA 전반 |
| `App / Chip` | State=Default/Active · label TEXT | 필터·개념 칩 |
| `App / ListRow` | State=Default/Hover · mark/title/meta TEXT | Records 리스트 |
| `App / GraphNode` | Tier=Focus/Near/Mid/Far (글로우는 여기만) | Atlas 성좌 |
| `App / PackCard` | State=Default/Highlight · name/counts/meta TEXT | Packs |
| `App / ProgressBar` | fill 폭 오버라이드로 진행률 | Intake Ingesting |
| `App / EmptyState` | title/desc TEXT + Button 중첩 | Empty 3종 |

**분기 화면 6종 신규**: Intake Ingesting(처리 패널+ProgressBar, ●◐○ 표기) · Intake Success(check+개념 Chip+Button 2종) · Records/Atlas/Search Empty(EmptyState 인스턴스, Atlas는 그리드 배경) · Packs Share ready(PackCard Highlight+공유 링크+커뮤니티 토글). → **13개 화면 전체 완성.**

⚠️ Figma 컴포넌트 프로퍼티 키는 `name#<id>` 형태로 id 접미사가 붙음 — 인스턴스에서 `componentProperties` 키를 동적 조회해서 setProperties 할 것(접미사 추측 금지).

## 구현 메모 (Phase 5~6 연결)

- 구 Ink&Paper의 "상태는 기호로" 규율 중 **●◐○·≡≈ 표기는 유지**하되 색과 결합.
- 글로우는 CSS `box-shadow`/`filter: drop-shadow`, 그래프는 three.js emissive+bloom 또는 sprite 텍스처.
- 프로스티드 패널 = `backdrop-filter: blur(24px)` — 웹 Vinc CSP에서 허용됨(unsafe-inline 스타일과 무관).
- 폰트는 시안에서 Inter 임시 사용 — 브랜드 폰트 미확정.
- ⚠️ WCAG: text-mute `#6E7189` on bg-0은 본문 금지(캡션 전용). Phase 3 게이트에서 쌍 검증.

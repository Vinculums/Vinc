# 홈페이지 시안 — Home · Signal (v1.1, 2026-07-19)

홈페이지(+웹 Vinc가 따를) 새 디자인. **다크 우선(발주자 확정)** — 기본 = Dark 모드(Deep Signal 혈통,
bg `#0C0D13` 계열 + 페리윙클 액센트). Light 모드는 로고 레퍼런스(그레인 그레이 위 잉크 ▽ + 인디고 tip)에서
도출한 "그레인 그레이 + 잉크 + 인디고"로, **테마 토글용 보조 모드**. 로고→홈→데스크탑 앱이 한 핏줄.

Figma 화면 7종은 전부 화면 레벨 색까지 변수로 재바인딩(166 노드)하고 프레임에
`setExplicitVariableModeForCollection(Dark)` 적용 — 모드만 바꾸면 라이트 시안도 즉시 볼 수 있다.
상수 예외(모드 무관): 앱 목업(항상 다크), 최종 CTA 밴드(항상 인디고), Guide 미니 비주얼(항상 다크).

구 Ink&Paper 자산은 `Homepage (Design system)` 페이지에서 **완전 삭제**(Foundations 스와치·Notation 아이콘
쇼케이스·구 로고 탐색). 보존: 발주자 로고 정본(78:164), `Logo / Mark` 컴포넌트(91:164), 다크 샘플.
Notation 아이콘 **메인 컴포넌트**(9:88 등)는 캔버스 밖에 살아 있어 앱 사이드바 참조는 안전.

## Figma 변수 — 컬렉션 `Home · Signal (draft)` (Light/Dark 2모드)

| 토큰 | Light | Dark | 용도 |
|---|---|---|---|
| bg/page | `#F2F2F0` | `#0C0D13` | 페이지 배경 |
| bg/section | `#E9E9E6` | `#12141D` | 교차 섹션 밴드 |
| bg/card | `#FBFBFA` | `#191C2A` | 카드 |
| text/primary | `#17171A` | `#E8E7EF` | 본문 |
| text/soft | `#55565E` | `#A7A9BF` | 보조 |
| text/mute | `#82838D` | `#6E7189` | 캡션 |
| border | `#DEDEDA` | `#262A3D` | 헤어라인 |
| accent/cta | `#32355C` | `#3A4070` | CTA 면(=로고 tip 인디고) |
| accent/cta-text | `#F2F2F0` | `#B9BEF0` | CTA 텍스트 |
| accent/link | `#4A4F8C` | `#8F96D9` | 링크·포인트 |
| accent/ring | `#8F96D9` | `#8F96D9` | 포커스 링 |

코드 신택스 `var(--home-*)`. 스코프 지정 완료. 웹 다크모드 = `data-theme="dark"`에서 Dark 모드 값.

## 컴포넌트 (`Homepage (Design system)` 페이지, 전부 변수 바인딩)

| 컴포넌트 | 변형/프로퍼티 |
|---|---|
| `Home / Button` | Kind=Primary/Quiet · label TEXT |
| `Home / Badge` | label TEXT (dot+pill) |
| `Home / Card` | title/desc TEXT + 로고 마크 아이콘 |
| `Home / Header` | 로고+워드마크 / 링크 5 / 로그인 버튼 — 전 화면 인스턴스 |
| `Home / Footer` | 브랜드 / © / 링크 4 — 전 화면 인스턴스 |

## 화면 (`Homepage (실제 화면)` 페이지 — **오토레이아웃 섹션 구조**)

| 화면 | 구성 |
|---|---|
| Home · Desktop (41:4) | Header → 히어로(배지·56px 헤드라인·CTA 2종·**다크 Deep Signal 목업**(GraphNode 인스턴스 성좌)) → 사용법 3단계 밴드 → 기능 카드 6(2×3) → 세 가지 서약 밴드 → 인디고 최종 CTA → Footer |
| Download (41:8) | OS 카드 3종("현재 OS" 하이라이트) + 체크섬 노트 |
| Release (41:12) | 체인지로그 카드 3(v0.3.1 현재 강조) + 로드맵(●◐○ 표기) |
| Guide (41:16) | 4단계 블록 — 각각 다크 미니 비주얼(성좌/채팅 버블/sync 다이어그램) |
| Login (152:174) | 센터 인증 카드(로고·매직링크) — 신규 "Account & Community" 섹션 |
| Account (153:199) | 프로필·MCP 키·뉴스레터 토글·계정삭제 존 |
| Community (154:221) | 팩 갤러리 2×3(도메인 분포 바 = 인디고 3단계) |

## 규칙

- **홈에는 글로우 없음** — 글로우는 앱 목업 안의 그래프 노드(GraphNode 인스턴스)에만 존재.
- 앱 목업은 라이트 페이지 위에서도 **항상 다크**(데스크탑 앱 광고) — 구 `--w-*` 관행 계승, 단 컴포넌트 인스턴스로.
- 상태 표기 언어(●◐○) 유지.
- ⚠️ Figma 함정: 프레임이 **오토레이아웃이면 자식 x/y는 무시**(41:4가 원래 V 오토레이아웃이라 초기 빌드가 스택됨 → 섹션 구조로 재편). `resize()`는 sizing 모드를 FIXED로 리셋 — **AUTO는 resize 후에** 설정.

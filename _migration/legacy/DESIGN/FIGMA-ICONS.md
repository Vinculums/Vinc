# Figma 아이콘 깨짐 — 원인 분석 & 복구

> 대상 파일: **WEG's team · `WUXLeDDNBqih2KTZOz9wCX`**. 증상: Icons 페이지의 Notation
> 아이콘 43개가 **검정(잉크) 사각형 덩어리**로 렌더(글리프 형상 소실). 아래는 근본
> 원인·진단 근거·복구(실제 반영 완료)다.

## 요약 (TL;DR)
- **근본 원인 — `currentColor` 미해소.** 원본 SVG 43개가 전부
  `stroke="currentColor"` / `fill="currentColor"` 로 색을 그린다. 이는 WebView(Tauri
  UI)에서는 **정상**(CSS `color` = 잉크 토큰 상속)이지만, **Figma 는 `currentColor`
  를 해소하지 못한다**(CSS 캐스케이드 컨텍스트 부재). 게다가 root `fill="none"` +
  자식 `stroke="currentColor"` 구조가 겹쳐, 임포트 시 벡터 형상이 **채운 사각형 하나로
  붕괴**한다(관측: 각 `ic/*` 컴포넌트가 자식 1개 = 잉크 solid Frame). → "깨짐".
- **복구 — 원본 불변, Figma 사본만 잉크 hex 로.** `figma_icons.py` 로
  `currentColor→#14110D` 변환한 SVG 를 Plugin API 로 각 컴포넌트에 **제자리 이식**하고
  fill/stroke 를 `foreground`(잉크) 변수에 바인딩했다(라이트/다크 자동 추종).

## 진단 근거 (2026-07-06)
| 점검 | 결과 |
|---|---|
| SVG 43개 currentColor 사용 | **43/43** (`figma_icons.py --check`) |
| 외부 참조·마스크·필터·`<use>`·`url()` | 0건 (임포트 방해 요소는 currentColor 뿐) |
| root `width/height="24"` · `viewBox 0 0 24 24` | 전수 정상 |
| Figma 파일 페이지 | `Foundations` · `Screens`(8) · `Icons`(44 자식 = 제목 + 43 `ic/*`) 존재 |
| 깨짐 형태(복구 전) | 각 `ic/*` = 자식 1개(잉크 solid Frame) — 글리프 벡터 소실, 사각형만 |

> 주의: MCP `get_metadata` 를 nodeId 없이 부르면 페이지가 **증분 로드**되어 첫 페이지
> (`Foundations`)만 나올 수 있다. 페이지 전수는 `figma.root.children` 로 확인해야 한다.

### 왜 WebView 는 되고 Figma 는 안 되나
`currentColor` 는 렌더 시점에 상속된 CSS `color` 로 치환되는 **키워드**다.
- **Tauri WebView**: 아이콘을 인라인 SVG 로 DOM 에 심고(`Icon.tsx`), 소비 측이
  `color: hsl(var(--foreground))` 를 주면 currentColor 가 잉크로 해소된다 → 정상.
- **Figma**: 벡터 노드에 CSS `color` 개념이 없다. currentColor 는 알 수 없는 값 →
  검정 폴백 또는 무시. 그래서 **같은 SVG 인데 앱에서는 멀쩡하고 Figma 에서만 깨진다.**

## 복구 (SSoT 보존)
원본 SVG 는 **바꾸지 않는다** — currentColor 는 코드/WebView 소비 경로의 정본이다.
Figma 로 가져갈 **사본만** 구체 잉크 hex 로 변환한다.

```sh
# currentColor → primitive.ink(#14110D). 산출: DESIGN/assets/icons-figma/ (43개)
python DESIGN/scripts/figma_icons.py            # 라이트
python DESIGN/scripts/figma_icons.py --theme dark   # 다크(d_text #EAE5DD)
```

### 반영 완료 (2026-07-06 · Figma Plugin API)
기존 `Icons` 페이지의 `ic/*` 컴포넌트 43개를 **제자리 복구**했다(컴포넌트 정체성 유지):
1. 각 컴포넌트의 깨진 자식(잉크 solid Frame)을 제거.
2. `icons-figma/{name}.svg`(currentColor→#14110D)를 `figma.createNodeFromSvg()` 로
   이식 → 실제 벡터 글리프(획+채움) 복원, 24 글리프를 32 프레임 중앙 정렬.
3. 모든 fill/stroke 를 `foreground`(잉크) 변수에 `setBoundVariableForPaint` 로 바인딩
   → 라이트/다크 자동 추종(hex 박제 아님). 스크린샷으로 43개 전수 렌더 확인.

> **미결(후속):** `Screens` 페이지의 아이콘은 컴포넌트 **인스턴스가 아니라 평면 복사본**
> (instance 0건)으로 임베드돼 있어 위 복구가 전파되지 않는다. 화면별 임베드 아이콘은
> 별도 세션에서 인스턴스로 재배치하거나 동일 방식으로 제자리 교체해야 한다.

### 재발 방지
- 아이콘을 Figma 에 넣을 때 **원본(currentColor) 직접 임포트 금지** — 반드시
  `figma_icons.py` 산출물을 쓴다.
- CI/게이트에 `figma_icons.py --check` 를 두어 원본 규약(currentColor=WebView SSoT)과
  Figma 사본 파이프라인이 분리 유지되는지 확인.

## 파일 맵
| 산출물 | 경로 |
|---|---|
| 원본 아이콘(currentColor · WebView SSoT) | `DESIGN/assets/icons/*.svg` (43) |
| Figma 사본(ink hex) | `DESIGN/assets/icons-figma/*.svg` (43, 생성물) |
| 변환·검사 스크립트 | `DESIGN/scripts/figma_icons.py` |
| 앱 소비(인라인 주입) | `Vinc/app/src/components/Icon.tsx` |

<!-- vinc-export
doc_id: vinc.d.motion-research
title: Vinc × Motion / bklit 적용 분석
origin: Vinc-docs/vinc/D/vinc.d.motion-research.md@5090690
chunks: 12
verified: true
content_hash: d0e6c293a738f286637de77326bf3812e4e360ded2eda5ad0e8e39e88602cfd9
updated_at: 2026-08-01T16:20:37.160Z
-->

---
doc_id: vinc.d.motion-research
layer: D
domain: motion
title: Vinc × Motion / bklit 적용 분석 — 모션 시스템·차트 인터랙션 도입 연구
status: draft
authority: research-input
version: v0.1
owner: vinc
last_updated: 2026-07-25
inherits_from: [vinc.d.atlas-visual-language, vinc.d.graph-views, vinc.d.glass-language, vinc.d.index]
tags: [vinc, D, motion, animation, research, atlas, sankey, tooltip, dec-candidate]
---

# Vinc × Motion / bklit 적용 분석

> **지위: 연구 입력(research-input).** 발주자 업로드 원문(2026-07-25)을 그대로 보존한다.
> 외부 자료 4종(motion.dev 3·bklit 2)과 현행 UI 영상(21s) 분석이며 SSoT 아님 —
> 채택은 §7 열린 질문의 DEC 확정으로만 정본화된다(recede 수치는 D32 개정,
> 모션 토큰은 디자인 토큰 계층 확장). **파일링 시점 코드 대조는 부록 2(as-built)** —
> 본문 §4-1 "즉답형" 서술 등 현행 코드와 다른 지점이 있으므로 실행 전 부록 2를
> 먼저 읽는다. 본문이 참조하는 "1차·2차 리서치"는 리포에 문서로 없고 코드
> D45-V·D45-V2·D45-E 시리즈로 as-built 존재(부록 2 §V7). 화면 문구 규율
> ([[vinc.a.terms]]·em-dash 금지)은 구현 산출물에 적용 — 본 문서의 기호는 문서 텍스트다.

## 모션 시스템·차트 인터랙션을 Vinc의 제약 안으로 가져오는 방법

작성 기준: 업로드 영상(Atlas 지도 → Flow → 타임라인 → 합계 생키 → 상세 패널, 21초 워크스루)의 현행 UI 확인 + 아래 4개 자료 원문 분석.

- Motion AI Kit: https://motion.dev/docs/ai-kit
- Motion UI: https://motion.dev/ui
- Motion JS(quick start): https://motion.dev/docs/quick-start
- bklit Sankey: https://bklit.com/docs/components/sankey-chart
- bklit Tooltip: https://bklit.com/docs/utility/tooltip

---

## 0. 결론 요약 (3줄)

1. **코드는 못 가져오고, 수치와 문법은 전부 가져올 수 있다.** Motion UI와 bklit은 React + Tailwind + shadcn 전제라 손수 만든 DOM/SVG 프론트에 직접 설치가 불가능하다. 대신 두 곳이 공개한 물리 파라미터(스프링 stiffness/damping), 안무 타임라인(생키 진입 0~600ms/200~1100ms), recede 불투명도 수치(0.4/0.1), 툴팁 해부도는 라이브러리 없이 그대로 이식 가능하다.
2. **Motion 런타임(vanilla JS)은 유일하게 "제약 통과" 도입 후보다.** npm 설치 → Vite 번들 인라인이므로 CSP(외부 CDN 금지)를 위반하지 않고, mini `animate()`는 2.3kb, Three.js 객체 값(카메라 등)도 애니메이션할 수 있어 sigma·three·3d-force-graph 스택과 공존한다. 더 가볍게는 **CSS `linear()` 스프링 곡선**(런타임 0)으로 크롬 층 전이 대부분을 해결할 수 있다.
3. **Motion AI Kit은 앱이 아니라 개발 루프에 넣는 물건이다.** Claude Code에 스킬/MCP로 설치해 최신 Motion 문서 컨텍스트, MotionScore 렌더 비용 감사(/motion 스킬), CSS 스프링 생성을 쓰는 것. 앱 런타임의 오프라인·결정론 불변과 무관하다(개발 머신에서만 네트워크 사용). 단, Motion+ 유료 구독이 전제.

---

## 1. 자료 4종의 정체와 Vinc 관련성 판정

### 1-1. Motion (라이브러리, vanilla JS 경로)

- `npm install motion` 후 `import { animate, stagger } from "motion"`. 하이브리드 엔진: opacity/transform/filter 등 하드웨어 가속 가능한 값은 브라우저(WAAPI)로, 나머지는 JS로. mini HTML/SVG용 `animate()`는 **2.3kb**.
- 애니메이션 대상: CSS 속성, SVG 속성·패스, 독립 transform, **임의 JS 객체**. 공식 예제가 Three.js 큐브 회전을 `animate(cube.rotation, {...})`로 직접 구동한다. → Vinc의 3D 지도 카메라(L0→L1→L2 전환)를 spring으로 구동할 수 있다는 뜻.
- `stagger(0.1)`로 다중 요소 지연 오프셋, `type:"spring", stiffness/damping`으로 물리 스프링, `hover()/press()/inView()` 제스처, `motionValue/springValue`로 캔버스 draw 루프에 값 주입 가능.
- **판정: 채택 후보 1순위 런타임.** CDN `<script>` 로드는 CSP 위반이므로 금지, npm+번들 경로만.

### 1-2. Motion AI Kit

- 구성 4종: ① Context(최신 Motion 문서 + 400+ 프리미엄 예제 소스를 에이전트에 공급), ② MotionScore for Agents(`/motion` 스킬이 소스코드·로컬 실행 사이트의 Motion/CSS/GSAP 애니메이션을 렌더 비용 기준으로 채점하고 수정안 제시), ③ CSS 스프링 생성(`linear()` 이징 곡선을 생성, "라이브러리 없이 스프링" — 예: `600ms linear(0, 0.0121 ...)`), ④ 전환 에디터(IDE 안 실시간 편집).
- Cursor 원클릭 외에 **Claude Code용 설치 경로가 공식 지원**된다. Motion+ 구독 필요(토큰 기반 MCP).
- **판정: 앱에 넣는 것이 아니라 WEGO의 Claude Code 개발 루프에 넣는 도구.** 특히 ③ CSS 스프링 생성은 빌드 타임에 곡선 문자열을 뽑아 앱에 정적 CSS로 심으므로, 오프라인·결정론 제약과 완전히 양립한다.

### 1-3. Motion UI

- React 마케팅 섹션 26종(히어로·프라이싱·테스티모니얼·bento·오버레이·버튼·로더 등). Tailwind + shadcn 토큰 위에서 동작, shadcn CLI로 설치.
- **핵심 훔칠 것은 섹션이 아니라 `motion.theme.ts` 구조다.** 모든 컴포넌트가 전환을 하드코딩하지 않고 단일 테마 파일에서 해석한다:
  - transitions: `snap {stiffness 1218, damping 70}` · `ui {305, 33}` · `gentle {110, 20}` · `lively {622, 17}` · `ambient {43, 13}`
  - stagger: `tight 0.04 / base 0.08 / relaxed 0.15`
  - travel(이동 거리 px): `hover 4 / enter 24 / section 48`
  - `reducedMotion: "calm"`
- 이는 Vinc의 "화면 문구는 카피 덱으로만"과 동일한 사상(하드코딩 금지, SSoT화)을 모션에 적용한 것이다. lime-design DTCG 토큰 SSoT에 **모션 토큰 계층**으로 편입 가능.
- **판정: 섹션·컴포넌트는 기각(React/Tailwind 전제 + 마케팅 정서), 테마 토큰 패턴과 개별 안무 아이디어(아코디언 height morph, 스켈레톤→콘텐츠 핸드오프, 시트 등장)만 채택.**

### 1-4. bklit (Sankey + Tooltip)

- shadcn/ui 위에 구축된 차트 컴포넌트 라이브러리. Sankey는 `@visx/sankey`(d3-sankey 레이아웃) + `motion` + `react-use-measure` 의존.
- **Sankey에서 훔칠 수치·안무:**
  - 진입 안무: 노드 scaleY+opacity 페이드인(0~600ms, 인덱스 stagger) → 링크가 stroke-dashoffset으로 소스→타깃 방향 성장(200~1100ms, stagger). 이징 `cubic-bezier(0.85, 0, 0.15, 1)`.
  - hover 문법: hover된 링크 + 양끝 노드는 완전 불투명 유지, **나머지 노드 fadedOpacity 0.4, 나머지 링크 0.1**. 이것은 D32("초점은 남고 나머지는 물러난다")의 정량화 그 자체다. Vinc가 아직 숫자로 못 박지 않았다면 이 값이 검증된 출발점.
  - 링크 그라디언트(소스색→타깃색)는 모노크롬 위반 → 단일 잉크 + 불투명도 차등으로 치환.
- **Tooltip에서 훔칠 해부도·역학:**
  - 해부 4요소: 크로스헤어(수직선, fade edges both/top/bottom/none + fadeLength, dasharray 지원) / 데이터 점 dots / 툴팁 박스(scale·fade + 화면 가장자리 flip 감지) / date pill(하단 날짜 티커).
  - 패널 추종 물리: 기본 `damping 20`으로 커서를 스프링 추종(0=즉시), `matchCrosshair`로 크로스헤어 스프링과 동기화, `springConfig/boxSpringConfig` 분리 오버라이드.
  - 박스 배경: 반투명 다크 + blur → **Liquid Glass 크롬 층과 정확히 같은 재질 언어.**
  - 테마는 CSS 변수(`--chart-crosshair` 등)로만 → Vinc 토큰 체계와 호환되는 사고방식.
- bklit 유틸에는 **Brush**도 있다(타임라인 기간 브러시 리서치의 참고 선례로 화면 수준 확인 가치 있음, 본 문서에서는 존재만 언급).
- **판정: 컴포넌트 설치 기각(React/shadcn/visx 의존), 수치·타임라인·해부도·물리 파라미터 채택.**

---

## 2. 전제 필터 (위반 제안 스스로 걸러낸 기록)

| 걸러낸 것 | 위반 조항 | 대신 한 것 |
|---|---|---|
| CDN `<script>`로 Motion 로드 | CSP(외부 CDN·네트워크 금지) | npm 설치 + Vite 번들 인라인 |
| Motion UI 섹션/컴포넌트 설치 | React·Tailwind·shadcn 미보유, 마케팅 정서 | motion.theme 토큰 구조와 안무 아이디어만 이식 |
| bklit 컴포넌트 설치 | 동일(React/shadcn/visx) | 수치(0.4/0.1, 0~600/200~1100ms)·해부도만 이식 |
| 생키 링크 그라디언트·노드 색 팔레트 | 모노크롬+초점색 1개 | 단일 잉크, 불투명도·굵기로만 위계 |
| confetti·border-beam·coverflow·page curtains | Ink & Paper 정서, 장식 과잉 | 조용한 크로스페이드 + 미세 travel |
| date pill 슬롯머신 숫자 애니 | 데이터 앱에서 시각 소음 위험 | 기각(원하면 열린 질문으로만 남김) |
| AI Kit MCP를 앱 런타임에 연결 | 오프라인·결정론 불변 | 개발 머신의 Claude Code 루프에만 설치, 산출물(CSS 곡선·코드)만 앱에 반영 |

---

## 3. Vinc 모션 토큰 제안 (motion.theme 패턴의 모노크롬 번역)

Motion UI의 "전환을 하드코딩하지 않는다" 규율을 그대로 가져오되, 마케팅용 5프리셋을 Ink & Paper에 맞는 4프리셋으로 줄인다. 파일은 `vinc.motion.ts`(또는 DTCG 토큰 SSoT의 motion 그룹) 하나.

```ts
// vinc.motion.ts (제안 초안)
export const motionTheme = {
  transitions: {
    micro:  { stiffness: 700, damping: 40 },  // 배지 pop, 저장 체크, pin 스냅
    ui:     { stiffness: 305, damping: 33 },  // 패널 등장, 카드, 툴팁 박스 (Motion UI 'ui' 그대로)
    gentle: { stiffness: 110, damping: 20 },  // Reader 승격(패널→전체폭), height morph
    glide:  { stiffness: 60,  damping: 18 },  // 3D 카메라 L0↔L1↔L2 (ambient보다 절제)
  },
  stagger: { tight: 0.04, base: 0.08 },        // 발췌 카드, 생키 노드
  travel:  { hover: 2, enter: 16, panel: 24 }, // px. 마케팅(4/24/48)보다 절반 수준으로 조용하게
  recede:  { node: 0.4, line: 0.12, text: 0.35 }, // D32 물러남 불투명도 (bklit 0.4/0.1 기반)
  reducedMotion: "calm",                       // prefers-reduced-motion 시 크로스페이드만
} as const
```

- **크롬 층(패널·칩·배지·툴팁)은 CSS `linear()` 스프링으로 런타임 0 구현**: 위 프리셋별 곡선을 빌드 타임에 1회 생성(AI Kit CSS 생성 또는 무료 도구/자체 스크립트)해 `--spring-ui: linear(0, 0.0121, ...)` 형태의 CSS 커스텀 프로퍼티로 심는다. 결정론(고정 문자열), 오프라인, 번들 부담 0.
- **콘텐츠·그래프 층(카메라, stagger, dashoffset, 값 구동)은 Motion 런타임**이 담당.
- 이 토큰들을 lime-design DTCG SSoT에 넣으면 `/ds-gate`류 감사에 "하드코딩된 transition 스캔" 규칙을 추가할 수 있다(색 하드코딩 스캔과 동일한 규율의 모션 버전).

---

## 4. 뷰·표면별 구체 적용 (영상에서 확인한 현행 기준)

### 4-1. Atlas 3D/2D 지도

영상 0~10초 구간: 구체 노드+라벨, 카메라 이동은 이미 부드럽지만(3d-force-graph 자체 트윈), 초점 전이(hover recede)와 패널 등장은 즉답형으로 보인다.

- **카메라 L0→L1→L2**: 현행 라이브러리 트윈을 유지하거나, `animate(camera.position, target, { type: "spring", ...glide })`로 교체해 줌 레벨 전환에 감속 곡선의 일관성을 부여. 주의: 3d-force-graph의 `cameraPosition()` 내장 트윈과 **이중 구동 금지** — 한쪽만 소유.
- **hover recede**: 노드/라벨/링크 불투명도를 `recede` 토큰 값으로 전이(120~180ms 상당의 micro~ui 스프링). 색조 추가 없이 명도만 변하므로 D32 그대로, 단지 "순간 점프"가 "물리적 물러남"이 된다.
- **상세 패널(영상 20초 프레임의 발췌 목록)**: 패널 자체는 CSS 스프링 slide+fade(travel.panel=24px), 발췌 카드는 `stagger(0.04)` 진입. 카드 수가 많으면 stagger 총합이 늘어지지 않게 상한(예: 첫 6장만 stagger, 이후 동시).

### 4-2. Flow (엣지 evidence 리서치와 결합)

- 1차 리서치의 "엣지 클릭 → 관계 패널(pin)"에 모션 문법을 입힌다: hover 미리보기는 recede 토큰으로 리니지만 남기고, 클릭 pin 시 패널이 ui 스프링으로 등장 + 해당 엣지 굵기를 micro 스프링으로 1단계 증폭(색 아님).
- 투명 히트 오버레이(기 리서치 채택)와 `hover()`/`press()` 제스처를 묶으면 포인터·터치 분기를 Motion이 흡수한다.

### 4-3. 타임라인

- 점 진입: 도메인 레인별 `stagger(tight)` 페이드인(데이터 로드 시 1회만, 뷰 재방문 시 반복 금지).
- **크로스헤어 차용**: bklit 툴팁의 수직 크로스헤어 + fade edges 패턴은 타임라인에 그대로 어울린다. 잉크색 1px 선, `indicatorFadeEdges` 개념으로 상하단 페이드, 커서 스프링 추종(damping 20). 하단 date pill 대신 축 위 날짜 라벨 강조(굵기)로 대체.
- 기간 브러시(향후): bklit Brush 유틸을 화면 수준 선례로 조사 목록에 추가.

### 4-4. 합계(Totals) 생키 — 가장 직접적인 이식처

- **진입 안무 이식**: 데이터 로드 시 노드 scaleY+opacity(0~600ms, stagger) → 링크 stroke-dashoffset 성장(200~1100ms). 단, 마케팅 문맥의 1.1초는 데이터 앱에선 길 수 있어 총합 700~800ms로 압축 권장. 이징은 bklit의 `cubic-bezier(0.85,0,0.15,1)` 또는 gentle 스프링.
- **hover 문법 수치 이식**: hover된 링크+양끝 노드 유지, 나머지 노드 0.4 / 링크 0.12. 이미 Vinc의 D32 문장과 동일한 행동이므로 수치만 확정하면 된다.
- **툴팁 → 드릴다운 연결**: hover 툴팁(구간 라벨 + 값 rows)은 bklit 해부도로, 클릭은 1차 리서치의 `/ledger-drilldown` 패널로. 툴팁 박스 배경은 Liquid Glass 재질(반투명+blur) 그대로.
- 링크 색은 그라디언트 금지, 단일 잉크 + 값 비례 불투명도.

### 4-5. 툴팁 전면 교체 (전 뷰 공통, 최우선)

- 현행 네이티브 `<title>` 툴팁은 1차 리서치에서 WCAG 1.4.13 부적합(dismissable/hoverable/persistent 불가)으로 이미 판정됨. bklit 툴팁의 해부(박스 + flip 감지 + 스프링 추종 damping)와 재질(반투명 blur)을 **자체 구현**으로 이식하는 것이 이 분석의 최단 실행 항목이다. 크로스헤어·dots는 뷰별 선택 요소.

### 4-6. Reader·편집기 (2차 리서치와 결합)

- 4-State 전이 표준화: Loading은 Motion UI Loaders의 "스켈레톤이 실제 콘텐츠로 매끄럽게 핸드오프" 패턴(모노크롬 shimmer는 불투명도 파동으로만) → Success 크로스페이드. Empty/Error는 travel 없는 순수 페이드.
- 저장 상태 칩(dirty→저장 중→저장됨): CSS 스프링 micro로 조용한 전이. 텍스트 점프 금지.
- 그래프 피드백 배지("개념 N개 갱신"): micro 스프링 pop(scale 0.9→1 + fade). 옆 그래프에서 새 MENTIONS가 밝아지는 것은 recede의 역방향(0.4→1.0) 전이로.
- Reader 패널→전체폭 승격: gentle 스프링 width/height morph(아코디언 height-morph 패턴의 확장). 동일 인스턴스 유지가 전제(2차 리서치의 Compartment 결론과 정합).

---

## 5. 성능·규율 (MotionScore의 원리를 무료로 가져오기)

AI Kit을 사지 않아도 MotionScore가 채점하는 기준 자체는 규율로 채택할 수 있다.

1. **컴포지터 속성만 애니메이션**: opacity, transform, filter. width/height/top/left 등 레이아웃 유발 속성 금지(Reader 승격 morph는 transform: scale 또는 clip-path 기반으로 설계 검토).
2. **렌더 루프 소유권 단일화**: sigma(WebGL)·three는 자기 루프가 프레임을 소유한다. Motion은 (a) DOM/SVG 오버레이(패널·툴팁·배지), (b) 카메라·불투명도 같은 "값"을 springValue/onUpdate로 공급하는 역할까지만. 같은 값을 두 시스템이 동시에 쓰지 않는다.
3. **진입 애니메이션은 데이터 이벤트에만**: 뷰 탭 전환마다 생키가 다시 자라나면 소음이다. 로드/갱신 시 1회, 전환 시엔 크로스페이드만.
4. **prefers-reduced-motion**: reducedMotion "calm" = 모든 travel·stagger·성장 안무 제거, 크로스페이드만 유지. 토큰 레벨에서 일괄 스위치.
5. 도입 후 `/motion` 스킬(구매 시)로 실행 중 로컬 앱을 프로파일해 렌더 비용 회귀를 CI 앞단에서 잡는 것도 가능(개발 루프 한정).

---

## 6. 도입 로드맵 (가치 × 비용)

| 단계 | 내용 | 런타임 추가 | 비용 | 가치 |
|---|---|---|---|---|
| 1 | **모션 토큰 + CSS `linear()` 스프링 세트** 정의(§3), **자체 툴팁**으로 `<title>` 전면 교체(§4-5), recede 수치 확정 | 0 KB | 낮음 | 매우 높음(접근성 부채 해소 + 전 뷰 일관성) |
| 2 | **Motion 런타임 도입**(npm, mini 중심 ≈2.3kb~): 패널·발췌 카드 stagger, 4-State 핸드오프, 저장/배지 micro | ~2.3kb+ | 낮음 | 높음 |
| 3 | **뷰 안무**: 합계 생키 진입(scaleY+dashoffset)과 hover 수치 이식, 타임라인 크로스헤어, Flow 엣지 패널 모션(1차 리서치 1단계와 병합) | 소폭 | 중간 | 높음 |
| 4 | **카메라 스프링**(L0↔L2 glide, 이중 구동 정리) + 뷰 전환 크로스페이드 통일 | 소폭 | 중간~높음 | 중간 |
| 병행 | **AI Kit을 Claude Code에 설치**(Motion+ 구매 시): 문서 컨텍스트·/motion 감사·CSS 스프링 생성이 개발 루프에 상주 | 앱 무관 | 구독 비용 | 개발 속도·품질 |

MVP 정의(1단계): 어떤 뷰에서든 hover recede가 동일 수치·동일 곡선으로 움직이고, 툴팁이 ESC로 닫히며(WCAG 1.4.13), 화면에 하드코딩된 transition이 0건.

---

## 7. 열린 질문 (제품 오너 결정)

> **결정 반영 (2026-07-25, 발주자):**
> - **Q1 Motion+ 구매 → 아니오.** AI Kit 4종 미도입. CSS `linear()` 곡선은 자체
>   스크립트로 빌드 타임 생성한다(무료 경로). 로드맵 "병행" 행은 폐기.
>   ⚠ 주의: `motion` npm 패키지 자체는 MIT 무료 — 구매 거부가 §6 2단계(런타임
>   도입) 거부는 아니다. 런타임은 Q2 로 별도 판단.
> - **Q3 recede 수치 → 확정(못 박음).** 노드 0.40 · 선 0.12 · 텍스트 0.35.
>   정본 등재 완료: [[vinc.d.atlas-visual-language]] §1.6 표 + DEC **D32-a**.
>   기존 D32 "비이웃 노드 12%" 를 개정하는 값임에 주의(노드는 덜 물러나고,
>   선이 대신 더 물러난다).
> - **Q2·Q4·Q5·Q6 미결** — 아래 원문 유지.
>
> **파생 신규 질문 (2026-07-25 발주자 제기): 관점 진입 구조.**
> "타임라인이든 무엇이든 특정 노드를 눌러서 들어가는 구조 말고, 처음부터 전부
> 한 번에 보여줄 수 없나?" → 사실관계·선택지는 **부록 3**. 요지: 사이드카는
> `GET /graph?owner=`(도메인·씨앗 없음) 로 **이미 전량을 돌려준다**(query.py
> `graph_plan` 의 `domain` 은 선택 인자, LIMIT 없음). 막고 있는 것은 백엔드가
> 아니라 클라이언트가 L0 에서 `/atlas`(주제 집계)만 부르는 것 + Timeline 렌더러의
> 고정 월-밴드 높이다.

1. **Motion+ 구매 여부**: AI Kit(문서 컨텍스트·MotionScore·스프링 생성·전환 에디터)은 유료. CSS 곡선만 필요하면 무료 대안(자체 스크립트·공개 생성기)으로 1단계는 가능. 2~4단계에서 400+ 예제 소스와 감사 스킬의 가치가 구독료를 넘는지.
2. **런타임 도입 범위**: mini(2.3kb, HTML/SVG)로 제한할지, 카메라·값 구동까지 포함한 하이브리드 전체를 쓸지. 4단계 착수 전 결정.
3. **recede 수치 확정**: bklit 기반 제안(0.4/0.12/0.35)을 그대로 D32 표준으로 못 박을지, 자체 대비 테스트 후 조정할지.
4. **진입 안무 정책**: 생키·타임라인 진입 애니메이션을 "최초 로드 1회"로 한정하는 원칙에 동의하는지, 갱신(원장 변화) 시에도 재생할지.
5. **reduced-motion 기본값**: OS 설정 추종만 할지, Settings에 Vinc 자체 토글(항상 calm)을 둘지.
6. **툴팁의 크로스헤어·dots 채택 범위**: 타임라인만 크로스헤어를 쓸지, 합계 hover에도 위치 지시선을 둘지.

---

## 부록: 출처 매핑

| 주장 | 출처 |
|---|---|
| mini animate 2.3kb, Three 객체 애니메이션, stagger, npm/CDN 설치 경로 | motion.dev/docs/quick-start |
| AI Kit 4구성(Context·MotionScore·CSS 생성·전환 에디터), Claude Code 지원, Motion+ 필요, `600ms linear(...)` 예 | motion.dev/docs/ai-kit |
| motion.theme.ts 프리셋 수치(snap 1218/70 등)·stagger·travel·reducedMotion, 섹션/컴포넌트 목록, shadcn 토큰 의존 | motion.dev/ui |
| 생키 진입 타임라인(0~600/200~1100ms), 이징 cubic-bezier(0.85,0,0.15,1), fadedOpacity 0.4/0.1, hover 유지 규칙, visx+motion 의존 | bklit.com/docs/components/sankey-chart |
| 툴팁 해부(크로스헤어 fade/dasharray·dots·박스 flip·date pill), damping 20 스프링 추종, matchCrosshair, CSS 변수 테마, 반투명+blur 박스 | bklit.com/docs/utility/tooltip |
| 현행 Vinc 뷰·패널 상태 | 업로드 영상(21s, 1600×1200) 프레임 분석 |

---

## 부록 2 — as-built 코드 대조 (파일링 검증, 2026-07-25)

> 파일링 시점에 본문의 코드베이스 전제를 라이브 소스와 전수 대조한 결과.
> 본문 원문은 무수정 보존이므로, 본문과 아래가 다르면 **아래(코드)가 사실**이다.
> 대조 대상: `Vinc/app` (v0.6.5 작업 트리).

| # | 본문 전제 | as-built 확인 | 판정 |
|---|---|---|---|
| V1 | 프레임워크 없는 수제 DOM/SVG + Vite, sigma·three·3d-force-graph 공존 | `app/package.json` — React/Tailwind 무, vite 6. 3d-force-graph 1.80 + three 0.185(`atlas3d.ts`) · sigma 3.0.3(`atlas.ts` 실사용) · 순수 DOM/SVG(`atlas-planar.ts`) | ✓ 일치 |
| V2 | CSP가 외부 CDN 차단 → npm+번들만 | `src-tauri/tauri.conf.json:25` `default-src 'self'; connect-src 'self' http://127.0.0.1:*; style-src 'self' 'unsafe-inline'`. 외부 script 원천 차단, 인라인 style 허용 → CSS `linear()` 커스텀 프로퍼티 경로도 통과 | ✓ 일치 |
| V3 | 현행 툴팁 = 네이티브 `<title>` | `atlas-planar.ts` 4곳(엣지 299 · 노드 309 · 타임라인 노드 453 · 생키 구간 533) + 크롬 `title` 속성(`main.ts:45` dom-row). WCAG 1.4.13 부채 실재 | ✓ 일치 |
| V4 | §4-1 "hover recede·패널 등장은 **즉답형**" | **불일치.** 평면: `.pl-fade { opacity: .12 }` + `transition: opacity .22s ease`(`app.css:396·401–402`, reduced-motion 게이트 하). 3D: 프레임 lerp `mat.opacity += d*0.25` → 타깃 `base*0.15`(`atlas3d.ts:338–360`). 이미 애니메이션됨 — 본 리서치의 실제 기여는 신설이 아니라 **수치·곡선의 토큰화·뷰 간 통일** | ⚠ 정정 |
| V5 | recede 수치 미확정 → 0.4/0.12/0.35 제안 | 현행 드리프트 실재: 평면 = 노드·선·레인·행 **무차별 0.12**(`atlas-planar.ts:350–362·475·566`) / 3D 노드 = base×**0.15** / 3D 엣지 = 색 스왑(`pal.link.fade`, 트윈 없음, `atlas3d.ts:331`). 노드 0.4 차등은 신규 제안이며 D32 개정 필요("이징 페이드"는 D32가 이미 요구, 수치만 공백) | ✓ 제안 유효 |
| V6 | 카메라 내장 트윈과 이중 구동 금지 | 실재 — L2 포커스 `cameraPosition(..., 700)` 700ms 내장 트윈(`atlas3d.ts:311–312`), L0/L1 은 즉시 배치(276–279). 경고 유효 | ✓ 일치 |
| V7 | "1차·2차 리서치" 참조 | 리포에 문서 없음 — **코드 as-built 로 존재**: 1차 = D45-V(흐름 엣지 관계 패널)·D45-V2 3·4단계(합계 구간·타임라인 월-밴드 드릴다운, `api.ts:181–221`·`copy.ts`), 2차 = D45-V2 2단계 Reader·D45-E 편집기(`codemirror-editor.ts`). §4-2 "투명 히트 오버레이"도 기구현(`atlas-planar.ts:290`, 14px 히트 스트로크) | ⚠ 참조는 코드로 추적 |
| V8 | MVP "하드코딩 transition 0건" | 현행 하드코딩: `app.css` transition 3건(.15s:224 · .35s:267 · .22s:402) + keyframes 2건(settings-pop .16s:420 · update-rise .18s:527). 이관 규모 소형 | ✓ 실측 |
| V9 | prefers-reduced-motion 토큰화 | 부분 기존재: `app.css:401`(평면 recede 게이트)·424(popover)·531(toast) — 개별 처리. 토큰 일괄 스위치가 신규 | ✓ 보강 제안 |
| V10 | D32 = "초점은 남고 나머지는 물러난다" | 정본 [[vinc.d.atlas-visual-language]] §1.6 D32 "MOC 구조 규율 — hover **이징** 페이드+엣지 후퇴"(발주자 승인 2026-07-10). 코드 주석도 동일 문구(`atlas-planar.ts:108`) | ✓ 일치 |
| V11 | "Ink & Paper 정서"·lime-design 편입 | 토큰 정본은 Signal([[vinc.d.graph-views]] v0.3 "색·치수는 Signal 토큰만"). Atlas 캔버스 모노크롬 잉크 규율은 유지(D37 층 분리). 모션 토큰 명명은 **Signal 계열**로. Vinc-design 허브에 motion/duration/easing/spring 토큰 전무 — 신규 영역 확인 | ⚠ 용어 정리 |

**요약**: 외부 자료 4종의 이식 판정·제약 필터는 전부 성립. 코드베이스 전제 11건 중
8건 일치, 3건 보정(V4 즉답형 서술 → 이미 애니메이션됨 / V7 선행 리서치 → 코드로 추적 /
V11 용어 → Signal). 로드맵 1단계의 실질은 "모션 신설"이 아니라 **산재한 5개 모션 값
(.15/.16/.18/.22/.35s + 3D lerp 0.25 + 카메라 700ms + recede 0.12/0.15)의 토큰 통일 +
툴팁 교체**다.

---

## 부록 3 — 관점 진입 구조: "처음부터 전부 보여줄 수 없나" (발주자 제기 2026-07-25)

> 모션과는 별개 주제이나, 같은 결정 라운드에서 나왔으므로 여기 사실관계를 남긴다.
> **결론 먼저: 백엔드는 이미 전량을 준다. 막고 있는 것은 클라이언트 한 줄과
> 타임라인 렌더러의 고정 밴드 높이뿐이다. 게다가 "L0 = 전체"는 이미 정본 문구다** —
> 즉 이것은 새 기능 제안이 아니라 **[[vinc.d.graph-views]] 사양 미준수 수리**에 가깝다.

### A3.1 현행 as-built — 층마다 무엇을 부르는가

| 스코프 | 호출 | 실제 모집단 |
|---|---|---|
| L0 | `GET /atlas` (`atlas.ts` showL0·showPlanar L0 분기) | **주제(도메인) 집계** — 개념 노드 아님 |
| L1 | `GET /graph?domain=` | 그 주제의 개념 전량 |
| L2 | `GET /graph?seed=&depth=2` | 씨앗 ego |

관점별 L0 거동(`atlas.ts` `showPlanar` L0 분기 1038–1070):

| 관점 | L0 화면 | 판정 |
|---|---|---|
| 3D·2D | 주제 구 N개 + 결합선 | D16 Domain Atlas — 의도된 설계 |
| 흐름 | 주제↔주제 `TIES` 그래프 | 개념 흐름이 아님(집계 엣지) |
| **타임라인** | **빈 상태 + 주제 칩**("주제에는 날짜가 없다") | **막다른 골목** — 발주자 지적의 진원 |
| 합계 | `/ledger?owner=` 전체 롤업 | **이미 전량** |

→ 같은 L0 인데 합계만 "전량", 나머지는 "주제층". 관점 간 모집단이 어긋나 있다.

### A3.2 정본은 이미 "전체"라고 쓰여 있다

- [[vinc.d.graph-views]] §3.2: "스코프 계승 … **L0=전체**, L1=그 주제, L2=그 노드 ego."
- 같은 문서 §2.3.1 표: L0 모집단 = **전체 개념**.
- 같은 문서 §2.2: 타임라인은 "**도메인/전역**" 관점이며 에피소드 스파인 + 도메인
  레인 구조를 규정 — 전역 타임라인이 사양에 이미 있다.

따라서 흐름·타임라인의 L0 를 전량 개념으로 바꾸는 것은 **DEC 신설이 아니라 §3.2·
§2.2 준수**다(합계는 이미 준수 중).

### A3.3 백엔드는 손댈 것이 없다 (실측)

| 근거 | 파일·행 |
|---|---|
| `graph_plan(*, owner, domain=None)` — `domain` 은 **선택 인자**, 없으면 절을 안 붙인다 | `Vinc/api/query.py:322–331` |
| `graph()` 독스트링 "**기본은 owner(±domain) 스코프 전체**(Atlas L1)" · seed 없으면 1048행에서 전량 플랜 실행 후 축 플랜 3종 합류 + `layout_positions` | `query.py:975–1058` |
| 전량 경로에 **LIMIT 없음** (개념 축·부착 축 3종 모두) | `query.py:322–354`·`600–692` |
| HTTP 도 `domain` 을 옵셔널로 넘긴다(`p.get("domain")`) | `Vinc/api/server.py:164–170` |
| 클라이언트 시그니처도 이미 옵셔널 | `Vinc/app/src/api.ts:167` |

즉 **`api.graph(cfg.owner, { layout })`** (도메인·씨앗 없이) 한 번이면 전량 그래프가
좌표까지 붙어서 온다. 사이드카 변경·마이그레이션·버전 스큐 전부 무관하다.

### A3.4 선택지 3안

| 안 | 내용 | D16/D43 영향 | 판정 |
|---|---|---|---|
| **A (권장)** | **평면 관점의 L0 = 전량 개념**. 흐름·타임라인이 `/graph?owner=` 를 부른다. 3D·2D L0 는 Domain Atlas 그대로 | 없음 — §3.2 준수 회복. 3D 시맨틱 줌(D16) 불변 | 사양 수리, DEC 불요(§3.2 명시 재확인만) |
| B | 스코프 축에 **`all` 4번째 층** 신설, 주제층과 병존 | `Level` 유니온·크럼·딥링크 확장 = D43 2축 모델 개정 | 중복 — L0 가 이미 "전체"라 층이 두 개가 된다 |
| C | 3D L0 도 전량 개념 | D16 시맨틱 줌 폐기 | 비권장 — 결합 행렬·Grid·Matrix 의 데이터 원천이 사라진다 |

### A3.5 A안의 실제 비용 (실측 기반)

1. **타임라인 밀도 붕괴 — 유일한 진짜 블로커.** `BAND_H = 96` 고정이고 한 달 안
   항목은 밴드 높이를 **균등 분할**한다(`atlas-planar.ts:402–414`, `yOf`). 한 달에
   40개면 간격 2.4px 인데 마크 반지름은 5.2px — 겹쳐서 못 읽는다. 전량을 켜려면
   **월별 가변 밴드 높이**(`max(96, count × 14)`)로 바꿔야 한다. 렌더러 국소 수정.
2. **가로 폭**: 레인 폭 `LANE_W = 190` × 도메인 수 → 도메인 12개면 2400px, 가로
   스크롤. 이미 스크롤 컨테이너(`.pl-wrap { overflow: auto }`)라 동작은 한다.
3. **SVG 노드 수**: 3D 는 WebGL 이지만 평면은 노드당 `<g>` DOM 이다. 수천 개면
   렌더·hover 위임 비용이 실재한다(위임은 컨테이너 3개뿐이라 리스너는 무관, 순수
   DOM 규모 문제). 상한 가드 또는 `Unlinked hide` 기본 적용 권장.
4. **초기 로드**: L0 진입이 곧 전량 조회 + 전량 레이아웃이 된다. 현재 L0(`/atlas`
   집계 2플랜)보다 무겁다. 로딩 문구는 이미 있다(`COPY.atlas.loading`).
5. **흐름 L0**: 방향 엣지(`PREREQUISITE_OF`·`DERIVES_FROM`·`INHERITS_FROM`)만
   그리므로 노드 수보다 관대하다. 다만 현행 L0 의 주제 `TIES` 그래프는 **잃는다** —
   `보기` 패널의 범위 행(Grid·Matrix)에 그 조망이 남아 있어 상실은 아니다.

### A3.6 권고 실행 순서

1. `showPlanar` L0 분기를 `api.graph(cfg.owner, { layout })` 로 교체(흐름·타임라인).
   합계는 무변경(이미 전량).
2. `mountTimeline` 월-밴드 높이를 그 달 항목 수에 비례하게(1번의 전제).
3. 노드 수 상한 가드 + 초과 시 강등 문구(정직한 축소 — 조용한 truncation 금지).
4. 흐름 L0 의 주제 조망이 필요하면 `보기` 패널 범위 행에서 Matrix 로 안내.

미결(발주자 확인 필요): **3D·2D L0 는 Domain Atlas 를 유지**한다는 전제가 맞는지.
"전부 한 번에"가 3D 지도에도 적용되길 원하면 C 안이 되고 D16 개정이 필요하다.

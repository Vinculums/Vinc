# Vinc 디자인 시스템 — Ink & Paper / Notation

> ⛔ **LEGACY-FROZEN (2026-07-18).** 이 Ink & Paper 시스템은 전면 오버홀로 대체 중이다.
> 새 디자인 시스템의 SSoT는 **`Vinc-design/`**(lime-design Design Hub)로 이관됐다.
> 이 파일과 `design-tokens.json`은 **동결** — Phase 5(앱 토큰 교체)까지 `app/src/tokens.css`
> 재현성 유지 목적으로만 보존하며, **수기 편집 금지**(74행 문법 파손 이력 있음, 2026-07-18 수리).
> 계획: `~/.claude/plans/vinc-zesty-bird.md` · 실행 노트: `Vinc-design/HUB-NOTES.md`.

> 프로즈 SSoT. 정본 수치는 `design-tokens.json`(토큰), 산출물은 `tokens.css`.
> 엔진 = `weg-9000-design`(순수 Python), 프로파일 = `vinc.profile.yml`. 데이터는 본 레포에 잔류.

## 1. 철학
레퍼런스 **Blue Note Re:imagined 2020** — 웜 크림 종이 + 밀도 높은 잉크, 색·그라데이션 0.
채도가 아니라 **대비**가 시스템 전체다. "뻔한 AI 인상"(선명 블루·과한 라운드·그라데이션)을 배격.

- **모노크롬 + 단일 포인트**: 잉크/종이 2톤을 기본으로, 포인트색(딥 잉크블루) 1개만 primary
  CTA·:focus 링에 쓴다. 상태(success/info/warning/danger)는 **색이 아니라 표기 아이콘 + 텍스트
  무게**로 변별한다. error 는 굵은 잉크 + `alert-triangle`.
- **순검정·순백 금지**: 텍스트는 웜 니어블랙 잉크, 배경은 웜 종이. 순검정 그림자·그라데이션·
  선명 블루(shadcn 기본) 금지 — 디자인 게이트 D-S02 로 기계 검증.

## 2. 토큰 (요약 — 정본은 design-tokens.json)
3계층 primitive→semantic→component, DTCG 참조형, HSL 채널, light-first(+ `[data-theme="dark"]`).
- 핵심 primitive: `ink`·`ink_soft`·`ink_mute` · `paper`·`paper_raised`·`sand`·`fog`·`hairline` ·
  `point`(단일 포인트) · `warn_bg`(배경 전용).
- 스케일: `radius`·`space`·`fs`(+`display`)·`lh`(+`tight`)·`shadow`(elev-0..3, 순검정 금지)·`font`.
- 타이포: heading = 타이트 볼드 그로테스크(`Space Grotesk`→`Satoshi` fallback), ui = `Satoshi`,
  mono = `JetBrains Mono`. 디스플레이 타이틀은 `--fs-display` + `--lh-tight`.

## 3. 접근성 (WCAG 2.2 AA)
본문 명암비 ≥4.5:1 검증 토큰만 텍스트로 사용. 실측·박제값은 `vinc.profile.yml` contrast_anchors.
전 페어 통과: 엔진 `validate_design.py --tokens design-tokens.json --profile vinc.profile.yml`.

## 4. 아이콘
`assets/icons/` — Notation 세트(24×24, currentColor, stroke 1.5). 육각=그래프 노드, 점 3단계=
레코드/상태, 셰브런=방향, 바=분포. 인덱스: `assets/icons/README.md`, 시각 시트: `gallery/index.html`.

## 5. 컴포넌트 (as-built)
`Button`·`Card`·`Badge`(틴트 ≤12%)·`Tab`·`Tooltip`·`ListRow`·`Progress`·`Skeleton`·`EmptyGuide`·
`ErrorBanner`·`StatusMark`(표기 아이콘) + 세그먼티드 컨트롤(`.seg`, D41) + Atlas 뷰
(`surfaces/atlas3d.ts` — `ForceGraph3D` 래퍼, D21 전면 3D; Compare 좌우 렌즈만 Sigma 2D).
카드는 '떠야 할 것'에만, 기본은 평문 섹션 + 헤어라인. 화면당 primary CTA 1개.

> **구현 형태 정정(2026-07-16):** 구 서술 "실제 `.tsx` 구현은 M5 후속 — P0–P2 선행 전제" 는
> 낡았다. 앱은 v0.1.6 로 출시됐고, 프론트는 **React 없이 바닐라 TypeScript**(`app/src/*.ts`,
> `.tsx` 0개 · React 의존성 0)로 구현돼 있다. 컴포넌트는 토큰 기반 DOM 렌더 함수다.

## 6. 파이프라인
```
design-tokens.json  ──(gen_tokens.py --profile vinc.profile.yml)──▶  tokens.css
        │                                                                  │
        └── validate_design.py (DTCG·a11y·governance) PASS       소비: Tauri UI / Figma 변수 미러
```
Figma(WEG's team → VINC 프로젝트)의 변수·스타일은 `design-tokens.json` 을 **미러**한다(SSoT 는 JSON).

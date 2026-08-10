<!-- vinc-export
doc_id: vinc.d.glass-language
title: Liquid Glass 재검토 (D37)
origin: Vinc-docs/vinc/D/vinc.d.glass-language.md@5090690
chunks: 2
verified: true
content_hash: ac779cd138459c4fa621eacea9190012a4e1c568262f348337d6b43e4be0725e
updated_at: 2026-08-01T16:20:32.569Z
-->

---
doc_id: vinc.d.glass-language
layer: D
domain: glass-language
title: Liquid Glass 재검토 — 크롬=글래스 / 콘텐츠=Ink & Paper 하이브리드 (D37)
status: draft
authority: SSoT
version: v0.1
owner: vinc
last_updated: 2026-07-11
inherits_from: [vinc.d.ux-research, vinc.d.atlas-visual-language, vinc.d.desktop-shell]
tags: [vinc, D, design, glass, liquid-glass, dec-candidate]
---

# Liquid Glass 재검토 (D37)

> 발주자 지시(2026-07-10): MVP 설치본과 화면에 Apple Liquid Glass 를 적용해
> 보고 싶다 — 디자인 시스템을 재고민하는 방향. 본 문서는 그 수용 범위를
> 판정하고 시안 규격을 정본화한다.

## 1. Liquid Glass 요지 (Apple, 2025 — iOS 26/macOS Tahoe 세대)

- 반투명 유리 재질: 배경을 **블러+굴절(lensing)** 로 비추고, 콘텐츠 색을
  흡수해 스스로 물든다(adaptive tint). 하이라이트 엣지가 유리 두께를 암시.
- 적용 위치가 핵심 규율: **콘텐츠가 아니라 그 위에 떠 있는 컨트롤·크롬
  레이어**(탭바·시트·팔레트·HUD)에만. 콘텐츠 평면은 불투명 유지.
- 공개 후 논쟁: 가독성·대비 저하 비판 → Apple 도 베타 중 불투명도를 상향
  조정. 접근성 설정(Reduce Transparency)의 불투명 폴백이 표준 요구.

## 2. 판정 — 전면 교체가 아니라 "층 분리 흡수"

Ink & Paper(모노크롬+단일 포인트색, Blue Note 계보)는 **콘텐츠 평면의
언어**로 유지한다. Liquid Glass 는 정확히 Apple 이 의도한 자리 — **떠 있는
크롬 층** — 에만 수용한다. 근거:

1. Atlas(3D 캔버스)가 주인공인 앱에서, 캔버스 위에 뜨는 패널이 유리면
   **가려진 그래프가 비쳐 보인다** — 공간감을 해치지 않고 오히려 강화.
2. 콘텐츠(목록·그래프 마크)까지 유리화하면 D20/D22 의 잉크 인코딩(농도=
   가중치 등)이 배경과 섞여 무너진다 — 가독성 논쟁의 재현.
3. 종이 질감 배경(paper) 위 유리는 Apple 순정(다색 배경)보다 대비 관리가
   쉽다 — 모노크롬이라 유리 틴트가 흡수할 색 자체가 절제돼 있음.

| 층 | 언어 | 대상 |
|---|---|---|
| 콘텐츠 평면 | **Ink & Paper 유지** | Atlas 마크(D20·D33·D35)·목록·본문·레일(상주 크롬) |
| **부유 크롬 층** | **Liquid Glass(신규)** | 커맨드 팔레트 · Share/Import 시트 · View 패널 · Atlas 상세 레일 · Settings 팝오버 · 치트시트 |

레일은 제외 — 상주(비부유) 크롬이라 유리화하면 "떠 있음"의 의미가 희석된다.

## 3. 시안 규격 (웹/Tauri 구현 제약 포함)

웹 렌더링에는 실제 굴절(lensing)이 없다 — `backdrop-filter` 로 근사한다:

```css
--glass-bg: hsl(var(--card) / .58);            /* 반투명 종이 */
--glass-blur: blur(14px) saturate(1.15);       /* 배경 흡수 */
--glass-edge: inset 0 1px 0 hsl(0 0% 100% / .35);  /* 상단 하이라이트 엣지 */
```

- 유리면 텍스트는 기존 잉크 그대로 — 배경 블러가 대비를 보증하는 수준까지만
  투명도를 허용(`.58` 미만 금지).
- **폴백 = 현행 카드**: `@supports not (backdrop-filter)` 및 사용자 감속·투명도
  축소 설정(`prefers-reduced-transparency`)에서는 불투명 카드로 강등 —
  Apple 의 Reduce Transparency 관례 승계. 즉 글래스는 **점진 향상**이다.
- 다크 모드: 동일 규격, `--card` 채널이 어두워지므로 자동 성립.
- 설치본(Tauri) 창 자체의 vibrancy(OS 유리)는 P4 셸 빌드에서 — 웹 레이어
  규격이 선행 정본.

## 4. 환경 대기(ambient) 배경 — 유리가 흡수할 것 (발주자 보강)

단색 종이 배경은 유리가 비출 것이 없다 — 배경에 **비의미 워시 3개**를 깐다:

- 좌상 밝은 종이빛(`--card`) · 우측 포인트색 속삭임(`--primary/.07`, **상한 .08**)
  · 하단 모래빛(`--muted`). 전부 토큰 참조 → 다크 모드 자동 성립.
- 포인트색 예약(§1.3)과의 관계: 환경 틴트는 의미 채널이 아니다 — 채도·불투명도를
  지각 임계 부근으로 묶어(상한 .08) "초점 표기"와 혼동될 수 없게 한다.
- **Atlas 캔버스 투명화**(양 테마): WebGL 캔버스 alpha — 대기가 그래프 뒤로
  비치고, 유리 패널이 그래프+대기를 함께 굴절. 다크는 반전 팔레트(D39 —
  밝은 잉크 마크)로 같은 구조가 성립(구 "종이 캔버스 예외"는 D39 로 폐지).

## 5. DEC

| # | 결정 | 상태 |
|---|---|---|
| D37 | **Liquid Glass 층 분리 수용** — 부유 크롬(팔레트·시트·패널·상세 레일·팝오버)만 글래스, 콘텐츠 평면·상주 레일은 Ink & Paper 유지. 불투명도 하한 .58, reduced-transparency/미지원 폴백 = 현행 카드. **보강(§4)**: 환경 대기 배경(워시 3, 포인트 틴트 상한 .08) + Atlas 캔버스 투명화(양 테마 — 다크는 D39 반전 팔레트) | 시안 채택 — 발주자 검토 대기 2026-07-10 |

## 부록 Z. 미분류 원문 사실
(없음)

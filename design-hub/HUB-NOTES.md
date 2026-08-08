# Vinc Design Hub — 구현 노트 (Phase 0 산출)

이 문서는 Vinc 디자인 시스템 오버홀의 실행 노트다. 승인된 계획은
`~/.claude/plans/vinc-zesty-bird.md`. 이 허브(`Vinc-design/`)가 3개 소비 리포
(`Vinc/app`, `Vinc-homepage/site`, 이메일)의 **단일 토큰 출처**다.

## 구조

```
Vinc-design/
├── design.profile.yml               # 인스턴스 프로파일 (name: vinc-design, dark_selector: [data-theme="dark"])
├── DESIGN/
│   ├── design-tokens.json           # DTCG SSoT — 현재 STARTER(플레이스홀더). Phase 3에서 Figma import로 교체
│   ├── tokens.css                   # 생성물 (gen_tokens) — 수기 편집 금지
│   └── DESIGN.md                    # 문서 스캐폴드
├── imports/
│   └── legacy-ink-paper.tokens.json # 구 Ink&Paper 토큰(수리본) 참조 사본 — Phase 3 매핑 참고용
├── tenants/desktop/design/          # 데스크탑 별도 테마 override (primitive leaf만)
├── dist/{app,site}/                 # 소비 리포용 후처리 산출물 (scope_tokens/gen_theme_ts/sync_out)
├── scripts/                         # 허브 전용 커스텀 툴 (Phase 3에서 작성)
└── reports/                         # ds-gate 산출
```

엔진(lime-design 플러그인)은 `${CLAUDE_PLUGIN_ROOT}/scripts/*.py`. Windows는 항상 `python -X utf8`.
Claude Code는 **허브 디렉토리에서 실행**해야 스킬의 상대경로가 맞는다.

## ⚠️ 엔진 계약 (Phase 0 실증 확인 — Phase 3 필독)

`_meta/design_rules/token_gen.py::render_css`를 직접 테스트해 확인한 제약:

1. **소비 계층 = primitive + semantic 2계층뿐.** `component` 계층은 **읽지 않는다**.
   구 Ink&Paper의 3계층 중 component 레이어와 semantic별 `$description` 규칙은
   엔진 SSoT가 아니라 **DESIGN.md 문서로만** 보존한다.
2. **semantic 값은 플랫 문자열**이어야 한다: `"background": "{paper}"`.
   구 파일처럼 DTCG 객체형(`"background": { "$value": "{paper}", "$type": ... }`)을 쓰면
   `gen_tokens`가 `AttributeError: 'dict' object has no attribute 'lstrip'`로 크래시한다.
   (실증: `imports/legacy-ink-paper.tokens.json`로 gen 시 exit 1.)
   → 새 SSoT의 semantic 레이어는 **스타터 스키마(플랫 문자열)**를 따른다.
   `$value/$type/$description` 주석은 **primitive 레이어**에만 단다.
3. **semantic 참조는 primitive로 1-step만.** `_resolve`는 `prim[ref_name(v)]`만 조회 →
   `"{semantic.primary}"`/`"{component.x}"` 체인은 KeyError. 모든 semantic은 primitive를 직접 가리킨다.
4. **생성 그룹은 고정**: `radius`(DEFAULT→`--radius`, 그 외→`--radius-{k}`), `space`→`--space-{k}`,
   `fs`→`--fs-{k}`, `lh`→`--lh-{k}`, `font`(heading/ui/mono)→`--font-{k}`, `shadow`→`--shadow-{k}`.
   그룹 내 **키는 자유** (예: `"fs": {"0": "...", "1": "..."}` → `--fs-0/--fs-1` 생성 가능).
5. **엔진이 못 내보내는 것** → 허브 커스텀 스크립트로 보강 필요:
   - `--icon-size` (임의 그룹 없음 — `space`에 `"icon"` 키로 넣어 `--space-icon`으로 우회하거나 후처리)
   - community `--dom-*` 카테고리 팔레트
   - Atlas WebGL scene 셰이드(heavy/mid/base/cross/fade 등)
   - 홈페이지 앱 목업 `--w-*`
   - 웹/데스크탑 스킨 스코핑(`:root[data-skin="web"]`) — `scope_tokens.py`
   - three.js/Sigma용 hex int 모듈 — `gen_theme_ts.py`
   - validate는 관대함: 객체형에도 PASS(참조 카운트가 속아 "unused primitive" INFO만 남음).
     따라서 **gen 통과 여부가 진짜 스키마 게이트**다.

## app.css 토큰 네이밍 불일치 (Phase 5 조정 대상)

- app.css는 `--fs-0`/`--fs-1`/`--lh`/`--icon-size`를 참조하나 현재 토큰은 `--fs-sm/base/lg`, `--lh-normal` 생성.
  → Phase 5에서 **SSoT의 fs/lh 키를 숫자(0,1,2…)로 정하고 `--lh`(=normal) 별칭**을 잡거나 app.css를 개명. 결정 필요.

## Phase 0 완료 상태

- ✅ 구 SSoT `Vinc-docs/DESIGN/design-tokens.json` 74행 문법 수리 → JSON 유효(light/dark 각 23키).
- ✅ Vinc-design 클론(빈 원격) → 허브 스캐폴드 → 스타터 gen/scaffold/validate 전부 exit 0 · PASS.
- ✅ 엔진 스키마 계약 실증 확인(위).
- 구 SSoT는 **legacy-frozen**: Phase 5(앱 토큰 교체)까지 `app/src/tokens.css` 재현성 유지 목적. 수기 편집 금지.

## Figma 파일 인벤토리 (WUXLeDDNBqih2KTZOz9wCX) — 2026-08-06 전면 재구축 후 신정본

(구 "발주자 고정 4페이지" 규칙은 2026-08-06 발주자가 기존 화면을 전부 삭제하고 전면 재구축을
지시하면서 폐기. 상세 기록: `concepts/figma-2026-08-rebuild.md`)

⚠️ `get_metadata`(nodeId 없이)는 페이지를 **하나만** 보고하는 버그가 있음 → 반드시 `use_figma`로
`figma.root.children` 전수 확인. (이 버그로 두 번 오판한 이력 있음.)

신정본 6페이지:

| # | 페이지 | id | 내용 |
|---|---|---|---|
| 0 | **Design System(Homepage)** | `0:1` | 정책 프레임(306:30) · 로고 섹션(157:15, Logo/Mark 91:164) · 아이콘 48종(226:5) · Home 컴포넌트 14종(231:5) |
| 1 | **Design System(App)** | `53:247` | App 컴포넌트 21종(243:27, Semantic Dark 고정) |
| 2 | **Homepage** | `40:2` | 실화면 기준 화면 44프레임(행 라벨 5행: MARKETING/ACCOUNT/AUTH·STATES/DOCS/LIGHT·MOBILE) |
| 3 | **App(Desktop)** | `40:3` | 앱 화면·오버레이 24프레임(Atlas/Search·Ask/Packs·Intake/Settings/Overlays/Web skin) |
| 4 | **Node** | `184:2076` | 그래프 예시 5종: 3D dark(299:30)·3D light(300:30)·2D(301:30)·Timeline(302:30)·Sankey(303:30) |
| 5 | **Email** | `219:5` | 이메일 DS(251:27) + 템플릿 14장(7종 × EN/KO) |

변수 컬렉션 7종(전부 신설, 구 3종 삭제): Primitive(221:5) · Semantic(222:5, Light/Dark, SSoT 시맨틱명 일치) ·
Graph(223:5, linkRamp+atlasScene) · Domain(223:27) · Window(223:35) · Email(223:45) · Scale(224:5, icon 18/24 신설).
폰트: **Pretendard 확정**(미설치로 Inter 임시 → 설치 후 텍스트 스타일 fontName 일괄 교체).
노드 ID 전체 맵: 세션 스크래치 `figma-node-map.md` → vinc 그래프 적재 예정.

## 다음(Opus 세션)

Phase 1(무드보드+시안)은 **사용자 레퍼런스 주입 대기**. 이후 Phase 2 Figma 라이브러리 →
Phase 3 `/ds-import`로 이 허브 SSoT 확정. Figma 변수명은 semantic 토큰명과 정확히 일치시킬 것
(import는 수동 매핑).

## Claude Design 으로 내보내기 (2026-08-08 확정)

```bash
python -X utf8 scripts/push_ssot.py     # 게이트 + 빌드 → dist/claude-design/tokens/ssot.css
# 그 다음 DesignSync 로 writes: ["tokens/ssot.css"] 만 계획해 업로드
```

**`/ds-push` 를 그대로 쓰지 않는다.** 그 스킬의 직접 경로는 스스로 밝히듯 tokens-only
허브용이라 `styles.css` 를 한 줄짜리(`@import "./tokens/tokens.css"`)로 갈아치운다.
Vinc 프로젝트는 tokens-only 가 아니다 — 컴포넌트 40여 개 · 템플릿 3 · ui_kits 2 ·
가이드라인 카드 23장이 있고, 그 `styles.css` 는 `tokens/` 6개를 물고 있다. 덮는 순간
6개가 import 클로저 밖으로 밀려나 **모든 컴포넌트·카드가 변수를 잃는다**.

lime-design 을 고쳐도 안 풀린다. `tokens/` 중 둘은 허브가 **원리상** 못 만든다:

| 파일 | 내용 | 허브 |
|---|---|---|
| `tokens/fonts.css` | `@font-face` 6개(자체 호스팅 Pretendard·JetBrains Mono) | ❌ `--font-*` 이름 스택만 |
| `tokens/base.css` | 리셋(box-sizing·body·::selection·:focus-visible·accent-color) | ❌ 토큰 생성기 소관 아님 |

그래서 **경로를 갈랐다**. 허브 출력은 `tokens/ssot.css` 한 곳으로만 가고, 프로젝트
`styles.css` 가 그것을 **마지막에** import 한다 — 두 곳이 같은 값을 정의하면 SSoT 가
이긴다. 푸시가 건드리는 파일은 하나뿐이라 카드·컴포넌트·템플릿은 사정권 밖이다.
`styles.css` 는 2026-08-08 에 한 번 고쳤고 그 뒤로는 안 건드린다.

⚠ finalize_plan 의 `writes` 에 `guidelines/`·`components/`·`templates/`·`assets/` 가
들어가면 그건 사고다. 목록은 항상 `["tokens/ssot.css"]` 하나여야 한다.

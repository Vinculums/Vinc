# Vinc 앱 (P3 UI) — Tauri 셸 · 8화면 · Sigma.js

오프라인 우선 데스크톱 데이터 마이닝 앱의 UI. **제어 = Tauri IPC · 데이터 = 사이드카
localhost HTTP**([[vinc.arch]]). 8화면([[vinc.d.index]]) + Sigma.js/WebGL 그래프 마이닝.

## 구조
```
src/
  App.tsx              부팅 게이트: 온보딩(첫 실행) ↔ 로그인(재실행) ↔ 셸
  components/
    Shell.tsx          사이드바 6뷰 · 크롬 · 설정 팝오버
    Icon.tsx           Notation 아이콘(원본 SVG 인라인 주입 — currentColor 상속)
    GraphView.tsx      Sigma.js/WebGL 래퍼(graphology + forceAtlas2)
    primitives.tsx     Button·Tab·ListRow·Progress·Skeleton·EmptyGuide·ErrorBanner·StatusMark
  screens/             8화면(각 4-State)
    Login  Onboarding  Ingest  Recall  Domains  Graph  Search  PackExport
  lib/
    ipc.ts             제어 평면 — Tauri #[command] 래퍼(+ 브라우저 데모 폴백)
    api.ts             데이터 평면 — 사이드카 HTTP 클라이언트(+ 데모 목 데이터)
    useAsync.ts        4-State 훅(idle/loading/success/error)
    app.tsx            앱 컨텍스트 · 내비 정의
  styles/              tokens.css(SSoT 미러) · global · components · app
src-tauri/             Rust 코어 — 사이드카 spawn · 로컬 로그인(F4) · 파일 다이얼로그
```

## 개발
```sh
npm install
npm run dev        # 브라우저 데모 모드(사이드카 없이 표면 검토 — 목 데이터)
npm run build      # tsc + vite 프로덕션 번들(dist/)
npm run tauri dev  # 전체 Tauri 셸(사이드카 실기동 — 아래 전제)
```

### 데모 모드 vs 실기동
- **브라우저(비-Tauri)**: `ipc.ts`/`api.ts` 가 데모로 강등 — 사이드카 없이 8화면·Sigma·
  4-State·디자인 표면을 검토할 수 있다(D-S01/D-S02 게이트용).
- **Tauri 셸**: Rust 코어가 `python -m api.cli serve` 로 사이드카를 띄우고
  `{host,port,token}` 을 프론트에 전달 → HTTP 는 `Authorization: Bearer <token>`.
  경로 오버라이드: `VINC_PYTHON`(기본 `python3`) · `VINC_ROOT`(기본 `..` = Vinc 레포 루트).

### 로컬 로그인(F4)
외부 인증 왕복이 없다. 패스프레이즈 검증은 IPC `session_login` → Rust →
`python -m api.cli verify`(SSoT: `api/auth.verify_passphrase`, argon2id). 온보딩은
`onboard` 로 자격 생성. argon2 는 옵션 extra(`vinc[server]`) — 미설치 시 검증만 불가.

## 디자인 토큰
`src/styles/tokens.css` 는 `Vinc-docs/DESIGN/design-tokens.json` 의 **미러**다(SSoT=JSON).
갱신: `weg-9000-design/scripts/gen_tokens.py` 재생성 후 복사. 색은 `hsl(var(--*))` 로만
소비(순검정/그라데이션/선명 블루 금지 — D-S02).

## 아이콘 & Figma
앱은 원본 SVG(`currentColor`)를 인라인 주입해 잉크 토큰을 상속한다. **Figma 는
currentColor 를 해소하지 못해 깨진다** — 별도 파이프라인/분석: `Vinc-docs/DESIGN/FIGMA-ICONS.md`.

## Tauri 번들 아이콘
`src-tauri/tauri.conf.json` 의 `bundle.icon` 은 `npm run tauri icon <src.png>` 로 생성한다
(레포에는 미포함 — 번들 시 1회).

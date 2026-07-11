# Vinc app — 5 surfaces + Settings (D15) · Atlas semantic zoom (D16)

프론트엔드 스캐폴드. 정본 설계는 `Vinc-docs vinc/D/*`(표면 설계서)와
`vinc.d.ux-research`(연구·DEC 근거), 카피는 D18 영어 카피 덱(`src/copy.ts`).

```
npm install
npm run dev        # http://localhost:5173 — 사이드카(api.cli serve) 필요
npm run build      # tsc --noEmit + vite build (게이트)
```

- **데이터 = localhost HTTP**: `src/api.ts` 가 사이드카(`127.0.0.1` + Bearer 토큰)만
  호출한다. 그래프/질의 로직은 UI 에 없다(레이아웃 좌표도 사이드카가 계산 — D17).
- **Settings(팝오버)** 에서 사이드카 주소·토큰·owner 스코프를 설정(개발 스캐폴드 편의).
- **Atlas**: L0(`GET /atlas`) → L1(`GET /graph?domain=`, 게이트웨이 = 결합 노드) →
  L2(`GET /graph?seed=&depth=`). hover = 이웃 유지·비이웃 페이드, double-click = refocus,
  breadcrumb 후퇴.
- **디자인 토큰**: `src/tokens.css` 는 `Vinc-docs/DESIGN/tokens.css`(엔진 산출물)의
  소비 사본 — 수기 수정 금지, 갱신은 gen_tokens.py 재실행 후 복사.
- **Tauri 셸(제어 = IPC)** 은 P4 패키징에서 감싼다 — 파일 다이얼로그·창 제어가
  그때 IPC 로 이동하고, 본 스캐폴드의 fetch 데이터플레인은 그대로 유지된다.

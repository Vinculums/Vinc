# Vinc Figma 노드 맵 (vinc MCP 적재 대기 기록)

파일: WUXLeDDNBqih2KTZOz9wCX
스토어 불통 구간 동안의 생성분 기록. 복구 후 vinc_ingest로 일괄 적재.

## 페이지 (2026-08-06)
| 페이지 | 노드 ID | 용도 |
|---|---|---|
| Design System(Homepage) | 0:1 | 공홈 디자인 시스템 (로고 섹션 157:15 보존) |
| Design System(App) | 53:247 | 앱 디자인 시스템 |
| Homepage | 40:2 | 공홈 실제 화면 |
| App(Desktop) | 40:3 | 앱 실제 화면 |
| Node | 184:2076 | 그래프 예시 4종 (3D/2D/Timeline/Sankey) |
| Email | 219:5 | 이메일 DS + 템플릿 14장 (신설) |

## 기존 보존 노드
- Logo / Mark 컴포넌트: 91:164 (정본, 두획 V)
- Logo 섹션: 157:15 (01 · Logo)
- Logo dark sample: 91:169

## 기존 변수 컬렉션 (Phase 1 재편 대상)
- `color` VariableCollectionId:1:2 (18개, Light/Dark) - 구세대, 정리 대상
- `App · Deep Signal (draft)` VariableCollectionId:123:168 (10개, Dark)
- `Home · Signal (draft)` VariableCollectionId:136:167 (11개, Light/Dark)

## 폰트
- ~~Pretendard: Figma 목록에 없음 (2026-08-06 확인) - 사용자 설치 필요~~
- ~~텍스트 스타일은 Inter 임시 생성. Pretendard 설치 후 스타일 fontName 일괄 교체 스크립트 실행~~
- **해소 (2026-08-08 실측).** Pretendard 가 설치돼 텍스트 스타일이 실제로 그것을 쓴다.
  라이브 스타일 체계도 아래 기록과 다르다 — Display/Bold 44/52 · H1/Bold 32/38 ·
  H2/Bold 24/30 · Lead 17/26 · Body 15/23 · MD 14/21 · SM 13/20 · Caption 11/16.
  근거: `imports/figma-variables-2026-08-08.json`

## Phase 1 산출 (2026-08-06)
### 변수 컬렉션 (구 3개 삭제: color 1:2, App Deep Signal 123:168, Home Signal 136:167)
- `Primitive` VariableCollectionId:221:5 (30색, 단일모드, 픽커 비노출)
- `Semantic` VariableCollectionId:222:5 (24롤, Light 222:0 / Dark 222:1, 프리미티브 별칭)
- `Graph` VariableCollectionId:223:5 (21개: link/5, label/3, node/3, scene fill·stroke 6 + glowOpacity 4)
- `Domain` VariableCollectionId:223:27 (dom-1~6, dom-other, Light/Dark)
- `Window` VariableCollectionId:223:35 (w-* 9색, 다크 상수)
- `Email` VariableCollectionId:223:45 (email/* 9색, 다크 상수)
- `Scale` VariableCollectionId:224:5 (radius 4, space 7, fs 5, icon 2 신설, layout 2)

### 시맨틱 변수 ID (바인딩용)
background 222:6 · foreground 222:7 · card 222:8 · card-foreground 222:9 · muted 222:10 · muted-foreground 222:11 · caption-foreground 222:12 · border 222:13 · input 222:14 · ring 222:15 · primary 222:16 · primary-foreground 222:17 · accent 222:18 · accent-foreground 222:19 · link 222:20 · info 222:21 · info-strong 222:22 · info-foreground 222:23 · success 222:24 · success-foreground 222:25 · warning 222:26 · warning-foreground 222:27 · danger 222:28 · danger-foreground 222:29 (전부 VariableID: 접두)

### 이펙트 스타일
shadow/soft·float·elev-1~3, glass/panel(bg blur14), glow/focus·near·mid·far(그래프 노드 전용)

### 텍스트 스타일 (Inter 임시 → Pretendard 교체 대기)
Display 56/64 Bold · XL 36/41 Bold · LG/Semi Bold 17/26 · Base/Regular·Medium 15/23 · Base/Prose 15/26 · SM/Regular·Medium 13/20 · Mono/SM 13/20(JetBrains Mono) · Email/H1 23/32 Semi Bold

### 신설 정책 (Phase 8에서 문서화)
1. font-weight 스케일: body 400 / label·강조 500 / 헤딩·버튼·액티브 600 / display·브랜드 700 (GNB 680 → 700 매핑)
2. icon 토큰: icon/app 18px, icon/base 24px
3. radius 정본 = SSoT (랜딩 인라인 드리프트 폐기)
4. fs-xl/display는 1440px 해석값(36/56)으로 고정 표기

## Phase 2 산출 (2026-08-06)
- 아이콘 섹션 `02 · Icons`: 226:5 (페이지 0:1, x80 y320)
- 아이콘 45종 전부 Icon / <name> 컴포넌트, foreground 변수 바인딩:
  alert 226:10 · arc 226:14 · bar-group 226:20 · caret 226:23 · check 226:26 · chevron-left 226:29 · chevron-right 226:32 · comet 226:37 · dash-ramp 226:42 · decide 226:46 · disc 226:49 · domains 226:54 · dot-grid 227:15 · dot 227:18 · download 227:23 · dumbbell 227:28 · edit 227:31 · ellipse 227:34 · graph 227:40 · half-disc 227:44 · hourglass 227:48 · ingest 227:54 · lock 227:58 · login 227:63 · onboarding 228:9 · pack-export 228:15 · pause 228:19 · pendulum 228:24 · pill-v 228:27 · pill 228:30 · pin 228:34 · play 228:37 · recall 228:48 · ring 228:51 · search 228:57 · settings 228:65 · sparkle 229:7 · stack 229:12 · sync 229:18 · tag 229:22 · undo 229:26 · upload 229:31 · x 229:34 · pack-in 229:39 · share-nodes 229:45
- 로고 규정 카드: 230:5 (01 · Logo 섹션 내, 섹션 1100 확폭)
- 추가 UI 아이콘: globe 235:8 · sun 235:12 · moon 235:15 (GNB용)
- 로고 91:164 내부 벡터 SCALE constraints 적용(축소 대응)

## Phase 3 산출: Home 세트 (섹션 03 · Components (Home) = 231:5, 페이지 0:1)
- Home / Button 231:10 (Kind=Primary 231:6/Quiet 231:8, label#231:0)
- Home / Badge 232:14 (Tone=Info/Success/Ring, label)
- Home / Chip 232:21 (Status=Done/Now/Neutral, label)
- Home / Toggle 234:9 (State=On/Off)
- Home / Input 234:14 (State=Default/Focus, text)
- Home / Seg 234:25 (Active=A/B, labelA/labelB)
- Home / GNB 237:5 (firstLink prop, bg .82+blur, 링크 6, globe+테마 세그)
- Home / Footer 237:32 (© 2026 Vinc · MIT License + 링크 5)
- Home / LangModal 238:16 (English 활성+체크, 한국어)
- Home / Banner 238:30 (Kind=Notice/Trial, text)
- Home / Card 240:17 (피처 카드, title/desc, 아이콘 수동 스왑)
- Home / CommunityCard 240:25 (title/author/stats + 도메인 바)
- Home / AuthCard 241:37 (Tone=Default/Danger, title/sub, slot)
- Home / ModalCard 241:38 (title, slot, actions)

## Phase 3 산출: App 세트 (섹션 03 · Components (App) = 243:27, 페이지 53:247, Semantic Dark 모드 고정)
- App / Button 243:36 (Kind=Primary/Ghost/Quiet/Danger, label)
- App / Chip 243:41 (State=Default/Active, label)
- App / NavItem 244:41 (State=Default/Active, label, 아이콘 스왑)
- App / DomainRow 244:52 (State, name/count)
- App / DocRow 244:62 (Ink=1/2/3, title)
- App / ListRow 244:71 (State=Default/Hover, mark/title/meta)
- App / StateMark 245:47 (State=Full/Half/Empty ● ◐ ○)
- App / ProgressBar 245:48 (fill 폭 오버라이드)
- App / ErrorBanner 245:50 (text, △ 접두)
- App / EmptyState 245:52 (title/desc + cta-slot)
- App / Tooltip 245:66 (text, 글래스)
- App / Sheet 246:83 (Size=Action 480x380/Write 832x560/Browse 980x620, title/subtitle, head/body/foot)
- App / ViewSeg 246:120 (Active=3D/2D/Timeline/Sankey)
- App / Slider 246:121
- App / MenuItem 246:131 (Tone=Default/Hover/Danger, label)
- App / GraphNode 247:61 (Tier=Focus/Near/Mid/Far, glow 이펙트, 유일 글로우)
- App / PackCard 247:70 (State=Default/Highlight, name/counts/meta)
- App / FloatCard 247:71 (title, slot, 좌 2px accent바, 글래스)
- App / UpdateToast 247:89 (State=Available/Downloading/Installing/Failed)
- App / Titlebar 250:53 (1280x34)
- App / Topbar 250:66 (1280x48, 좌 액션·중앙 검색·우 슬롯)

## Phase 3 산출: Email 세트 (섹션 00 · Email Design System = 251:27, 페이지 219:5)
- Email / BrandRow 251:28 (V #C7CAF0 + 워드마크)
- Email / CTA 251:34 (label)
- Email / Chip 251:36 (text)
- Email / Frame 251:38 (h1/body/fine + slot, 640 바디·480 카드·다크 고정)

## Phase 4 산출: Homepage 화면 (페이지 40:2, 전부 다크 모드 고정, x 피치 1560)
- Home · Landing / dark 252:27 (x0: GNB+히어로+vshell목업(그래프 placeholder)+how+features+packs+vows+fin(항상 인디고)+뉴스레터 2상태+푸터)
- Home · Download / dark 255:131 (x1560)
- Home · Pricing / dark 256:174 (x3120: trial배너+연월세그+3티어+FAQ)
- Home · Community / dark 258:231 (x4680) + Pack modal states 258:353 (y2900, 4단계)
- Home · Release / dark 260:337 (x6240: v0.7.4+로드맵 칩)
- Home · Privacy / dark 260:418 (x7800: EN 6섹션+KO 병기 헤더)
- Home · Auth · Login & Welcome 261:416 (y4700: LOGIN form/sent + WELCOME loading/form/available)
- Home · Auth · Verify states 262:438 (y5600: 6상태)
- Home · Auth · Subscribe & Unsubscribe 263:462 (y6500: 5+5상태)
- Home · Account / dark 264:502 (x9360: acard 7종+키 1회 노출)
- Home · Account · Team / dark 266:550 (x10920: accept/create/teamView/invite+candlist/danger)
- Home · Account · Delete states 266:651 (y7500: 5상태)
- Home · Connect & Connect-app states 267:615 (y8400: 5+6상태, kv+스푸핑 경고)
- Home · 404 / dark 268:657 (x12480, 신설)
- Home · Service states 268:709 (429/unavailable/signed-out, 신설)
- Home · Mobile GNB drawer 268:746 (390px, 신설)
- Docs 15종 (y9500, x 0~21840 피치 1560): Overview 269:714 · Quickstart 269:807 · Use-cases 269:903 · Import 270:825 · Atlas 270:921 · Search 270:1017 · Packs 271:936 · Shortcuts 271:1032(단축키 표) · MCP Setup 271:1149(호스트 카드) · MCP Tools 273:1047 · CLI 273:1139 · GRAPH.md What 273:1233 · Generate 274:1158 · Spec 274:1250 · Validate 274:1338
- ~~라이트 대표 4~~ (구 랜딩·다운로드 라이트는 재구축과 함께 삭제)

## 실화면 기준 재구축 (2026-08-06, Playwright vincs.io 분석 후)
- 삭제: 구 Landing dark/light, Download dark/light, Pricing, Community
- **Home · Landing / dark 278:1051** (신): 2톤 헤드라인(둘째 줄 link색)+로고 위, 궤도 비주얼(orbit 278:1099: 중심 카드 원+로고+방사선+위성 점+아이콘 타일 4), 앱 목업 vshell 279:1103(미니 그래프 장식+아이콘 레일+Hybrid search 카드+2D/3D 필+도메인 칩), Three clicks(01~03 번호 카드), No setup maze(아이콘 타일 카드 6), packs(OFFICIAL PACK 키커), vows 단일 대형 카드(원형 아이콘 2열), fin 중앙정렬(인디고 밴드 폐기: 로고+헤딩+Connect it 링크+Download 버튼), 뉴스레터 중앙
- **Home · Download / dark 282:1189** (신): 중앙 히어로(로고 64+버전 라인+감지 문구), notice 배너(info 원 아이콘), 플랫폼 카드 풀폭 포맷 버튼(.dmg/.exe 인디고, .deb quiet), Windows 카드 ring 하이라이트
- **Home · Pricing / dark 283:1241** (신): 중앙 히어로+세그, 가격 ₩0/$79/$120(실사이트 표기), Popular 인라인 필, 풀폭 CTA, FAQ
- **Home / CommunityCard 컴포넌트 v2 284:27** (구 240:25 삭제): 그래프 썸네일+OFFICIAL 필+도메인 키커(◆)+태그 칩 4+imports/Import 버튼. props: title#284:0 author#284:1 stats#284:2 domain#284:3 imports#284:4
- **Home · Community / dark 285:1296** (신): 2톤 헤딩, Share your own pack 버튼, 풀폭 검색+Recent 정렬, 카드 2x2
- Docs 16프레임(15+라이트): kicker(섹션명 caps link색)+구분선 패치 완료
- 라이트 신규: Landing light 286:1581 · Download light 286:1800

## Homepage 페이지(40:2) 배치 정본 (행 라벨 row-label 5종)
- Row1 MARKETING y0: Landing(0) Download(1560) Pricing(3120) Community(4680) Release(6240) Privacy(7800) 404(9360)
- Row2 ACCOUNT y5400: Account(0) Team(1560) Account-light(3120)
- Row3 AUTH·STATES y9000~: Login&Welcome 9000 · Verify 9950 · Sub/Unsub 10900 · Delete 12400 · Connect 13350 · Service 15000 · PackModal 15700
- Row4 DOCS y17000: 15+라이트, x 피치 1560
- Row5 LIGHT·MOBILE y19600: Landing-light(0) Download-light(1560) MobileGNB(3120)

## Phase 5 산출: App 화면 (페이지 40:3 App(Desktop), 다크 고정, 1280x800)
### Row1 Atlas y0
- Atlas default 287:30 (셸 정본: 타이틀바+탑바(렌즈·뷰세그)+LNB 232+캔버스 placeholder+메트릭 바+라쏘 힌트)
- Atlas empty 289:98 (x1560) · lens panel(Options) 289:221 (x3120) · coachmark+lasso 289:373 (x4680)
- node detail+related 290:311 (x6240) · edge detail 290:412 (x7800) · reader 290:468 (x9360) · Web skin 297:2681 (x10920)
### Row2 Search·Ask y1000
- results 291:494 · no results+reindex 291:617 (x1560) · error+browse 291:728 (x3120) · Ask answer+disambiguation 291:849 (x4680)
### Row3 Packs·Intake y2000
- Packs(드롭존 42:3 포함) 292:715 · Intake states(Ingesting 42:5/Success 42:7/Error) 292:859 (x1560)
### Row4 Settings y3200
- Account tab(dim+모달) 294:803 · Connections/Data/Advanced 294:898 (x1560) · device flow 5상태 294:986 (x4200)
### Row5~6 Overlays y4400/5600/6800
- Overlays1(Share/Destination/Import pack/Confirm/Prompt) 295:881
- Overlays2(Tools Create/GRAPH.md/Doc editor conflict) 296:891
- Overlays3(팔레트/컨텍스트 메뉴/툴팁/토스트 4종/Ask history/Cheatsheet) 297:904

## Phase 6 산출: Graph 예시 (페이지 184:2076 Node, 1200x800)
- Graph · 3D · dark 299:30 (x0: 사선 카메라 투영, 도메인 구+토러스 링, 글로우 focus/near, 육각 레코드, 다이아 브리지, 깊이 페이드 엣지, 허브 라벨 4)
- Graph · 3D · light 300:30 (x1300: 매트 잉크 D77, 포커스 indigo)
- Graph · 2D · dark 301:30 (x2600: sigma 팬 레이아웃, 4티어 엣지+화살촉, 허브 배지, 미니맵+뷰포트)
- Graph · Timeline · dark 302:30 (x3900: 루트/넥/브랜치 3, 원=개념·육각=문서, 리더 라벨, 인과 베지어(역행 점선), 월 눈금, 캡션 필)
- Graph · Sankey · dark 303:30 (x5200: 3열 alluvial, 경로 보존 포커스 리본(peri), 잉크 .82 스트라타+bg 헤어라인, 라벨 플레이트, 2 more topics)

## 2차 수정 (2026-08-06, 그래프 비주얼 정밀 재현 + 배경 + 1프레임 전환)
- **Home / PageBackground 313:30** (0:1 섹션 231:5): 오로라 4블롭(코드 radial-gradient 값) + 하프톤 도트(pitch 19, 단일 벡터 ROUND-cap 점 트릭, ring α.10) + 커서 부스트 스팟(rest 50%/42%, 큰 도트+방사 마스크) + 스포트라이트. NOISE 이펙트는 플러그인 API 검증 거부로 생략(수동 추가 가능). PATTERN 페인트도 이 파일 런타임 미지원 확인
  - 마케팅 7프레임(랜딩/다운로드/프라이싱/커뮤니티/릴리스/프라이버시/404)에 absolute 인스턴스 삽입
- **궤도 재구축 315:1753**: index.html 좌표 1:1(400vb x1.12) — 가이드 원 r118/168 dash 2 8, 스포크 10(짝 ring/.38·홀 mutedFg/.32), 칩 4+recall 5번째, 점 5, 신호 3, 허브글로우 r44+허브 r30+로고 32(전부 ring 틴트)
- **vshell 목업 재구축 (279:1103 내부)**: 코드 좌표 1:1(720x400 → x1.164 슬라이스) — 엣지 12+hl 3, 노드 plain 9/pt 3/sel r13+halo r20 dash 4 5, 신호 2, 레일 6아이콘(graph active=point 18%), crumb+2D/3D 칩(캔버스 내부), 레전드 점 3색, detail 카드(점+라인 88/64/76%)
- **커뮤니티 썸네일 갱신** (284:28): 22노드 22엣지 넓은 constellation
- **Locale 컬렉션 VariableCollectionId:318:30** (EN 318:0 / KO 318:1): string 변수 70종(landing/* mock/* gnb/*) — 랜딩 68 텍스트 + GNB 메인 5링크 바인딩. **한 프레임에서 언어 전환 검증 완료**(KO 스크린샷). 한계: 컴포넌트 TEXT 프로퍼티 기반 라벨(버튼 CTA)은 변수 바인딩 불가 → EN 유지
- **다크/라이트도 동일 원리**: 프레임 선택 → Semantic 모드 전환. 라이트 복제 4프레임 삭제(Landing/Download light, Docs Overview light, Account light)
- 랜딩의 Home/Card 인스턴스(steps/features)는 바인딩 위해 detach됨
- Pretendard: 여전히 미노출(Figma 재시작 필요) → 교체 절차 scratchpad/pretendard-swap-plan.md
